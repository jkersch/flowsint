"""Contract for POST /sketches/{id}/nodes/search and GET /sketches/{id}/stats."""

import uuid
from unittest.mock import MagicMock

import pytest

from flowsint_core.core.auth import create_access_token
from flowsint_core.core.graph import GraphSerializer, GraphService
from flowsint_core.core.models import (
    Investigation,
    InvestigationUserRole,
    Profile,
    Sketch,
)
from flowsint_core.core.services import sketch_service as sketch_service_module
from flowsint_core.core.types import Role
from flowsint_types import Domain, PhishingPage


def _seed_user(db_session, roles=(Role.OWNER,)):
    user = Profile(
        id=uuid.uuid4(), email=f"{uuid.uuid4()}@example.com", hashed_password="x"
    )
    investigation = Investigation(id=uuid.uuid4(), name="Case", owner_id=user.id)
    sketch = Sketch(
        id=uuid.uuid4(),
        title="Board",
        description="",
        owner_id=user.id,
        investigation_id=investigation.id,
    )
    db_session.add_all([user, investigation, sketch])
    if roles:
        db_session.add(
            InvestigationUserRole(
                user_id=user.id, investigation_id=investigation.id, roles=list(roles)
            )
        )
    db_session.commit()
    headers = {"Authorization": f"Bearer {create_access_token({'sub': user.email})}"}
    return headers, str(sketch.id)


def _rows(*entities):
    return [
        {"id": f"4:x:{i}", "data": data}
        for i, data in enumerate(GraphSerializer.serialize_flowsint_types(entities))
    ]


@pytest.fixture
def repo(monkeypatch):
    """Graph repository behind the service; Neo4j is never touched."""
    repo = MagicMock()
    repo.search_nodes.return_value = {"nodes": [], "next_cursor": None}
    repo.get_sketch_stats.return_value = {
        "nodes": 0,
        "edges": 0,
        "by_type": {},
        "by_relationship": {},
        "top_nodes": [],
    }
    monkeypatch.setattr(
        sketch_service_module,
        "create_graph_service",
        lambda sketch_id, type_resolver=None, **kw: GraphService(
            sketch_id=sketch_id, repository=repo, type_resolver=type_resolver
        ),
    )
    return repo


def _search(client, sketch_id, headers, **body):
    return client.post(
        f"/api/sketches/{sketch_id}/nodes/search", json=body, headers=headers
    )


class TestSearch:
    def test_returns_nodes_and_cursor(self, client, db_session, repo):
        headers, sketch_id = _seed_user(db_session)
        repo.search_nodes.return_value = {
            "nodes": _rows(
                PhishingPage(url="https://evil.example/login", status="VERIFIED")
            ),
            "next_cursor": "4:x:0",
        }

        res = _search(
            client,
            sketch_id,
            headers,
            types=["PhishingPage"],
            query="evil",
            filters={"status": "VERIFIED"},
            limit=1,
        )

        assert res.status_code == 200
        body = res.json()
        assert body["next_cursor"] == "4:x:0"
        node = body["nodes"][0]
        assert node["id"] == "4:x:0"
        assert node["nodeType"] == "phishingpage"
        assert node["nodeLabel"] == "https://evil.example/login"
        assert node["nodeProperties"]["status"] == "VERIFIED"
        repo.search_nodes.assert_called_once_with(
            sketch_id=sketch_id,
            types=["phishingpage"],
            query="evil",
            filters={"status": "VERIFIED"},
            limit=1,
            cursor=None,
        )

    def test_empty_body_searches_everything(self, client, db_session, repo):
        headers, sketch_id = _seed_user(db_session)
        repo.search_nodes.return_value = {
            "nodes": _rows(Domain(domain="a.example")),
            "next_cursor": None,
        }
        res = _search(client, sketch_id, headers)
        assert res.status_code == 200
        assert [n["nodeLabel"] for n in res.json()["nodes"]] == ["a.example"]
        assert repo.search_nodes.call_args.kwargs["limit"] == 50

    def test_viewer_may_search(self, client, db_session, repo):
        headers, sketch_id = _seed_user(db_session, roles=(Role.VIEWER,))
        assert _search(client, sketch_id, headers).status_code == 200

    def test_without_role_is_forbidden(self, client, db_session, repo):
        headers, sketch_id = _seed_user(db_session, roles=())
        assert _search(client, sketch_id, headers).status_code == 403
        repo.search_nodes.assert_not_called()

    def test_other_users_sketch_is_forbidden(self, client, db_session, repo):
        headers, _ = _seed_user(db_session)
        _, foreign_sketch = _seed_user(db_session)
        assert _search(client, foreign_sketch, headers).status_code == 403

    def test_unknown_sketch_is_404(self, client, db_session, repo):
        headers, _ = _seed_user(db_session)
        assert _search(client, uuid.uuid4(), headers).status_code == 404

    def test_malformed_sketch_id_is_422(self, client, db_session, repo):
        headers, _ = _seed_user(db_session)
        assert _search(client, "not-a-uuid", headers).status_code == 422

    def test_requires_authentication(self, client, db_session, repo):
        _, sketch_id = _seed_user(db_session)
        assert _search(client, sketch_id, {}).status_code == 401

    @pytest.mark.parametrize("limit", [0, 501])
    def test_limit_bounds(self, client, db_session, repo, limit):
        headers, sketch_id = _seed_user(db_session)
        assert _search(client, sketch_id, headers, limit=limit).status_code == 422

    @pytest.mark.parametrize(
        "body, detail",
        [
            ({"types": ["NoSuchType"]}, "Unknown type(s): NoSuchType"),
            ({"filters": {"status": ["a"]}}, "must be a string, number or boolean"),
            ({"filters": {"a b": "x"}}, "Invalid filter field"),
        ],
    )
    def test_invalid_criteria_are_400(self, client, db_session, repo, body, detail):
        headers, sketch_id = _seed_user(db_session)
        repo.search_nodes.side_effect = ValueError("Invalid filter field: 'a b'")
        res = _search(client, sketch_id, headers, **body)
        assert res.status_code == 400
        assert detail in res.json()["detail"]

    def test_backend_failure_is_500(self, client, db_session, repo):
        headers, sketch_id = _seed_user(db_session)
        repo.search_nodes.side_effect = RuntimeError("neo4j down")
        res = _search(client, sketch_id, headers)
        assert res.status_code == 500
        assert res.json()["detail"] == "Failed to search nodes"


class TestStats:
    def test_returns_stats(self, client, db_session, repo):
        headers, sketch_id = _seed_user(db_session, roles=(Role.VIEWER,))
        repo.get_sketch_stats.return_value = {
            "nodes": 3,
            "edges": 2,
            "by_type": {"domain": 2, "ip": 1},
            "by_relationship": {"RESOLVES_TO": 2},
            "top_nodes": [
                {"id": "4:x:2", "type": "ip", "label": "1.2.3.4", "degree": 2}
            ],
        }
        res = client.get(f"/api/sketches/{sketch_id}/stats", headers=headers)
        assert res.status_code == 200
        assert res.json()["by_type"] == {"domain": 2, "ip": 1}
        repo.get_sketch_stats.assert_called_once_with(sketch_id, top_n=10)

    def test_without_role_is_forbidden(self, client, db_session, repo):
        headers, sketch_id = _seed_user(db_session, roles=())
        res = client.get(f"/api/sketches/{sketch_id}/stats", headers=headers)
        assert res.status_code == 403

    def test_unknown_sketch_is_404(self, client, db_session, repo):
        headers, _ = _seed_user(db_session)
        res = client.get(f"/api/sketches/{uuid.uuid4()}/stats", headers=headers)
        assert res.status_code == 404

    def test_backend_failure_is_500(self, client, db_session, repo):
        headers, sketch_id = _seed_user(db_session)
        repo.get_sketch_stats.side_effect = RuntimeError("neo4j down")
        res = client.get(f"/api/sketches/{sketch_id}/stats", headers=headers)
        assert res.status_code == 500
