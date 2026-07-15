from pydantic import BaseModel


class OAuthError(Exception):
    """An RFC 6749 error returned directly to the caller as JSON.

    Rendered as ``{"error": ..., "error_description": ...}``.
    """

    def __init__(
        self,
        error: str,
        error_description: str,
        *,
        status_code: int = 400,
    ):
        self.error = error
        self.error_description = error_description
        self.status_code = status_code
        super().__init__(f"{error}: {error_description}")


class OAuthRedirectError(Exception):
    """An authorize error that must be delivered back to the client's
    redirect_uri as query params (redirect_uri/client_id already validated)."""

    def __init__(
        self,
        redirect_uri: str,
        error: str,
        error_description: str,
        state: str,
    ):
        self.redirect_uri = redirect_uri
        self.error = error
        self.error_description = error_description
        self.state = state
        super().__init__(f"{error}: {error_description}")


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    scope: str | None = None


class UserInfoResponse(BaseModel):
    sub: str
    email: str
    role: str
    full_name: str | None = None
