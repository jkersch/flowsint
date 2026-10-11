"""Contract for POST /api/investigations/{id}/nodes/search."""

import uuid

from flowsint_core.core.models import Sketch
from flowsint_core.core.types import Role
from flowsint_types import Domain

from .test_sketch_search import _rows, _seed_user, repo  # noqa: F401


def _investigation_of(db_session, sketch_id):
    return str(db_session.get(Sketch, uuid.UUID(sketch_id)).investigation_id)


def _search(client, investigation_id, headers, **body):
    return client.post(
        f"/api/investigations/{investigation_id}/nodes/search",
        json=body,
        headers=headers,
    )


def test_returns_hits_with_their_sketch(client, db_session, repo):  # noqa: F811
    headers, sketch_id = _seed_user(db_session, roles=(Role.VIEWER,))
    investigation_id = _investigation_of(db_session, sketch_id)
    rows = _rows(Domain(domain="evil.example"))
    rows[0]["sketch_id"] = sketch_id
    repo.search_nodes.return_value = {"nodes": rows, "next_cursor": None}

    res = _search(client, investigation_id, headers, query="evil", types=["Domain"])

    assert res.status_code == 200
    hit = res.json()["nodes"][0]
    assert hit["sketch_id"] == sketch_id
    assert hit["sketch_title"] == "Board"
    assert hit["node"]["nodeLabel"] == "evil.example"
    kwargs = repo.search_nodes.call_args.kwargs
    assert kwargs["sketch_ids"] == [sketch_id]
    assert (kwargs["query"], kwargs["types"]) == ("evil", ["domain"])


def test_stranger_is_forbidden(client, db_session, repo):  # noqa: F811
    _, sketch_id = _seed_user(db_session)
    headers, _ = _seed_user(db_session)
    res = _search(client, _investigation_of(db_session, sketch_id), headers)
    assert res.status_code == 403
    repo.search_nodes.assert_not_called()


def test_unknown_investigation(client, db_session, repo):  # noqa: F811
    headers, _ = _seed_user(db_session)
    assert _search(client, uuid.uuid4(), headers).status_code == 404


def test_invalid_criteria(client, db_session, repo):  # noqa: F811
    headers, sketch_id = _seed_user(db_session)
    res = _search(
        client, _investigation_of(db_session, sketch_id), headers, types=["Nope"]
    )
    assert res.status_code == 400
    assert "Unknown type" in res.json()["detail"]


def test_backend_failure(client, db_session, repo):  # noqa: F811
    headers, sketch_id = _seed_user(db_session)
    repo.search_nodes.side_effect = RuntimeError("down")
    res = _search(client, _investigation_of(db_session, sketch_id), headers)
    assert res.status_code == 500


def test_read_only_token_may_search(client, db_session, repo):  # noqa: F811
    from .test_api_tokens import _bearer, _create

    headers, sketch_id = _seed_user(db_session)
    token = _create(client, headers, scopes=["read"]).json()["token"]
    res = _search(client, _investigation_of(db_session, sketch_id), _bearer(token))
    assert res.status_code == 200
