from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import UnauthorizedError
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    verify_password,
)
from app.domains.auth.models import RefreshToken
from app.domains.auth.schemas import RegisterRequest, TokenPair
from app.domains.users.enums import Role
from app.domains.users.models import User
from app.domains.users.schemas import UserCreate
from app.domains.users.service import UserService


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AuthService:
    """Registration, login, and refresh-token lifecycle."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.users = UserService(db)

    async def register(self, data: RegisterRequest) -> User:
        # Self-registration is always a CUSTOMER; privileged roles are
        # assigned out-of-band. UserService enforces email uniqueness.
        return await self.users.create(
            UserCreate(
                email=data.email,
                password=data.password,
                full_name=data.full_name,
                role=Role.CUSTOMER,
            )
        )

    async def login(self, email: str, password: str) -> TokenPair:
        user = await self.users.get_by_email(email)
        # Verify even when the user is missing to avoid leaking which emails
        # exist via response timing.
        password_ok = verify_password(
            password, user.password_hash if user else DUMMY_PASSWORD_HASH
        )
        if user is None or not password_ok:
            raise UnauthorizedError("Invalid email or password")
        if not user.is_active:
            raise UnauthorizedError("Account is disabled")

        return await self._issue_tokens(user)

    async def refresh(self, refresh_token: str) -> TokenPair:
        token_hash = hash_refresh_token(refresh_token)
        stored = await self._get_by_hash(token_hash)

        if stored is None:
            raise UnauthorizedError("Invalid refresh token")

        # Reuse detection: a token presented after being rotated out is a
        # sign of theft — revoke the whole family to force re-login.
        if stored.revoked_at is not None:
            await self._revoke_all_for_user(stored.user_id)
            raise UnauthorizedError("Refresh token has already been used")

        if stored.expires_at <= _now():
            raise UnauthorizedError("Refresh token has expired")

        user = await self.db.get(User, stored.user_id)
        if user is None or not user.is_active:
            raise UnauthorizedError("Account is no longer active")

        # Rotate: revoke the presented token, issue a fresh pair.
        stored.revoked_at = _now()
        return await self._issue_tokens(user)

    async def logout(self, refresh_token: str) -> None:
        stored = await self._get_by_hash(hash_refresh_token(refresh_token))
        if stored is not None and stored.revoked_at is None:
            stored.revoked_at = _now()
            await self.db.commit()

    # --- internals -------------------------------------------------------

    async def _issue_tokens(self, user: User) -> TokenPair:
        raw_refresh = generate_refresh_token()
        self.db.add(
            RefreshToken(
                user_id=user.id,
                token_hash=hash_refresh_token(raw_refresh),
                expires_at=_now()
                + timedelta(seconds=settings.refresh_token_ttl_seconds),
            )
        )
        await self.db.commit()

        access = create_access_token(user_id=user.id, role=user.role.value)
        return TokenPair(
            access_token=access,
            refresh_token=raw_refresh,
            expires_in=settings.access_token_ttl_seconds,
        )

    async def _get_by_hash(self, token_hash: str) -> RefreshToken | None:
        result = await self.db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def _revoke_all_for_user(self, user_id: int) -> None:
        await self.db.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=_now())
        )
        await self.db.commit()
