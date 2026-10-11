from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_session_user
from app.api.schemas.api_token import ApiTokenCreate, ApiTokenCreated, ApiTokenRead
from flowsint_core.core.models import Profile
from flowsint_core.core.postgre_db import get_db
from flowsint_core.core.services import (
    DatabaseError,
    NotFoundError,
    ValidationError,
    create_api_token_service,
)

router = APIRouter()


@router.post("", response_model=ApiTokenCreated, status_code=status.HTTP_201_CREATED)
def create_token(
    payload: ApiTokenCreate,
    db: Session = Depends(get_db),
    current_user: Profile = Depends(get_session_user),
) -> ApiTokenCreated:
    """Create a personal API token. The token is in the response, once."""
    service = create_api_token_service(db)
    try:
        token, plaintext = service.create(
            current_user.id, payload.name, payload.expires_in_days, payload.scopes
        )
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DatabaseError:
        raise HTTPException(status_code=500, detail="Failed to create token")
    return ApiTokenCreated(
        **ApiTokenRead.model_validate(token).model_dump(), token=plaintext
    )


@router.get("", response_model=List[ApiTokenRead])
def list_tokens(
    db: Session = Depends(get_db),
    current_user: Profile = Depends(get_session_user),
) -> List[ApiTokenRead]:
    """Your tokens, newest first. Only their prefixes are shown."""
    tokens = create_api_token_service(db).list(current_user.id)
    return [ApiTokenRead.model_validate(t) for t in tokens]


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_token(
    token_id: UUID,
    db: Session = Depends(get_db),
    current_user: Profile = Depends(get_session_user),
) -> None:
    """Revoke a token; requests using it are rejected from now on."""
    try:
        create_api_token_service(db).revoke(token_id, current_user.id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Token not found")
    except DatabaseError:
        raise HTTPException(status_code=500, detail="Failed to revoke token")
