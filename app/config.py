from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = Field(...)
    jwt_secret: str = Field(..., min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 604800
    auth_code_ttl_seconds: int = 60
    oauth_issuer: str = Field(...)
    # Frontend page that renders the OAuth login form. /oauth/authorize
    # redirects the browser here with the request context as query params.
    oauth_login_url: str = "http://localhost:5173/oauth/login"
    # Comma-separated list of browser origins allowed to call the API (CORS).
    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

@lru_cache()
def get_settings() -> "Settings":
    """Get application settings."""
    return Settings()

settings = get_settings()