"""Repository for ApiToken model."""

from typing import List, Optional
from uuid import UUID

from ..models import ApiToken
from .base import BaseRepository


class ApiTokenRepository(BaseRepository[ApiToken]):
    model = ApiToken

    def get_by_owner(self, owner_id: UUID) -> List[ApiToken]:
        return (
            self._db.query(ApiToken)
            .filter(ApiToken.owner_id == owner_id)
            .order_by(ApiToken.created_at.desc())
            .all()
        )

    def get_by_id_and_owner(self, token_id: UUID, owner_id: UUID) -> Optional[ApiToken]:
        return (
            self._db.query(ApiToken)
            .filter(ApiToken.id == token_id, ApiToken.owner_id == owner_id)
            .first()
        )

    def get_by_hash(self, token_hash: str) -> Optional[ApiToken]:
        return (
            self._db.query(ApiToken).filter(ApiToken.token_hash == token_hash).first()
        )

    def count_active_by_owner(self, owner_id: UUID) -> int:
        return (
            self._db.query(ApiToken)
            .filter(ApiToken.owner_id == owner_id, ApiToken.revoked_at.is_(None))
            .count()
        )
