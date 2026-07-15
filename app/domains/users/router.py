from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db
from app.domains.users.schemas import UserCreate, UserRead, UserUpdate
from app.domains.users.service import UserService

router = APIRouter(prefix="/users", tags=["users"])


def get_user_service(db: Annotated[AsyncSession, Depends(get_db)]) -> UserService:
    return UserService(db)


ServiceDep = Annotated[UserService, Depends(get_user_service)]


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(data: UserCreate, service: ServiceDep) -> UserRead:
    return await service.create(data)


@router.get("", response_model=list[UserRead])
async def list_users(
    service: ServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[UserRead]:
    return await service.list(limit=limit, offset=offset)


@router.get("/{user_id}", response_model=UserRead)
async def get_user(user_id: int, service: ServiceDep) -> UserRead:
    return await service.get(user_id)


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(user_id: int, data: UserUpdate, service: ServiceDep) -> UserRead:
    return await service.update(user_id, data)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, service: ServiceDep) -> None:
    await service.delete(user_id)
