from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import CurrentUser, get_db
from app.domains.auth.oauth_schemas import (
    OAuthError,
    OAuthRedirectError,
    TokenResponse,
    UserInfoResponse,
)
from app.domains.auth.oauth_service import OAuthService

# Discovery lives at the site root per RFC 8414; the rest are under /oauth.
discovery_router = APIRouter(tags=["oauth"])
router = APIRouter(prefix="/oauth", tags=["oauth"])

# The OAuth request context forwarded to the frontend login page and posted
# back to /oauth/authorize/login.
_FORWARD_FIELDS = (
    "response_type",
    "client_id",
    "redirect_uri",
    "code_challenge",
    "code_challenge_method",
    "state",
    "scope",
)


def get_oauth_service(db: Annotated[AsyncSession, Depends(get_db)]) -> OAuthService:
    return OAuthService(db)


ServiceDep = Annotated[OAuthService, Depends(get_oauth_service)]


def _login_page_redirect(
    params: dict[str, str | None],
    *,
    client_name: str | None = None,
    error: str | None = None,
    email: str | None = None,
) -> RedirectResponse:
    """Hand the browser to the frontend login page, carrying the OAuth request
    context as query params. The React page renders the form and posts the
    fields straight back to /oauth/authorize/login."""
    query = {k: params[k] for k in _FORWARD_FIELDS if params.get(k) is not None}
    if client_name is not None:
        query["client_name"] = client_name
    if error is not None:
        query["error"] = error
    if email is not None:
        query["email"] = email
    return RedirectResponse(
        f"{settings.oauth_login_url}?{urlencode(query)}", status_code=302
    )


def _redirect_with_error(exc: OAuthRedirectError) -> RedirectResponse:
    query = urlencode(
        {
            "error": exc.error,
            "error_description": exc.error_description,
            "state": exc.state,
        }
    )
    return RedirectResponse(f"{exc.redirect_uri}?{query}", status_code=302)


# --- discovery -----------------------------------------------------------


@discovery_router.get("/.well-known/oauth-authorization-server")
async def discovery() -> dict:
    return OAuthService.discovery_document()


# --- authorize -----------------------------------------------------------


@router.get("/authorize")
async def authorize(
    service: ServiceDep,
    response_type: str | None = None,
    client_id: str | None = None,
    redirect_uri: str | None = None,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
    state: str | None = None,
    scope: str | None = None,
):
    try:
        client = await service.validate_authorization_request(
            response_type=response_type,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            state=state,
        )
    except OAuthRedirectError as exc:
        return _redirect_with_error(exc)
    # OAuthError (bad client_id/redirect_uri) propagates to the JSON handler:
    # we can't safely show a login page for an unverified client/redirect_uri.

    params = {
        "response_type": response_type,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "state": state,
        "scope": scope,
    }
    return _login_page_redirect(params, client_name=client.client_name)


@router.post("/authorize/login")
async def authorize_login(
    service: ServiceDep,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    response_type: Annotated[str, Form()],
    client_id: Annotated[str, Form()],
    redirect_uri: Annotated[str, Form()],
    code_challenge: Annotated[str, Form()],
    code_challenge_method: Annotated[str, Form()],
    state: Annotated[str, Form()],
    scope: Annotated[str | None, Form()] = None,
):
    params = {
        "response_type": response_type,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "state": state,
        "scope": scope,
    }

    # Re-validate the request before trusting redirect_uri / issuing a code.
    try:
        client = await service.validate_authorization_request(
            response_type=response_type,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            state=state,
        )
    except OAuthRedirectError as exc:
        return _redirect_with_error(exc)

    user = await service.authenticate(email, password)
    if user is None:
        # Bounce back to the styled login page with the error, rather than
        # rendering our own. Same message for bad password and unknown email.
        return _login_page_redirect(
            params,
            client_name=client.client_name,
            error="invalid_credentials",
            email=email,
        )

    code = await service.create_authorization_code(
        user_id=user.id,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scope=scope,
    )
    query = urlencode({"code": code, "state": state})
    return RedirectResponse(f"{redirect_uri}?{query}", status_code=302)


# --- token ---------------------------------------------------------------


@router.post("/token", response_model=TokenResponse, response_model_exclude_none=True)
async def token(
    service: ServiceDep,
    grant_type: Annotated[str, Form()],
    code: Annotated[str | None, Form()] = None,
    redirect_uri: Annotated[str | None, Form()] = None,
    client_id: Annotated[str | None, Form()] = None,
    code_verifier: Annotated[str | None, Form()] = None,
    refresh_token: Annotated[str | None, Form()] = None,
) -> TokenResponse:
    if grant_type == "authorization_code":
        return await service.exchange_code(
            code=code,
            redirect_uri=redirect_uri,
            client_id=client_id,
            code_verifier=code_verifier,
        )
    if grant_type == "refresh_token":
        return await service.refresh(
            refresh_token=refresh_token, client_id=client_id
        )
    raise OAuthError("unsupported_grant_type", f"Unsupported grant_type: {grant_type}")


# --- userinfo ------------------------------------------------------------


@router.get("/userinfo", response_model=UserInfoResponse)
async def userinfo(user: CurrentUser) -> UserInfoResponse:
    # Read straight from the current DB row, not from JWT claims.
    return UserInfoResponse(
        sub=str(user.id),
        email=user.email,
        role=user.role.value,
        full_name=user.full_name,
    )


# --- revoke --------------------------------------------------------------


@router.post("/revoke")
async def revoke(
    service: ServiceDep,
    token: Annotated[str, Form()],
    token_type_hint: Annotated[str | None, Form()] = None,
) -> Response:
    # RFC 7009: always 200, even for unknown tokens.
    await service.revoke(token)
    return JSONResponse(content={}, status_code=200)
