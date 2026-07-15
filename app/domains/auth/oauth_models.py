from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class OAuthClient(Base):
    """A registered OAuth client (public client, no secret)."""

    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(primary_key=True)
    client_name: Mapped[str] = mapped_column(nullable=False)
    redirect_uris: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuthorizationCode(Base):
    """A single-use PKCE authorization code."""

    __tablename__ = "authorization_codes"

    code: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("oauth_clients.client_id"), nullable=False
    )
    redirect_uri: Mapped[str] = mapped_column(nullable=False)
    code_challenge: Mapped[str] = mapped_column(nullable=False)
    code_challenge_method: Mapped[str] = mapped_column(String(8), nullable=False)
    scope: Mapped[str | None] = mapped_column(nullable=True)
    # Set on first exchange so a second use of the code can revoke the token
    # family it produced (interception detection).
    family_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OAuthRefreshToken(Base):
    """A rotating, hashed refresh token issued via the OAuth flow.

    Kept separate from the /auth flow's ``refresh_tokens`` table so the two
    surfaces never interfere.
    """

    __tablename__ = "oauth_refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(unique=True, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    family_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("oauth_clients.client_id"), nullable=False
    )
    scope: Mapped[str | None] = mapped_column(nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    replaced_by: Mapped[int | None] = mapped_column(
        ForeignKey("oauth_refresh_tokens.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
