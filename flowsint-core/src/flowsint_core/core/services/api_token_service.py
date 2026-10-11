"""
Personal API keys: create, list, revoke, and authenticate requests.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, List, Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session

from ..models import ApiToken, Profile
from ..repositories import ApiTokenRepository, ProfileRepository
from .base import BaseService
from .exceptions import NotFoundError, ValidationError

TOKEN_PREFIX = "fsk_"
# read: look at data; write: change it; enrich: run enrichers and flows, which
# send node values to external services. Order is the canonical display order.
SCOPES = ("read", "write", "enrich")
# Shown in listings: "fsk_" plus 8 characters of the random part.
DISPLAY_PREFIX_LENGTH = len(TOKEN_PREFIX) + 8
MAX_ACTIVE_TOKENS = 50
MAX_NAME_LENGTH = 100
MAX_EXPIRY_DAYS = 3650
# last_used_at is informational; writing it on every request would turn each
# authenticated read into a database write.
LAST_USED_RESOLUTION = timedelta(minutes=1)


def is_api_token(token: str) -> bool:
    return token.startswith(TOKEN_PREFIX)


def hash_token(token: str) -> str:
    # Tokens carry 256 bits of randomness, so a fast hash is enough: there is
    # nothing to brute-force, unlike a password.
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite drops tzinfo; treat stored naive datetimes as UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@dataclass(frozen=True)
class TokenAuth:
    """Who a valid token belongs to and what it may do."""

    user: Profile
    scopes: Tuple[str, ...]


def normalize_scopes(scopes: Optional[List[str]]) -> List[str]:
    if scopes is None:
        return list(SCOPES)
    unknown = sorted(set(scopes) - set(SCOPES))
    if unknown:
        raise ValidationError(
            f"Unknown scope(s): {', '.join(unknown)}; use {', '.join(SCOPES)}"
        )
    if not scopes:
        raise ValidationError("A token needs at least one scope")
    return [s for s in SCOPES if s in scopes]


class ApiTokenService(BaseService):
    def __init__(
        self,
        db: Session,
        token_repo: ApiTokenRepository,
        profile_repo: ProfileRepository,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        **kwargs: Any,
    ) -> None:
        super().__init__(db, **kwargs)
        self._token_repo = token_repo
        self._profile_repo = profile_repo
        self._now = now

    def create(
        self,
        owner_id: UUID,
        name: str,
        expires_in_days: Optional[int] = None,
        scopes: Optional[List[str]] = None,
    ) -> Tuple[ApiToken, str]:
        """Create a token; returns the stored row and the plaintext token.

        The plaintext is not stored anywhere and can't be shown again.
        """
        name = (name or "").strip()
        if not name:
            raise ValidationError("A token needs a name")
        if len(name) > MAX_NAME_LENGTH:
            raise ValidationError(f"Token names are at most {MAX_NAME_LENGTH} chars")
        if expires_in_days is not None and not 1 <= expires_in_days <= MAX_EXPIRY_DAYS:
            raise ValidationError(
                f"expires_in_days must be between 1 and {MAX_EXPIRY_DAYS}"
            )
        scopes = normalize_scopes(scopes)
        if self._token_repo.count_active_by_owner(owner_id) >= MAX_ACTIVE_TOKENS:
            raise ValidationError(
                f"At most {MAX_ACTIVE_TOKENS} active tokens; revoke one first"
            )

        plaintext = TOKEN_PREFIX + secrets.token_urlsafe(32)
        now = self._now()
        token = ApiToken(
            owner_id=owner_id,
            name=name,
            prefix=plaintext[:DISPLAY_PREFIX_LENGTH],
            token_hash=hash_token(plaintext),
            scopes=scopes,
            created_at=now,
            expires_at=now + timedelta(days=expires_in_days)
            if expires_in_days
            else None,
        )
        self._token_repo.add(token)
        self._commit()
        self._refresh(token)
        return token, plaintext

    def list(self, owner_id: UUID) -> List[ApiToken]:
        return self._token_repo.get_by_owner(owner_id)

    def revoke(self, token_id: UUID, owner_id: UUID) -> ApiToken:
        """Revoke one of the owner's tokens. Revoking twice is harmless."""
        token = self._token_repo.get_by_id_and_owner(token_id, owner_id)
        if not token:
            raise NotFoundError("Token not found")
        if token.revoked_at is None:
            token.revoked_at = self._now()
            self._commit()
        return token

    def authenticate(self, plaintext: str) -> Optional[TokenAuth]:
        """The active owner and scopes of a valid token, or None for any failure."""
        if not is_api_token(plaintext):
            return None
        token = self._token_repo.get_by_hash(hash_token(plaintext))
        if token is None or token.revoked_at is not None:
            return None
        now = self._now()
        expires_at = _aware(token.expires_at)
        if expires_at is not None and expires_at <= now:
            return None
        owner = self._profile_repo.get_by_id(token.owner_id)
        if owner is None or not owner.is_active:
            return None

        last_used = _aware(token.last_used_at)
        if last_used is None or now - last_used >= LAST_USED_RESOLUTION:
            token.last_used_at = now
            self._commit()
        # Filter rather than validate: a bad stored value must deny, not crash.
        granted = tuple(s for s in SCOPES if s in (token.scopes or []))
        return TokenAuth(user=owner, scopes=granted)


def create_api_token_service(db: Session) -> ApiTokenService:
    return ApiTokenService(
        db=db,
        token_repo=ApiTokenRepository(db),
        profile_repo=ProfileRepository(db),
    )
