// Build output mode and self-hosted proxy limits for next.config.ts.

type Environment = Record<string, string | undefined>;

// The self-hosted Next.js server proxies /api/backend itself (on Vercel the
// platform does). It buffers request bodies up to this size: anything beyond is
// truncated, so it must exceed the backend's largest request (50 MiB upload +
// 1 MiB multipart overhead) and leave the 413 decision to the backend.
export const STANDALONE_PROXY_MAX_BODY_BYTES = 52 * 1024 * 1024;
// Idle socket timeout for proxied requests (Next.js default: 30s). Must exceed
// the backend's slowest silent response, e.g. LLM_TIMEOUT_SECONDS=90.
export const STANDALONE_PROXY_TIMEOUT_MS = 180_000;

// The Docker image builds with NEXT_OUTPUT=standalone. Unset keeps the default
// output, so Vercel builds are unchanged.
export function buildOutput(env: Environment): "standalone" | undefined {
  const configured = env.NEXT_OUTPUT?.trim();
  if (!configured) return undefined;
  if (configured !== "standalone") {
    throw new Error("NEXT_OUTPUT must be standalone or unset");
  }
  return "standalone";
}

// Only standalone builds get proxy overrides; other builds keep every default.
export function standaloneConfig(env: Environment) {
  if (!buildOutput(env)) return {};
  return {
    output: "standalone" as const,
    experimental: {
      proxyClientMaxBodySize: STANDALONE_PROXY_MAX_BODY_BYTES,
      proxyTimeout: STANDALONE_PROXY_TIMEOUT_MS,
    },
  };
}
