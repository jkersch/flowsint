"""Which scope each request needs, and that tokens are held to it."""

import uuid

import pytest

from app.api.scopes import ENRICH, READ, WRITE, required_scope
from app.main import app
from flowsint_core.core.models import Investigation, InvestigationUserRole, Sketch
from flowsint_core.core.types import Role

from .test_api_tokens import _bearer, _create, _login

# Write-method routes that are NOT plain writes. Everything else with POST,
# PUT, PATCH or DELETE must need "write": adding a read-only POST means adding
# it here (and to READ_POSTS) on purpose.
NON_WRITE_ROUTES = {
    ("POST", "/api/sketches/{sketch_id}/nodes/search"): READ,
    ("POST", "/api/investigations/{investigation_id}/nodes/search"): READ,
    ("POST", "/api/sketches/{sketch_id}/import/analyze"): READ,
    ("POST", "/api/types/detect"): READ,
    ("POST", "/api/flows/{flow_id}/compute"): READ,
    ("POST", "/api/custom-types/{id}/validate"): READ,
    ("POST", "/api/enrichers/{enricher_name}/launch"): ENRICH,
    ("POST", "/api/flows/{flow_id}/launch"): ENRICH,
    ("POST", "/api/enrichers/templates/{template_id}/test"): ENRICH,
}


def _routes():
    for route in app.routes:
        for method in getattr(route, "methods", None) or []:
            yield method, route.path


def test_every_registered_route_is_classified():
    for method, path in _routes():
        if method in ("GET", "HEAD", "OPTIONS"):
            expected = READ
        else:
            expected = NON_WRITE_ROUTES.get((method, path), WRITE)
        assert required_scope(method, path) == expected, (method, path)


def test_classified_routes_exist():
    """A stale entry here would hide a renamed endpoint."""
    registered = set(_routes())
    for key in NON_WRITE_ROUTES:
        assert key in registered, key


@pytest.mark.parametrize(
    "method, path, scope",
    [
        ("get", "/api/sketches/abc", READ),
        ("HEAD", "/api/x", READ),
        ("POST", "/api/sketches/abc/nodes/search/", READ),
        ("POST", "/api/sketches/abc/nodes/add", WRITE),
        ("POST", "/api/enrichers/templates/launch", WRITE),
        ("POST", "/api/enrichers/dns/launch", ENRICH),
        ("PATCH", "/api/anything", WRITE),
        ("DELETE", "/api/types/detect", WRITE),
        ("POST", "/", WRITE),
    ],
)
def test_required_scope(method, path, scope):
    assert required_scope(method, path) == scope


def _sketch_for(db_session, user):
    inv = Investigation(id=uuid.uuid4(), name="Case", owner_id=user.id)
    sketch = Sketch(
        id=uuid.uuid4(),
        title="Board",
        description="",
        owner_id=user.id,
        investigation_id=inv.id,
    )
    role = InvestigationUserRole(
        user_id=user.id, investigation_id=inv.id, roles=[Role.OWNER]
    )
    db_session.add_all([inv, sketch, role])
    db_session.commit()
    return str(sketch.id)


def _token(client, db_session, scopes):
    user, headers = _login(db_session)
    token = _create(client, headers, scopes=scopes).json()["token"]
    return user, _bearer(token)


def test_read_only_token_can_read_but_not_write(client, db_session):
    user, headers = _token(client, db_session, ["read"])
    sketch_id = _sketch_for(db_session, user)

    assert client.get(f"/api/sketches/{sketch_id}", headers=headers).status_code == 200
    res = client.put(
        f"/api/sketches/{sketch_id}",
        json={"title": "renamed"},
        headers=headers,
    )
    assert res.status_code == 403
    assert res.json()["detail"] == "This API token lacks the 'write' scope"


def test_write_scope_without_read_cannot_read(client, db_session):
    user, headers = _token(client, db_session, ["write"])
    sketch_id = _sketch_for(db_session, user)
    res = client.get(f"/api/sketches/{sketch_id}", headers=headers)
    assert res.status_code == 403
    assert "'read'" in res.json()["detail"]


def test_enrich_needs_enrich_scope(client, db_session):
    user, headers = _token(client, db_session, ["read", "write"])
    sketch_id = _sketch_for(db_session, user)
    res = client.post(
        "/api/enrichers/domain_to_ip/launch",
        json={"node_ids": ["4:x:1"], "sketch_id": sketch_id},
        headers=headers,
    )
    assert res.status_code == 403
    assert "'enrich'" in res.json()["detail"]


def test_scopes_do_not_limit_login_sessions(client, db_session):
    user, headers = _login(db_session)
    sketch_id = _sketch_for(db_session, user)
    res = client.put(
        f"/api/sketches/{sketch_id}", json={"title": "renamed"}, headers=headers
    )
    assert res.status_code == 200


def test_token_scopes_are_listed(client, db_session):
    _, headers = _login(db_session)
    created = _create(client, headers, scopes=["enrich", "read"]).json()
    assert created["scopes"] == ["read", "enrich"]
    assert client.get("/api/tokens", headers=headers).json()[0]["scopes"] == [
        "read",
        "enrich",
    ]


@pytest.mark.parametrize("scopes", [[], ["admin"]])
def test_invalid_scopes_are_rejected(client, db_session, scopes):
    _, headers = _login(db_session)
    assert _create(client, headers, scopes=scopes).status_code == 422


def test_direct_call_with_token_but_no_request_is_denied(client, db_session):
    from fastapi import HTTPException

    from app.api.deps import get_current_user

    _, headers = _login(db_session)
    token = _create(client, headers).json()["token"]
    with pytest.raises(HTTPException) as e:
        get_current_user(token=token, db=db_session)
    assert e.value.status_code == 403
