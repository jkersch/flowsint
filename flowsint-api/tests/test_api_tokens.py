"""Contract for /api/tokens and for authenticating with a personal API token."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from flowsint_core.core.auth import create_access_token
from flowsint_core.core.models import ApiToken, Profile


def _login(db_session, active=True):
    user = Profile(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4()}@example.com",
        hashed_password="x",
        is_active=active,
    )
    db_session.add(user)
    db_session.commit()
    jwt = create_access_token({"sub": user.email})
    return user, {"Authorization": f"Bearer {jwt}"}


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _create(client, headers, **body):
    return client.post("/api/tokens", json={"name": "laptop", **body}, headers=headers)


class TestCreate:
    def test_returns_token_once(self, client, db_session):
        user, headers = _login(db_session)
        res = _create(client, headers, expires_in_days=30)

        assert res.status_code == 201
        body = res.json()
        assert body["token"].startswith("fsk_")
        assert body["prefix"] == body["token"][:12]
        assert body["name"] == "laptop"
        assert body["expires_at"] is not None
        assert body["revoked_at"] is None
        assert body["scopes"] == ["read", "write", "enrich"]

        listed = client.get("/api/tokens", headers=headers).json()
        assert [t["id"] for t in listed] == [body["id"]]
        assert "token" not in listed[0]
        assert "token_hash" not in listed[0]

    @pytest.mark.parametrize(
        "body", [{"name": ""}, {"name": "x" * 101}, {"expires_in_days": 0}]
    )
    def test_validation(self, client, db_session, body):
        _, headers = _login(db_session)
        assert _create(client, headers, **body).status_code == 422

    def test_service_validation_is_400(self, client, db_session):
        _, headers = _login(db_session)
        res = _create(client, headers, name="   ")
        assert res.status_code == 400
        assert "needs a name" in res.json()["detail"]

    def test_requires_login(self, client, db_session):
        assert client.post("/api/tokens", json={"name": "x"}).status_code == 401


class TestAuthenticateWithToken:
    def test_token_acts_as_its_owner(self, client, db_session):
        user, headers = _login(db_session)
        token = _create(client, headers).json()["token"]

        res = client.get("/api/auth/me", headers=_bearer(token))
        assert res.status_code == 200
        assert res.json()["email"] == user.email

    def test_jwt_still_works(self, client, db_session):
        user, headers = _login(db_session)
        assert client.get("/api/auth/me", headers=headers).json()["email"] == user.email

    @pytest.mark.parametrize("token", ["fsk_nope", "fsk_", "not-a-jwt"])
    def test_bad_tokens_are_401(self, client, db_session, token):
        res = client.get("/api/auth/me", headers=_bearer(token))
        assert res.status_code == 401
        assert res.headers["WWW-Authenticate"] == "Bearer"

    def test_revoked_token_is_rejected(self, client, db_session):
        _, headers = _login(db_session)
        created = _create(client, headers).json()
        assert (
            client.delete(f"/api/tokens/{created['id']}", headers=headers).status_code
            == 204
        )

        assert (
            client.get("/api/auth/me", headers=_bearer(created["token"])).status_code
            == 401
        )
        listed = client.get("/api/tokens", headers=headers).json()
        assert listed[0]["revoked_at"] is not None

    def test_expired_token_is_rejected(self, client, db_session):
        _, headers = _login(db_session)
        created = _create(client, headers, expires_in_days=1).json()
        row = db_session.get(ApiToken, uuid.UUID(created["id"]))
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db_session.commit()
        assert (
            client.get("/api/auth/me", headers=_bearer(created["token"])).status_code
            == 401
        )

    def test_deactivated_user_token_is_rejected(self, client, db_session):
        user, headers = _login(db_session)
        token = _create(client, headers).json()["token"]
        user.is_active = False
        db_session.commit()
        assert client.get("/api/auth/me", headers=_bearer(token)).status_code == 401


class TestTokensCannotManageTokens:
    @pytest.mark.parametrize(
        "method, path",
        [
            ("post", "/api/tokens"),
            ("get", "/api/tokens"),
            ("delete", "/api/tokens/{id}"),
        ],
    )
    def test_forbidden_with_api_token(self, client, db_session, method, path):
        _, headers = _login(db_session)
        created = _create(client, headers).json()
        kwargs = {"json": {"name": "x"}} if method == "post" else {}
        res = getattr(client, method)(
            path.format(id=created["id"]), headers=_bearer(created["token"]), **kwargs
        )
        assert res.status_code == 403
        assert "log in" in res.json()["detail"]

    def test_invalid_api_token_is_still_401(self, client, db_session):
        assert client.get("/api/tokens", headers=_bearer("fsk_nope")).status_code == 401


class TestRevoke:
    def test_cannot_revoke_another_users_token(self, client, db_session):
        _, alice = _login(db_session)
        _, bob = _login(db_session)
        created = _create(client, alice).json()
        assert (
            client.delete(f"/api/tokens/{created['id']}", headers=bob).status_code
            == 404
        )
        assert (
            client.get("/api/auth/me", headers=_bearer(created["token"])).status_code
            == 200
        )

    def test_unknown_token(self, client, db_session):
        _, headers = _login(db_session)
        assert (
            client.delete(f"/api/tokens/{uuid.uuid4()}", headers=headers).status_code
            == 404
        )

    def test_list_is_per_user(self, client, db_session):
        _, alice = _login(db_session)
        _, bob = _login(db_session)
        _create(client, alice)
        assert client.get("/api/tokens", headers=bob).json() == []
