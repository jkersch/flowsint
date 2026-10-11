from datetime import datetime
from typing import List, Literal, Optional

from pydantic import UUID4, BaseModel, Field

from .base import ORMBase

Scope = Literal["read", "write", "enrich"]


class ApiTokenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    expires_in_days: Optional[int] = Field(None, ge=1, le=3650)
    scopes: Optional[List[Scope]] = Field(
        None, min_length=1, description="Defaults to all: read, write, enrich"
    )


class ApiTokenRead(ORMBase):
    id: UUID4
    name: str
    prefix: str
    scopes: List[str]
    created_at: datetime
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class ApiTokenCreated(ApiTokenRead):
    token: str = Field(..., description="The token itself; it is shown only once")
