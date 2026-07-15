# OAuth 2.1 (Authorization Code + PKCE)

Mock of Wakilni's future authorization server. The production client is an MCP
server acting on behalf of users. All PKCE-only, public clients (no client
secret).

> The existing `/auth/*` endpoints (register/login/refresh/logout/me) are a
> **separate** surface for direct developer testing and are unaffected by this.
> `/oauth/*` is what the MCP uses.

## Endpoints

| Method | Path | Body | Purpose |
|---|---|---|---|
| GET | `/.well-known/oauth-authorization-server` | — | RFC 8414 discovery JSON |
| GET | `/oauth/authorize` | query params | Renders login form |
| POST | `/oauth/authorize/login` | form-encoded | Verifies creds, issues code, redirects |
| POST | `/oauth/token` | form-encoded | `authorization_code` + `refresh_token` grants |
| GET | `/oauth/userinfo` | — (Bearer) | Current user claims from DB |
| POST | `/oauth/revoke` | form-encoded | Revoke a refresh token (always 200) |

Discovery is at the **site root** (not under `/oauth`) as RFC 8414 requires.

## The flow

The login UI is **hosted by the frontend**, not this server. `/oauth/authorize`
validates the request then redirects the browser to the frontend login page
(`OAUTH_LOGIN_URL`, default `http://localhost:5173/oauth/login`), carrying the
request context as query params. The frontend renders its own form and posts the
fields back to `/oauth/authorize/login`.

```
browser                    authorization server               frontend login page
  │  GET /oauth/authorize (PKCE challenge, state) ─────►
  │  ◄── 302 OAUTH_LOGIN_URL?client_id&redirect_uri&code_challenge
  │        &code_challenge_method&state&scope&client_name ──►  (renders form)
  │  POST /oauth/authorize/login (form: params + email + password) ─►
  │  ◄── 302 redirect_uri?code=…&state=…   (bad creds ◄── 302 back to
  │                                          OAUTH_LOGIN_URL?…&error&email)
  │  POST /oauth/token (code + code_verifier) ─────────►
  │  ◄── {access_token, refresh_token, …} ─────────────
  │  GET /oauth/userinfo (Bearer access_token) ────────►
  │  POST /oauth/token (grant_type=refresh_token) ─────►  (rotates)
```

## Contracts

### GET `/oauth/authorize`
Query params: `response_type=code` (required), `client_id` (required),
`redirect_uri` (required, exact match), `code_challenge` (required),
`code_challenge_method=S256` (required — `plain` rejected), `state` (required,
echoed back), `scope` (optional).

- Bad `client_id`/`redirect_uri` → **400** `{error, error_description}` (no
  redirect — an unvalidated redirect target is never trusted).
- Any other invalid param (with a valid redirect_uri) → **302** to
  `redirect_uri?error=…&error_description=…&state=…`.
- Valid → **302** to `OAUTH_LOGIN_URL` with the request context as query params:
  `response_type, client_id, redirect_uri, code_challenge,
  code_challenge_method, state, scope`, plus `client_name` (for display).

### POST `/oauth/authorize/login`
Form-encoded (`application/x-www-form-urlencoded`): the authorize params
(`response_type, client_id, redirect_uri, code_challenge, code_challenge_method,
state, scope`) + `email` + `password`. Submit it as a **native form POST /
navigation**, not `fetch` — the server replies with browser 302 redirects.

- Bad credentials → **302** back to `OAUTH_LOGIN_URL?…&error=invalid_credentials
  &email=<email>` (frontend shows the error and pre-fills the email).
- Success → **302** `redirect_uri?code=<code>&state=<state>`. The code is a
  32-byte URL-safe random string, single-use, expires in 60s.

### POST `/oauth/token`
Form-encoded (RFC 6749). `Content-Type: application/x-www-form-urlencoded`.

**grant_type=authorization_code** — `code`, `redirect_uri`, `client_id`,
`code_verifier`. Validates: code exists / not expired / unused, `client_id`
match, exact `redirect_uri` match, and `BASE64URL(SHA256(code_verifier))` ==
stored `code_challenge` (verifier 43–128 chars). Marks the code used.

**grant_type=refresh_token** — `refresh_token`, `client_id`. Looks up by hash,
rotates: revokes the presented token, issues a new pair in the same family, and
sets `replaced_by` on the old row.

Success (both) → **200**:
```json
{ "access_token": "<jwt>", "refresh_token": "<opaque>",
  "token_type": "bearer", "expires_in": 900, "scope": "read" }
```
Errors → RFC 6749 shape `{ "error": "...", "error_description": "..." }`.

### GET `/oauth/userinfo`
`Authorization: Bearer <access_token>`. Returns fresh from the DB (not JWT
claims):
```json
{ "sub": "1", "email": "u@x.com", "role": "customer", "full_name": "U" }
```

### POST `/oauth/revoke`
Form fields: `token`, `token_type_hint` (optional). Revokes the matching refresh
token and its whole family. **Always 200**, even for unknown tokens (RFC 7009).

## Security rules enforced

- **S256 only** — `code_challenge_method=plain` is rejected.
- **Exact redirect_uri match** — no wildcards, no substrings.
- **Single-use codes** — a second use is a hard **401** *and* revokes the token
  family issued from that code (interception signal).
- **Refresh tokens stored as SHA-256 hashes**, never plaintext.
- **Rotation + reuse detection** — presenting a rotated-out (revoked +
  replaced) refresh token revokes the entire family and returns **401**.
- **`state`** is echoed on redirect but never persisted — it's the client's CSRF
  check, not the server's.

## Seeded client

| client_id | redirect_uri | auth method |
|---|---|---|
| `wakilni-mcp` | `http://localhost:5173/callback` | none (public, PKCE) |

Change it with a new migration or SQL against `oauth_clients`.

## Manual test with curl

Start the server and create a user:
```bash
uv run alembic upgrade head
uv run uvicorn app.main:app --reload   # in another terminal
curl -X POST localhost:8000/auth/register -H 'Content-Type: application/json' \
  -d '{"email":"u@example.com","password":"supersecret1"}'
```

**1. Generate a PKCE pair:**
```bash
VERIFIER=$(python -c "import secrets;print(secrets.token_urlsafe(64)[:96])")
CHALLENGE=$(python -c "import hashlib,base64,sys;print(base64.urlsafe_b64encode(hashlib.sha256(sys.argv[1].encode()).digest()).rstrip(b'=').decode())" "$VERIFIER")
```

**2. Get an authorization code** (skips the browser form by posting to the
login endpoint directly):
```bash
CODE=$(curl -s -i -X POST localhost:8000/oauth/authorize/login \
  -d response_type=code -d client_id=wakilni-mcp \
  -d redirect_uri=http://localhost:5173/callback \
  -d code_challenge=$CHALLENGE -d code_challenge_method=S256 \
  -d state=xyz -d scope=read \
  -d email=u@example.com -d password=supersecret1 \
  | grep -i '^location:' | sed -E 's/.*code=([^&]+).*/\1/' | tr -d '\r')
echo "code=$CODE"
```

**3. Exchange the code for tokens:**
```bash
curl -s -X POST localhost:8000/oauth/token \
  -d grant_type=authorization_code -d code=$CODE \
  -d redirect_uri=http://localhost:5173/callback \
  -d client_id=wakilni-mcp -d code_verifier=$VERIFIER
```

**4. Call userinfo** (paste the access_token):
```bash
curl -s localhost:8000/oauth/userinfo -H "Authorization: Bearer <ACCESS_TOKEN>"
```

**5. Refresh** (paste the refresh_token):
```bash
curl -s -X POST localhost:8000/oauth/token \
  -d grant_type=refresh_token -d refresh_token=<REFRESH_TOKEN> \
  -d client_id=wakilni-mcp
```

**6. Revoke:**
```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/oauth/revoke \
  -d token=<REFRESH_TOKEN> -d token_type_hint=refresh_token
```

To try the real browser flow, open the authorize URL in a browser:
```
http://localhost:8000/oauth/authorize?response_type=code&client_id=wakilni-mcp&redirect_uri=http://localhost:5173/callback&code_challenge=<CHALLENGE>&code_challenge_method=S256&state=xyz&scope=read
```
You'll get the login form; submitting it redirects to the callback with `?code=…&state=…`.
