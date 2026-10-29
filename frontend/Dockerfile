# syntax=docker/dockerfile:1

# Self-hosted production image. Vercel deployments do not use this file.
# Node 22 LTS matches CI; the tag is pinned by digest for reproducible builds.
FROM node:26.12.0-trixie-slim@sha256:e556a144463cc8dc53afe8de6d2fa0db5c132fd39ffa232c17641bd40df2bf11 AS base

FROM base AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund

FROM base AS builder
WORKDIR /app
# Compiled into the build: NEXT_PUBLIC_* values are inlined into the browser
# bundle and the /api/backend rewrite destination is fixed by `next build`.
# All of them are public; never pass secrets as build arguments.
ARG BACKEND_PROXY_ORIGIN=http://ai-backend:8000
ARG NEXT_PUBLIC_API_URL=/api/backend
ARG NEXT_PUBLIC_SITE_URL
ARG NEXT_PUBLIC_GOOGLE_CLIENT_ID
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    NEXT_OUTPUT=standalone
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN if [ -z "$NEXT_PUBLIC_SITE_URL" ] || [ -z "$NEXT_PUBLIC_GOOGLE_CLIENT_ID" ]; then \
        echo "NEXT_PUBLIC_SITE_URL and NEXT_PUBLIC_GOOGLE_CLIENT_ID build arguments are required" >&2; \
        exit 1; \
    fi \
    && npm run build

FROM base AS runner
WORKDIR /app
ARG BACKEND_PROXY_ORIGIN=http://ai-backend:8000
# BACKEND_PROXY_ORIGIN is checked against the compiled rewrite at startup.
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000 \
    BACKEND_PROXY_ORIGIN=${BACKEND_PROXY_ORIGIN}

# The standalone server needs only node: drop package managers from the runtime.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
        /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
        /usr/local/bin/yarn /usr/local/bin/yarnpkg /opt/yarn-*

# Application files stay root-owned and read-only to the runtime user; only the
# Next.js cache directory is writable.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
COPY --from=builder /app/scripts/start-standalone.mjs ./start-standalone.mjs
RUN mkdir -p .next/cache && chown app:app .next/cache

USER 10001:10001

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:' + (process.env.PORT || 3000) + '/', { redirect: 'manual' }).then((r) => process.exit(r.status < 500 ? 0 : 1), () => process.exit(1))"]

CMD ["node", "start-standalone.mjs"]
