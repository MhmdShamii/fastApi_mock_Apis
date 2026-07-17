# RBAC for /users. Roles: internal | customer | guest.
#
#   401 = not authenticated (missing/invalid token). Always carries a
#         `WWW-Authenticate: Bearer` header.
#   403 = authenticated but not allowed.
#   404 = resource doesn't exist — but never leaked to unauthorized callers
#         (a non-owner, non-internal GET/{id} gets 403, not 404, so user IDs
#         can't be enumerated).
#
# Manual test checklist (replace $BASE, $CUST (customer token), $INT (internal
# token), and IDs as appropriate):
#
#   # 1. Unauthenticated PATCH -> 401
#   curl -i -X PATCH $BASE/users/1 -H 'Content-Type: application/json' \
#        -d '{"full_name":"x"}'
#   #   => 401, header: WWW-Authenticate: Bearer
#
#   # 2. Customer PATCH to self with a role field -> 422 (unknown field)
#   curl -i -X PATCH $BASE/users/<self_id> -H "Authorization: Bearer $CUST" \
#        -H 'Content-Type: application/json' -d '{"role":"internal"}'
#   #   => 422
#
#   # 3. Customer PATCH to another user's ID -> 403
#   curl -i -X PATCH $BASE/users/<other_id> -H "Authorization: Bearer $CUST" \
#        -H 'Content-Type: application/json' -d '{"full_name":"x"}'
#   #   => 403
#
#   # 4. Internal PATCH to any user with a role field -> 200
#   curl -i -X PATCH $BASE/users/<any_id> -H "Authorization: Bearer $INT" \
#        -H 'Content-Type: application/json' -d '{"role":"internal"}'
#   #   => 200
#
#   # 5. Unauthenticated GET /users -> 401
#   curl -i $BASE/users
#   #   => 401, header: WWW-Authenticate: Bearer
#
#   # 6. Customer GET /users -> 403
#   curl -i $BASE/users -H "Authorization: Bearer $CUST"
#   #   => 403
#
#   # 7. Internal GET /users -> 200 with list
#   curl -i $BASE/users -H "Authorization: Bearer $INT"
#   #   => 200, JSON array

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser, get_db, require_roles
from app.core.exceptions import ForbiddenError
from app.domains.users.enums import Role
from app.domains.users.models import User
from app.domains.users.schemas import UserAdminUpdate, UserRead, UserSelfUpdate
from app.domains.users.service import UserService

router = APIRouter(prefix="/users", tags=["users"])


def get_user_service(db: Annotated[AsyncSession, Depends(get_db)]) -> UserService:
    return UserService(db)


ServiceDep = Annotated[UserService, Depends(get_user_service)]


def _authorize_self_or_internal(caller: User, user_id: int) -> None:
    """Allow the request only if the caller is INTERNAL or is the target user.

    Raises 403 (not 404) for everyone else so a non-owner cannot probe which
    user IDs exist.
    """
    if caller.role != Role.INTERNAL and caller.id != user_id:
        raise ForbiddenError("Not permitted")


# Note: POST /users was removed. Account creation goes through /auth/register.


@router.get(
    "",
    response_model=list[UserRead],
    dependencies=[Depends(require_roles(Role.INTERNAL))],
)
async def list_users(
    service: ServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[UserRead]:
    return await service.list(limit=limit, offset=offset)


@router.get("/{user_id}", response_model=UserRead)
async def get_user(
    user_id: int, caller: CurrentUser, service: ServiceDep
) -> UserRead:
    _authorize_self_or_internal(caller, user_id)
    return await service.get(user_id)


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: int,
    caller: CurrentUser,
    service: ServiceDep,
    body: Annotated[dict[str, Any], Body(...)],
) -> UserRead:
    _authorize_self_or_internal(caller, user_id)

    # Field-level restriction: only INTERNAL callers may touch role/is_active.
    # Non-internal bodies are validated against UserSelfUpdate, whose
    # extra="forbid" turns a smuggled field (e.g. "role") into a 422.
    schema = UserAdminUpdate if caller.role == Role.INTERNAL else UserSelfUpdate
    try:
        data = schema.model_validate(body)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc

    return await service.update(user_id, data)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles(Role.INTERNAL))],
)
async def delete_user(user_id: int, service: ServiceDep) -> None:
    await service.delete(user_id)
