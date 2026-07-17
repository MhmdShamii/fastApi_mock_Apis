from typing import Annotated, AsyncGenerator

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError
from app.core.security import decode_access_token
from app.db import AsyncSessionLocal
from app.domains.users.enums import Role
from app.domains.users.models import User

_bearer_scheme = HTTPBearer(auto_error=False)

# A single 401 for every authentication failure. Per RFC 6750 an authentication
# failure on a Bearer-protected resource MUST carry a ``WWW-Authenticate: Bearer``
# challenge, so we raise a FastAPI HTTPException (which lets us set the header)
# rather than the plain UnauthorizedError. The detail is deliberately generic so
# we never reveal *why* auth failed (missing vs. expired vs. unknown user).
_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Resolve the authenticated user from a Bearer access token.

    Raises 401 (never 403) on any failure, with a ``WWW-Authenticate: Bearer``
    header, so callers can distinguish "you are not authenticated" from "you are
    authenticated but not allowed" (which is 403, raised by ``require_roles``).
    """
    if credentials is None:
        raise _UNAUTHENTICATED

    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise _UNAUTHENTICATED from exc

    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise _UNAUTHENTICATED from exc

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise _UNAUTHENTICATED
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: Role):
    """Dependency factory that allows only the given roles through."""

    async def _guard(user: CurrentUser) -> User:
        if user.role not in roles:
            raise ForbiddenError("Insufficient role")
        return user

    return _guard
