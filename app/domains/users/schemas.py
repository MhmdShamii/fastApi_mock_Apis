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


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    role: Role | None = None
    is_active: bool | None = None

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
