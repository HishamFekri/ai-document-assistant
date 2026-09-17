# Authentication and sessions (Batch 10)

This batch preserves Google OAuth, the direct Google ID-token endpoint, local
users, stateless JWTs, cookie/Bearer authentication and ownership checks. It needs
no migration, refresh-token system, session database or secret rotation.

## Login and protected requests

The browser navigates to `/auth/google/start`. The backend creates a random
`secrets.token_urlsafe(32)` state, sets a ten-minute HttpOnly state cookie, and
redirects to Google with that state and the configured callback URL. The GET
callback compares returned state with the cookie using `compare_digest`, exchanges
the code, verifies the Google ID token, finds/creates the local user, and sets the
auth cookie before redirecting to `${FRONTEND_URL}/chat`.

The direct `POST /auth/google` flow verifies its supplied ID token through the
same Google verifier and returns the existing user response plus the same cookie.
Google's supported `verify_oauth2_token` mechanism still verifies signature and
the explicitly configured `GOOGLE_CLIENT_ID` audience. Required account ID/email
checks and the existing explicit-unverified-email rejection remain active. There
is no unverified-payload fallback.

Protected requests prefer an explicit HTTPBearer token over the `access_token`
cookie. JWT decoding verifies the signature using the configured algorithm only
(`HS256` by default), requires `exp` and `sub`, validates expiry and requires an
integer expiration. `sub` must be a canonical positive decimal string in the
existing PostgreSQL/Batch 8 user-ID range (1 through 2,147,483,647). The decoder
checks `iat` as an integer when present and retains the JWT library's time checks.
Issued tokens continue to contain `sub`, `email`, `iat` and `exp`; email and issued-at
are not newly required because route identity depends on `sub` and expiration.
See [PyJWT's required-claim documentation](https://pyjwt.readthedocs.io/en/stable/usage.html#requiring-presence-of-claims).

After validation, Batch 8 admission consumes the appropriate verified-user rate
unit and the dependency loads the user from the database. Unknown users and invalid,
expired or malformed tokens produce the same safe 401 `Invalid access token`.
Missing credentials retain the 401 `Authentication required` response. Google
credential failures use a safe 401; state failures remain 400. Provider/JWT error
text, token payloads and secrets are neither returned nor logged by these handlers.

## Configuration and lifetime

`app/services/auth_config.py` centralizes the nonsecret session/cookie policy and
validates it when authentication configuration loads, including API startup.
Configuration is cached per process; restart API/worker processes after changes.
All instances must share compatible settings and signing keys.

| Setting | Default / contract |
| --- | --- |
| `ENVIRONMENT` | `development`; accepts `development`, `test`, `staging`, `production`, ignoring case/surrounding whitespace; rejects unknown/blank values |
| `COOKIE_SECURE` | Optional; true for production/staging, false for development/test; only `true`/`false` supported; production/staging cannot disable it |
| `COOKIE_SAMESITE` | `lax`; `strict` and `none` are explicit supported choices; `none` requires Secure |
| `JWT_EXPIRE_MINUTES` | 10,080 (seven days); positive integer, maximum 525,600; single source for JWT duration and auth-cookie Max-Age |
| `FRONTEND_URL` | `http://localhost:3000` locally; browser frontend origin and post-login redirect target; production/staging requires HTTPS |
| `FRONTEND_URLS` | Explicit CORS/origin allowlist including `FRONTEND_URL`; existing localhost defaults in development, defaults to `FRONTEND_URL` in production; wildcard/empty lists rejected |
| `GOOGLE_REDIRECT_URI` | Exact registered, browser-facing callback URL; HTTP(S) locally, HTTPS when configured in production/staging |
| `GOOGLE_CLIENT_ID` | Existing configured Google audience, required |
| `GOOGLE_CLIENT_SECRET` | Required for redirect code exchange; direct ID-token flow does not use it |
| `JWT_SECRET_KEY` / `JWT_ALGORITHM` | Existing environment-loaded signing configuration, unchanged; no automatic rotation |

Redirect login requires `GOOGLE_REDIRECT_URI` and `GOOGLE_CLIENT_SECRET`; a missing
callback setting leaves `/auth/google/start` safely unavailable rather than guessing
a URL. Keeping the callback optional at startup preserves direct-token-only use.
The example environment now documents both redirect settings and `FRONTEND_URL`.
Do not copy placeholder credentials into production.

The default JWT lifetime and cookie Max-Age are both **604,800 seconds**. Creation
uses one configured duration; cookies are issued immediately afterward. The auth
cookie uses Max-Age without a separately configured Expires date. Existing valid
issued tokens remain compatible and are not shortened or re-signed. Changing the
lifetime changes new tokens; it does not revoke existing JWTs. Requiring `exp`/`sub`
and canonical types intentionally rejects malformed or non-expiring legacy tokens.

Both cookies remain **HttpOnly, host-only (no Domain), Path `/`**. Their Secure
flag comes exclusively from validated configuration, never request scheme or
forwarded headers. The state cookie always uses **SameSite=Lax**, even if the auth
cookie is configured differently. It is removed on successful callbacks, valid
cancellations, handled callback errors, and logout. Missing/mismatched/non-ASCII
state is rejected before any code exchange. A callback without valid state cannot
claim cancellation success. Admission failures happen before callback handling and
can leave the original state cookie until its ten-minute expiry.

## Deployment topology and HTTPS

The checked local frontend configuration uses `http://localhost:8000` as its API
origin; frontend development normally runs at `http://localhost:3000`. These are
different origins but the same site. Use matching hostnames throughout local
login/API/callback URLs; mixing `localhost` and `127.0.0.1` loses host-only cookies.
Local defaults intentionally allow HTTP with Secure disabled.

The repository also contains a Next.js `/api/backend/:path*` rewrite to the
existing Render backend. The deployed browser-facing frontend origin and selected
API layout are not established by the repository, so this batch does not change
them or guess a cross-site cookie policy.

Supported deployment arrangements:

1. **Same-site HTTPS frontend and API**, such as `https://app.example.com` and
   `https://api.example.com`: use production configuration and SameSite=Lax. CORS
   must allow the exact frontend origin with credentials. Register the API's
   public `/auth/google/callback` URL with Google.
2. **Existing same-origin frontend proxy**: set `NEXT_PUBLIC_API_URL=/api/backend`
   and use that same browser-facing host/path for Google start, callback and all
   authenticated API calls. Register a callback such as
   `https://app.example.com/api/backend/auth/google/callback`. The rewrite must
   preserve Set-Cookie responses. Do not start login on the direct backend hostname
   and then expect its host-only cookie on the frontend proxy hostname.
3. **Truly cross-site direct frontend/API hosts**: explicitly use
   `COOKIE_SAMESITE=none` with `COOKIE_SECURE=true`, exact credentialed CORS origins,
   and the direct API's HTTPS callback. Browser third-party-cookie restrictions
   can still prevent this layout from working. A different auth architecture is
   outside this batch; the existing same-origin proxy is available for evaluation.

Lax permits the safe top-level GET navigation from Google that returns the state
cookie. It does not permit cross-site credentialed fetches. Strict is an explicit
auth-cookie choice with different external-navigation behavior, not the recommended
default for this flow. None requires Secure. These browser rules are described in
the [Set-Cookie reference](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie).

Production HTTPS terminates at the trusted ingress/proxy. All browser-facing
frontend/API/callback URLs must use HTTPS even if proxy-to-Uvicorn traffic uses
HTTP on a private network. Secure cookies do not depend on Uvicorn seeing an HTTPS
scheme. Cookie Domain is never broadened to bridge hosts.

The Docker command retains `--no-proxy-headers`. The app does not interpret
`X-Forwarded-Proto`, `X-Forwarded-Host`, `X-Forwarded-For` or `Forwarded` for cookie
security or redirect URLs. If operators need client-IP restoration for Batch 8
behind a proxy, enable server-layer forwarding only for an explicit trusted proxy
IP/CIDR allowlist, have that proxy overwrite client-supplied forwarding headers,
and prevent direct access to the origin. Never use an unrestricted trust wildcard.
Without trusted restoration, auth admission conservatively shares the proxy peer's
IP bucket. This batch does not alter that Batch 8 boundary or the Docker command.

CORS retains credentials with explicit origins; wildcard origins cannot be
configured. Existing mutating-request Origin checks remain in place. This batch
does not add a general CSRF/CORS redesign.

## Logout and remaining limits

`POST /auth/logout` remains idempotent and does not require a valid JWT, so an
expired session can still be cleared. After Batch 8 authentication admission, it
expires both cookies with Max-Age=0 and an immediate Expires value, using the same
name, host-only scope, Path, Secure, HttpOnly and relevant SameSite settings as
issuance. Authentication endpoints retain the shared **30 starts/minute/peer IP**
limit; direct login, redirect start/callback and logout have no alternative bypass.
Redis failure remains a safe 503, and a rate denial remains 429 with Retry-After.

Previously both frontend logout handlers started a request and redirected
immediately, including on HTTP/backend or network failure. Now the shared logout
helper waits for an OK response before clearing local user/document/chat state
and using each screen's existing redirect. A network error or non-OK response,
including 401/403/429/503, shows: "Could not confirm sign out. You may still be
signed in. Please try again." It retains current state and does not claim confirmed
server logout. Proxy bodies and exception details are not displayed. Existing
authentication-expiry handling remains unchanged.

Logout removes this browser's cookies; it does **not** revoke a copied Bearer JWT
or sessions on other devices. Stateless tokens remain valid until their original
expiration. A lost logout response is ambiguous even if the server sent deletion;
retry is safe. Browser cookie policy and proxy routing must permit Set-Cookie for
issuance/deletion to work. Concurrent tabs/in-flight work are not redesigned here.

## Safe validation

```text
python -B tests/test_auth_hardening.py
python -B tests/test_resource_admission.py
python -B tests/test_database_safety.py
```

The authentication suite uses real JWT decoding and HTTP/cookie handlers with
synthetic users and blocked external services. It also executes the three existing
`test_auth.py` tests against an isolated session adapter. It does not replace
Batch 1's guarded fixtures or run their live database integration path. Google
verification/code exchange is mocked; no Google OAuth, production database,
Redis, migration or secret-rotation operation is performed.

Frontend validation uses installed dependencies:

```text
node --test tests/logout.test.mjs
node node_modules/typescript/bin/tsc --noEmit --incremental false
npm run build
npm run lint
```

The logout tests execute both actual frontend consumers with isolated hooks and
mocked fetch, checking delayed success, state clearing, redirects, HTTP failures
and network failures. No real browser login is performed. Compare lint against
the existing 4-error / 20-warning baseline; unrelated findings are out of scope.
