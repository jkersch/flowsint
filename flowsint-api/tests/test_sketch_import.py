"""Contract for POST /sketches/{id}/import/execute."""

import json
from unittest.mock import MagicMock

import pytest

from app.api.routes import sketches as sketches_route
from flowsint_core.core.graph import GraphService
from flowsint_core.core.types import Role

from .test_sketch_search import _seed_user


@pytest.fixture
def repo(monkeypatch):
    repo = MagicMock()
    repo.batch_create_nodes.side_effect = lambda nodes, sketch_id: {
        "nodes_created": len(nodes),
        "node_ids": [f"4:x:{i}" for i in range(len(nodes))],
        "errors": [],
    }
    repo.batch_create_edges_by_element_id.return_value = {"errors": []}
    monkeypatch.setattr(
        sketches_route,
        "create_graph_service",
        lambda sketch_id, type_resolver=None, **kw: GraphService(
            sketch_id=sketch_id, repository=repo, type_resolver=type_resolver
        ),
    )
    return repo


def _execute(client, sketch_id, headers, nodes, edges=()):
    payload = {"nodes": nodes, "edges": list(edges)}
    return client.post(
        f"/api/sketches/{sketch_id}/import/execute",
        data={"entity_mappings_json": json.dumps(payload)},
        headers=headers,
    )


def _node(ref, entity_type, data):
    return {
        "id": ref,
        "node_id": ref,
        "entity_type": entity_type,
        "nodeLabel": ref,
        "include": True,
        "data": data,
    }


def test_response_lists_written_nodes(client, db_session, repo):
    headers, sketch_id = _seed_user(db_session)
    res = _execute(
        client,
        sketch_id,
        headers,
        [
            _node("page", "PhishingPage", {"url": "https://evil.example/"}),
            _node("dom", "Domain", {"domain": "evil.example"}),
        ],
        [{"from_id": "page", "to_id": "dom", "label": "HOSTED_ON"}],
    )

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "completed"
    assert body["nodes"] == [
        {
            "ref": "page",
            "id": "4:x:0",
            "type": "phishingpage",
            "label": "https://evil.example/",
        },
        {"ref": "dom", "id": "4:x:1", "type": "domain", "label": "evil.example"},
    ]
    edges = repo.batch_create_edges_by_element_id.call_args.kwargs["edges"]
    assert edges == [
        {"from_element_id": "4:x:0", "to_element_id": "4:x:1", "rel_label": "HOSTED_ON"}
    ]


def test_invalid_entity_is_reported(client, db_session, repo):
    headers, sketch_id = _seed_user(db_session)
    res = _execute(client, sketch_id, headers, [_node("x", "NoSuchType", {})])
    body = res.json()
    assert body["status"] == "completed_with_errors"
    assert body["nodes"] == []
    assert "Unknown type" in body["errors"][0]


def test_malformed_payload_is_400(client, db_session, repo):
    headers, sketch_id = _seed_user(db_session)
    res = client.post(
        f"/api/sketches/{sketch_id}/import/execute",
        data={"entity_mappings_json": "{not json"},
        headers=headers,
    )
    assert res.status_code == 400
