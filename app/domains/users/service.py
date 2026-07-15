from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import hash_password
from app.domains.users.models import User
from app.domains.users.schemas import UserCreate, UserUpdate


class UserService:
    """Data-access and business logic for users."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, user_id: int) -> User:
        user = await self.db.get(User, user_id)
        if user is None:
            raise NotFoundError(f"User {user_id} not found")
        return user

    async def get_by_email(self, email: str) -> User | None:
        result = await self.db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def list(self, *, limit: int = 50, offset: int = 0) -> list[User]:
        result = await self.db.execute(
            select(User).order_by(User.id).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def create(self, data: UserCreate) -> User:
        if await self.get_by_email(data.email) is not None:
            raise ConflictError(f"Email {data.email} is already registered")

        user = User(
            email=data.email,
            full_name=data.full_name,
            role=data.role,
            password_hash=hash_password(data.password),
        )
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def update(self, user_id: int, data: UserUpdate) -> User:
        user = await self.get(user_id)
        fields = data.model_dump(exclude_unset=True)

        if "email" in fields and fields["email"] != user.email:
            existing = await self.get_by_email(fields["email"])
            if existing is not None and existing.id != user.id:
                raise ConflictError(f"Email {fields['email']} is already registered")

        if "password" in fields:
            user.password_hash = hash_password(fields.pop("password"))

        for key, value in fields.items():
            setattr(user, key, value)

        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def delete(self, user_id: int) -> None:
        user = await self.get(user_id)
        await self.db.delete(user)
        await self.db.commit()
