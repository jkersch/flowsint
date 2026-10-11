"""Tests for personal API tokens (ApiTokenService + ApiTokenRepository)."""

import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from flowsint_core.core.models import ApiToken
from flowsint_core.core.repositories import ApiTokenRepository, ProfileRepository
from flowsint_core.core.services.api_token_service import (
    MAX_ACTIVE_TOKENS,
    ApiTokenService,
    hash_token,
    is_api_token,
)
from flowsint_core.core.services.exceptions import NotFoundError, ValidationError
from tests.factories import ProfileFactory

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def service(db_session, clock):
    ProfileFactory._meta.sqlalchemy_session = db_session
    return ApiTokenService(
        db=db_session,
        token_repo=ApiTokenRepository(db_session),
        profile_repo=ProfileRepository(db_session),
        now=clock,
    )


@pytest.fixture
def user(service):
    return ProfileFactory()


class TestCreate:
    def test_token_format_and_stored_fields(self, service, user, db_session):
        row, plaintext = service.create(user.id, "  laptop  ")

        assert re.fullmatch(r"fsk_[A-Za-z0-9_-]{43}", plaintext)
        assert row.name == "laptop"
        assert row.prefix == plaintext[:12]
        assert row.token_hash == hash_token(plaintext)
        assert row.owner_id == user.id
        assert row.expires_at is None and row.revoked_at is None

    def test_plaintext_is_never_stored(self, service, user, db_session):
        _, plaintext = service.create(user.id, "x")
        stored = db_session.query(ApiToken).one()
        assert plaintext not in {
            str(getattr(stored, c.name)) for c in ApiToken.__table__.columns
        }

    def test_tokens_are_unique(self, service, user):
        tokens = {service.create(user.id, f"t{i}")[1] for i in range(5)}
        assert len(tokens) == 5

    def test_expiry(self, service, user):
        row, _ = service.create(user.id, "ci", expires_in_days=30)
        assert row.expires_at.replace(tzinfo=timezone.utc) == T0 + timedelta(days=30)

    @pytest.mark.parametrize(
        "name, days, message",
        [
            ("", None, "needs a name"),
            ("   ", None, "needs a name"),
            ("x" * 101, None, "at most 100"),
            ("ok", 0, "between 1 and 3650"),
            ("ok", 3651, "between 1 and 3650"),
        ],
    )
    def test_validation(self, service, user, name, days, message):
        with pytest.raises(ValidationError, match=message):
            service.create(user.id, name, days)

    def test_active_token_limit(self, service, user):
        for i in range(MAX_ACTIVE_TOKENS):
            service.create(user.id, f"t{i}")
        with pytest.raises(ValidationError, match="revoke one first"):
            service.create(user.id, "one too many")

    def test_revoked_tokens_free_up_the_limit(self, service, user):
        rows = [service.create(user.id, f"t{i}")[0] for i in range(MAX_ACTIVE_TOKENS)]
        service.revoke(rows[0].id, user.id)
        service.create(user.id, "fits again")


class TestListAndRevoke:
    def test_list_is_per_owner_newest_first(self, service, user, clock):
        other = ProfileFactory()
        service.create(user.id, "old")
        clock.now = T0 + timedelta(hours=1)
        service.create(user.id, "new")
        service.create(other.id, "not mine")
        assert [t.name for t in service.list(user.id)] == ["new", "old"]

    def test_revoke_sets_timestamp_once(self, service, user, clock):
        row, _ = service.create(user.id, "x")
        clock.now = T0 + timedelta(minutes=5)
        service.revoke(row.id, user.id)
        first = row.revoked_at
        clock.now = T0 + timedelta(minutes=10)
        service.revoke(row.id, user.id)
        assert row.revoked_at == first

    def test_cannot_revoke_someone_elses_token(self, service, user):
        row, _ = service.create(user.id, "x")
        with pytest.raises(NotFoundError):
            service.revoke(row.id, ProfileFactory().id)
        assert row.revoked_at is None

    def test_unknown_token(self, service, user):
        with pytest.raises(NotFoundError):
            service.revoke(uuid4(), user.id)


class TestAuthenticate:
    def test_valid_token_returns_owner_and_marks_use(self, service, user):
        row, plaintext = service.create(user.id, "x")
        assert service.authenticate(plaintext).user.id == user.id
        assert row.last_used_at.replace(tzinfo=timezone.utc) == T0

    def test_last_used_is_written_at_most_once_a_minute(self, service, user, clock):
        row, plaintext = service.create(user.id, "x")
        service.authenticate(plaintext)
        clock.now = T0 + timedelta(seconds=30)
        service.authenticate(plaintext)
        assert row.last_used_at.replace(tzinfo=timezone.utc) == T0
        clock.now = T0 + timedelta(minutes=2)
        service.authenticate(plaintext)
        assert row.last_used_at.replace(tzinfo=timezone.utc) == clock.now

    @pytest.mark.parametrize(
        "token", ["", "eyJhbGciOi.jwt.token", "fsk_", "fsk_unknown", "FSK_x"]
    )
    def test_unknown_or_malformed_tokens(self, service, user, token):
        service.create(user.id, "x")
        assert service.authenticate(token) is None

    def test_revoked_token(self, service, user):
        row, plaintext = service.create(user.id, "x")
        service.revoke(row.id, user.id)
        assert service.authenticate(plaintext) is None

    def test_expired_token(self, service, user, clock):
        _, plaintext = service.create(user.id, "x", expires_in_days=1)
        clock.now = T0 + timedelta(days=1) - timedelta(seconds=1)
        assert service.authenticate(plaintext) is not None
        clock.now = T0 + timedelta(days=1)
        assert service.authenticate(plaintext) is None

    def test_inactive_owner(self, service, user, db_session):
        _, plaintext = service.create(user.id, "x")
        user.is_active = False
        db_session.commit()
        assert service.authenticate(plaintext) is None

    def test_tampered_token_fails(self, service, user):
        _, plaintext = service.create(user.id, "x")
        assert (
            service.authenticate(
                plaintext[:-1] + ("A" if plaintext[-1] != "A" else "B")
            )
            is None
        )


def test_is_api_token():
    assert is_api_token("fsk_abc")
    assert not is_api_token("eyJ...")


def test_hash_is_sha256_hex():
    assert re.fullmatch(r"[0-9a-f]{64}", hash_token("fsk_x"))
    assert hash_token("fsk_x") != hash_token("fsk_y")


def test_default_factory_and_clock(db_session):
    from flowsint_core.core.services import create_api_token_service

    ProfileFactory._meta.sqlalchemy_session = db_session
    service = create_api_token_service(db_session)
    row, plaintext = service.create(ProfileFactory().id, "x")
    assert service.authenticate(plaintext) is not None
    assert row.created_at is not None


class TestScopes:
    def test_default_is_all_scopes(self, service, user):
        row, plaintext = service.create(user.id, "x")
        assert row.scopes == ["read", "write", "enrich"]
        assert service.authenticate(plaintext).scopes == ("read", "write", "enrich")

    def test_scopes_are_deduplicated_and_ordered(self, service, user):
        row, plaintext = service.create(user.id, "x", scopes=["enrich", "read", "read"])
        assert row.scopes == ["read", "enrich"]
        assert service.authenticate(plaintext).scopes == ("read", "enrich")

    @pytest.mark.parametrize(
        "scopes, message",
        [
            ([], "at least one scope"),
            (["read", "admin"], "Unknown scope\\(s\\): admin"),
        ],
    )
    def test_invalid_scopes(self, service, user, scopes, message):
        with pytest.raises(ValidationError, match=message):
            service.create(user.id, "x", scopes=scopes)

    def test_stored_unknown_scope_is_ignored(self, service, user, db_session):
        """A hand-edited row can't grant a scope that doesn't exist."""
        row, plaintext = service.create(user.id, "x", scopes=["read"])
        row.scopes = ["read", "superuser"]
        db_session.commit()
        assert service.authenticate(plaintext).scopes == ("read",)

    def test_missing_scopes_grant_nothing(self, service, user, db_session):
        row, plaintext = service.create(user.id, "x")
        row.scopes = None
        db_session.commit()
        assert service.authenticate(plaintext).scopes == ()
