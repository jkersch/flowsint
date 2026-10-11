from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from flowsint_core.core.auth import ALGORITHM, AUTH_SECRET
from flowsint_core.core.models import Profile
from flowsint_core.core.postgre_db import get_db
from flowsint_core.core.services.api_token_service import (
    create_api_token_service,
    is_api_token,
)

from .scopes import required_scope

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


def _credentials_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    request: Request = None,  # type: ignore[assignment]
) -> Profile:
    """The user behind a login JWT or a personal API token (fsk_...).

    An API token must also carry the scope the request needs (see scopes.py).
    """
    if is_api_token(token):
        auth = create_api_token_service(db).authenticate(token)
        if auth is None:
            raise _credentials_exception()
        # FastAPI always passes the request; a direct call without one can't
        # be classified, so it is denied rather than let through.
        needed = required_scope(request.method, request.url.path) if request else None
        if needed not in auth.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This API token lacks the '{needed}' scope",
            )
        return auth.user

    try:
        payload = jwt.decode(token, AUTH_SECRET, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise _credentials_exception()
    except JWTError:
        raise _credentials_exception()
    user = db.query(Profile).filter(Profile.email == email).first()
    if user is None:
        raise _credentials_exception()
    return user


def get_session_user(
    token: str = Depends(oauth2_scheme),
    user: Profile = Depends(get_current_user),
) -> Profile:
    """Like get_current_user, but only for a login session, not an API token.

    Used where an API token must not be enough, e.g. to create more tokens:
    a leaked token then can't be used to mint replacements for itself.
    """
    if is_api_token(token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API tokens can't manage API tokens; log in instead",
        )
    return user
