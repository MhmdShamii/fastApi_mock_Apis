from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.domains.users.enums import Role


class UserBase(BaseModel):
    email: EmailStr
    full_name: str | None = Field(default=None, max_length=255)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        # Domain is case-insensitive; lowercase so uniqueness lookups are stable.
        return value.strip().lower()


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)
    role: Role = Role.CUSTOMER


    class UserAdminUpdate(BaseModel):
    """Full update surface — only INTERNAL callers may use this schema."""

    email: EmailStr | None = None
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    role: Role | None = None
    is_active: bool | None = None

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str | None) -> str | None:
        return value.strip().lower() if value is not None else None


# Backwards-compatible alias. The service layer (intentionally untouched) imports
# and type-hints ``UserUpdate``; keeping this name pointed at the admin schema
# means those signatures keep working unchanged.
UserUpdate = UserAdminUpdate


class UserSelfUpdate(BaseModel):
    """Self-service update surface — a user editing their own account.

    Deliberately excludes ``role`` and ``is_active`` so a non-privileged caller
    cannot elevate themselves. ``extra="forbid"`` makes any unknown field (e.g.
    a smuggled ``"role"``) a 422 validation error rather than a silent no-op.
    """

    model_config = ConfigDict(extra="forbid")

    email: EmailStr | None = None
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str | None) -> str | None:
        return value.strip().lower() if value is not None else None


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: Role
    is_active: bool
    created_at: datetime
    updated_at: datetime
