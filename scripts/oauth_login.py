"""Interactive OAuth 2.1 + PKCE login from the terminal.

Acts as the OAuth client: generates PKCE, opens your browser to the real
authorize flow (you log in on the frontend page), catches the redirect on a
local port, exchanges the code, and prints the tokens + userinfo.

Run:  uv run python -m scripts.oauth_login
"""

import asyncio
import base64
import hashlib
import json
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings

API = "http://localhost:8000"
CLIENT_ID = "wakilni-mcp"
CB_PORT = 8765
CB_URL = f"http://127.0.0.1:{CB_PORT}/callback"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


async def _set_debug_redirect(add: bool) -> None:
    """Temporarily register (or remove) the local callback as a valid
    redirect_uri on the seeded client, so the exact-match check passes.

    Uses a throwaway NullPool engine so each call is self-contained on its own
    event loop (this runs across several asyncio.run() calls)."""
    eng = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with eng.begin() as conn:
            uris = list(
                (
                    await conn.execute(
                        text(
                            "SELECT redirect_uris FROM oauth_clients "
                            "WHERE client_id = :c"
                        ),
                        {"c": CLIENT_ID},
                    )
                ).scalar()
                or []
            )
            if add and CB_URL not in uris:
                uris.append(CB_URL)
            elif not add and CB_URL in uris:
                uris.remove(CB_URL)
            else:
                return
            await conn.execute(
                text(
                    "UPDATE oauth_clients SET redirect_uris = CAST(:u AS jsonb) "
                    "WHERE client_id = :c"
                ),
                {"u": json.dumps(uris), "c": CLIENT_ID},
            )
    finally:
        await eng.dispose()


def _catch_callback() -> dict[str, str]:
    """Block until the browser hits /callback; return its query params."""
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            if not parsed.path.startswith("/callback"):
                self.send_response(404)
                self.end_headers()
                return
            result.update({k: v[0] for k, v in parse_qs(parsed.query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<h2>Done. Return to your terminal.</h2>"
                b"<p>You can close this tab.</p>"
            )

        def log_message(self, *args):  # silence default logging
            pass

    server = HTTPServer(("127.0.0.1", CB_PORT), Handler)
    while "code" not in result and "error" not in result:
        server.handle_request()
    server.server_close()
    return result


def main() -> None:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = _b64url(secrets.token_bytes(16))

    asyncio.run(_set_debug_redirect(add=True))
    try:
        authorize_url = f"{API}/oauth/authorize?" + urlencode(
            {
                "response_type": "code",
                "client_id": CLIENT_ID,
                "redirect_uri": CB_URL,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                "scope": "read",
            }
        )

        print("\nOpening your browser to log in...\n")
        print("If it doesn't open, paste this URL into your browser:\n")
        print(f"  {authorize_url}\n")
        webbrowser.open(authorize_url)
        print(f"Waiting for the login redirect on {CB_URL} ...\n")

        cb = _catch_callback()

        if "error" in cb:
            print(f"Authorization failed: {cb['error']} - {cb.get('error_description', '')}")
            return
        if cb.get("state") != state:
            print("State mismatch - aborting (possible CSRF).")
            return

        with httpx.Client(base_url=API, timeout=10) as http:
            tok = http.post(
                "/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": cb["code"],
                    "redirect_uri": CB_URL,
                    "client_id": CLIENT_ID,
                    "code_verifier": verifier,
                },
            )
            if tok.status_code != 200:
                print(f"Token exchange failed ({tok.status_code}): {tok.text}")
                return
            tokens = tok.json()
            print("=== TOKENS ===")
            print(json.dumps(tokens, indent=2))

            info = http.get(
                "/oauth/userinfo",
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            print("\n=== USERINFO ===")
            print(json.dumps(info.json(), indent=2))
    finally:
        asyncio.run(_set_debug_redirect(add=False))


if __name__ == "__main__":
    main()
