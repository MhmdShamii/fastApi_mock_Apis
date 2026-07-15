import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    verify_password,
)
from app.domains.auth.oauth_models import (
    AuthorizationCode,
    OAuthClient,
    OAuthRefreshToken,
)
from app.domains.auth.oauth_schemas import (
    OAuthError,
    OAuthRedirectError,
    TokenResponse,
)
from app.domains.users.models import User
from app.domains.users.service import UserService


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _b64url_sha256(value: str) -> str:
    digest = hashlib.sha256(value.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OAuthService:
    """OAuth 2.1 Authorization Code + PKCE flow."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.users = UserService(db)

    # --- discovery -------------------------------------------------------

    @staticmethod
    def discovery_document() -> dict:
        issuer = settings.oauth_issuer.rstrip("/")
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/oauth/authorize",
            "token_endpoint": f"{issuer}/oauth/token",
            "userinfo_endpoint": f"{issuer}/oauth/userinfo",
            "revocation_endpoint": f"{issuer}/oauth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        }

    # --- client helpers --------------------------------------------------

    async def _get_active_client(self, client_id: str) -> OAuthClient | None:
        client = await self.db.get(OAuthClient, client_id)
        if client is None or not client.is_active:
            return None
        return client

    # --- authorize (GET) -------------------------------------------------

    async def validate_authorization_request(
        self,
        *,
        response_type: str | None,
        client_id: str | None,
        redirect_uri: str | None,
        code_challenge: str | None,
        code_challenge_method: str | None,
        state: str | None,
    ) -> OAuthClient:
        """Validate an /authorize request.

        Raises OAuthError for client_id/redirect_uri problems (cannot redirect),
        and OAuthRedirectError for everything else (deliverable to redirect_uri).
        Returns the validated client on success.
        """
        # These two must be valid before we can trust redirect_uri at all.
        if not client_id:
            raise OAuthError("invalid_request", "Missing client_id")
        client = await self._get_active_client(client_id)
        if client is None:
            raise OAuthError("invalid_client", "Unknown or inactive client_id")
        if not redirect_uri:
            raise OAuthError("invalid_request", "Missing redirect_uri")
        if redirect_uri not in client.redirect_uris:
            raise OAuthError("invalid_request", "redirect_uri is not registered")

        # From here, errors can be delivered back to the client.
        if not state:
            raise OAuthRedirectError(
                redirect_uri, "invalid_request", "Missing state", ""
            )
        if response_type != "code":
            raise OAuthRedirectError(
                redirect_uri,
                "unsupported_response_type",
                "Only response_type=code is supported",
                state,
            )
        if not code_challenge:
            raise OAuthRedirectError(
                redirect_uri, "invalid_request", "Missing code_challenge", state
            )
        if code_challenge_method != "S256":
            raise OAuthRedirectError(
                redirect_uri,
                "invalid_request",
                "code_challenge_method must be S256",
                state,
            )
        return client

    # --- authorize login (POST) -----------------------------------------

    async def authenticate(self, email: str, password: str) -> User | None:
        user = await self.users.get_by_email(email.strip().lower())
        if user is None or not user.is_active:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    async def create_authorization_code(
        self,
        *,
        user_id: int,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str,
        scope: str | None,
    ) -> str:
        code = secrets.token_urlsafe(32)
        self.db.add(
            AuthorizationCode(
                code=code,
                user_id=user_id,
                client_id=client_id,
                redirect_uri=redirect_uri,
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method,
                scope=scope,
                expires_at=_now() + timedelta(seconds=settings.auth_code_ttl_seconds),
            )
        )
        await self.db.commit()
        return code

    # --- token: authorization_code grant --------------------------------

    async def exchange_code(
        self,
        *,
        code: str | None,
        redirect_uri: str | None,
        client_id: str | None,
        code_verifier: str | None,
    ) -> TokenResponse:
        if not (code and redirect_uri and client_id and code_verifier):
            raise OAuthError(
                "invalid_request",
                "code, redirect_uri, client_id and code_verifier are required",
            )

        stored = await self.db.get(AuthorizationCode, code)
        if stored is None:
            raise OAuthError("invalid_grant", "Authorization code not found")

        # Single-use: reusing a spent code implies interception. Revoke the
        # token family it produced and reject hard.
        if stored.used_at is not None:
            if stored.family_id is not None:
                await self._revoke_family(stored.family_id)
            raise OAuthError(
                "invalid_grant",
                "Authorization code has already been used",
                status_code=401,
            )

        if stored.expires_at <= _now():
            raise OAuthError("invalid_grant", "Authorization code has expired")
        if stored.client_id != client_id:
            raise OAuthError("invalid_grant", "client_id mismatch")
        if stored.redirect_uri != redirect_uri:
            raise OAuthError("invalid_grant", "redirect_uri mismatch")

        if not (43 <= len(code_verifier) <= 128):
            raise OAuthError(
                "invalid_request", "code_verifier must be 43-128 characters"
            )
        if not hmac.compare_digest(
            _b64url_sha256(code_verifier), stored.code_challenge
        ):
            raise OAuthError("invalid_grant", "PKCE verification failed")

        user = await self.db.get(User, stored.user_id)
        if user is None or not user.is_active:
            raise OAuthError("invalid_grant", "User is no longer active")

        # Consume the code and bind it to a new token family.
        family_id = str(uuid.uuid4())
        stored.used_at = _now()
        stored.family_id = family_id

        return await self._issue_pair(
            user=user,
            client_id=client_id,
            family_id=family_id,
            scope=stored.scope,
        )

    # --- token: refresh_token grant -------------------------------------

    async def refresh(
        self, *, refresh_token: str | None, client_id: str | None
    ) -> TokenResponse:
        if not (refresh_token and client_id):
            raise OAuthError(
                "invalid_request", "refresh_token and client_id are required"
            )

        stored = await self._get_refresh_by_hash(hash_refresh_token(refresh_token))
        if stored is None:
            raise OAuthError("invalid_grant", "Unknown refresh token", status_code=401)
        if stored.client_id != client_id:
            raise OAuthError("invalid_grant", "client_id mismatch", status_code=401)

        # Reuse detection: a rotated-out token (revoked AND replaced) being
        # presented again means the family is compromised.
        if stored.revoked_at is not None and stored.replaced_by is not None:
            await self._revoke_family(stored.family_id)
            raise OAuthError(
                "invalid_grant", "Refresh token reuse detected", status_code=401
            )
        if stored.revoked_at is not None:
            raise OAuthError(
                "invalid_grant", "Refresh token is revoked", status_code=401
            )
        if stored.expires_at <= _now():
            raise OAuthError(
                "invalid_grant", "Refresh token has expired", status_code=401
            )

        user = await self.db.get(User, stored.user_id)
        if user is None or not user.is_active:
            raise OAuthError(
                "invalid_grant", "User is no longer active", status_code=401
            )

        # Rotate within the same family.
        stored.revoked_at = _now()
        pair, new_id = await self._issue_pair(
            user=user,
            client_id=client_id,
            family_id=stored.family_id,
            scope=stored.scope,
            return_id=True,
        )
        stored.replaced_by = new_id
        await self.db.commit()
        return pair

    # --- revoke ----------------------------------------------------------

    async def revoke(self, token: str | None) -> None:
        # RFC 7009: always succeeds. We only hold refresh tokens; access
        # tokens are stateless and simply ignored.
        if not token:
            return
        stored = await self._get_refresh_by_hash(hash_refresh_token(token))
        if stored is not None and stored.revoked_at is None:
            await self._revoke_family(stored.family_id)

    # --- internals -------------------------------------------------------

    async def _issue_pair(
        self,
        *,
        user: User,
        client_id: str,
        family_id: str,
        scope: str | None,
        return_id: bool = False,
    ):
        raw_refresh = generate_refresh_token()
        token = OAuthRefreshToken(
            token_hash=hash_refresh_token(raw_refresh),
            user_id=user.id,
            family_id=family_id,
            client_id=client_id,
            scope=scope,
            expires_at=_now() + timedelta(seconds=settings.refresh_token_ttl_seconds),
        )
        self.db.add(token)
        await self.db.flush()  # assign token.id before commit

        access = create_access_token(user_id=user.id, role=user.role.value)
        pair = TokenResponse(
            access_token=access,
            refresh_token=raw_refresh,
            expires_in=settings.access_token_ttl_seconds,
            scope=scope,
        )
        if return_id:
            return pair, token.id
        await self.db.commit()
        return pair

    async def _get_refresh_by_hash(
        self, token_hash: str
    ) -> OAuthRefreshToken | None:
        result = await self.db.execute(
            select(OAuthRefreshToken).where(
                OAuthRefreshToken.token_hash == token_hash
            )
        )
        return result.scalar_one_or_none()

    async def _revoke_family(self, family_id: str) -> None:
        await self.db.execute(
            update(OAuthRefreshToken)
            .where(
                OAuthRefreshToken.family_id == family_id,
                OAuthRefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=_now())
        )
        await self.db.commit()
