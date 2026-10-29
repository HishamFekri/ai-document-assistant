# Self-hosting the frontend with Docker

`frontend/Dockerfile` builds a standalone Next.js server for self-hosting. Vercel
does not use it: Vercel builds leave `NEXT_OUTPUT` unset, so their output,
proxy and configuration are unchanged.

## Build arguments

Every value below is compiled into the build. `NEXT_PUBLIC_*` values are inlined
into the JavaScript sent to browsers, and `next build` fixes the `/api/backend`
rewrite destination, so changing any of them requires a rebuild. All of them are
public. Never pass secrets as build arguments.

| Argument | Default | Notes |
| --- | --- | --- |
| `NEXT_PUBLIC_SITE_URL` | required | `https://ai.hishamjouda.com` (metadata and Open Graph URLs) |
| `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | required | public OAuth client ID; ships to every browser |
| `NEXT_PUBLIC_API_URL` | `/api/backend` | keep relative so the browser stays same-origin |
| `BACKEND_PROXY_ORIGIN` | `http://ai-backend:8000` | backend origin on the Docker network; bare origin only |

The build fails if either required argument is missing.

```bash
docker build -t ai-frontend \
  --build-arg NEXT_PUBLIC_SITE_URL=https://ai.hishamjouda.com \
  --build-arg NEXT_PUBLIC_GOOGLE_CLIENT_ID="$GOOGLE_CLIENT_ID" \
  frontend
```

## Runtime

- The server runs as UID/GID 10001 and listens on port 3000 (`PORT`, `HOSTNAME=0.0.0.0`).
- The application files are root-owned. Only `/app/.next/cache` is writable, so
  the container can also run with `read_only: true` and a tmpfs mounted at
  `/app/.next/cache`.
- npm, npx, corepack and yarn are removed from the runtime image.
- `BACKEND_PROXY_ORIGIN` is set in the image. If it is overridden at runtime with a
  value that differs from the build, the container refuses to start instead of
  proxying somewhere else.
- A healthcheck requests `/` inside the container.

```yaml
services:
  frontend:
    build:
      context: ./frontend
      args:
        NEXT_PUBLIC_SITE_URL: https://ai.hishamjouda.com
        NEXT_PUBLIC_GOOGLE_CLIENT_ID: ${GOOGLE_CLIENT_ID}
    container_name: ai-frontend
    restart: unless-stopped
    read_only: true
    tmpfs:
      - /app/.next/cache
      - /tmp
    security_opt:
      - no-new-privileges:true
    networks: [proxy]
    # No published ports: the reverse proxy reaches ai-frontend:3000 on the network.

networks:
  proxy:
    external: true
```

## Backend proxy limits

On Vercel, the platform proxies `/api/backend/*`. When self-hosted, the Next.js
server proxies it, and its defaults are too small for this backend. Standalone
builds raise them:

- **Request body:** 52 MiB instead of 10 MB. Larger bodies would be silently
  truncated. This is above the backend's 50 MiB upload plus 1 MiB multipart
  overhead, so the backend still returns its own 413. If you raise
  `MAX_UPLOAD_SIZE_MB` on the backend, raise `STANDALONE_PROXY_MAX_BODY_BYTES`
  in `src/lib/build-output.ts` too.
- **Idle timeout:** 180 s instead of 30 s. This is above the backend's 90 s
  `LLM_TIMEOUT_SECONDS`.

Proxied request bodies are buffered in memory, up to the limit above, per
upload.

## Reverse proxy (Caddy)

- Proxy the site to `ai-frontend:3000` only. Do not publish the backend port or
  serve the uploads directory.
- Add `Strict-Transport-Security` in Caddy. The app sends HSTS only on Vercel
  production.
- If Caddy's own timeouts or request-body limits are configured, keep them at or
  above the limits above.
- The backend sees the frontend container as the client for every proxied
  request. Keep `AUTH_CLIENT_IP_SOURCE` at its default (`peer`) unless the proxy
  chain has been verified (see `backend/docs/authentication-sessions.md`).
