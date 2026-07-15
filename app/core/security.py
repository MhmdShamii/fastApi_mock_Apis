import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import settings

_password_hasher = PasswordHasher()

# Token "type" claim values, used to stop an access token being replayed
# where a refresh token is expected and vice-versa.
ACCESS_TOKEN_TYPE = "access"

# A real, valid Argon2 hash of a random secret, computed once. Verify a
# supplied password against this when the user doesn't exist so login takes
# the same amount of time whether or not the email is registered (mitigates
# user-enumeration via timing).
DUMMY_PASSWORD_HASH = _password_hasher.hash(secrets.token_hex(16))


def hash_password(password: str) -> str:
    """Hash a plaintext password using Argon2."""
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against an Argon2 hash."""
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Return True if the hash should be recomputed with current parameters."""
    return _password_hasher.check_needs_rehash(password_hash)


# --- JWT access tokens ---------------------------------------------------


def create_access_token(*, user_id: int, role: str) -> str:
    """Mint a short-lived signed JWT access token for a user."""
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=settings.access_token_ttl_seconds)
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": ACCESS_TOKEN_TYPE,
        "iss": settings.oauth_issuer,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate an access token, raising jwt errors if invalid.

    Verifies the signature, expiry, and issuer, and rejects any token whose
    ``type`` claim is not an access token.
    """
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        issuer=settings.oauth_issuer,
        options={"require": ["exp", "iat", "sub"]},
    )
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise jwt.InvalidTokenError("token is not an access token")
    return payload


# --- Opaque refresh tokens ----------------------------------------------
# Refresh tokens are random opaque strings, not JWTs. Only their SHA-256
# hash is stored, so a leaked database cannot be used to forge sessions.


def generate_refresh_token() -> str:
    """Return a new cryptographically-random opaque refresh token."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """Deterministically hash a refresh token for storage/lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
