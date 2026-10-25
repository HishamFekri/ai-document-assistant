// Destination for the /api/backend/* rewrite in next.config.ts.

type Environment = Record<string, string | undefined>;

export const DEFAULT_BACKEND_PROXY_ORIGIN = "https://ai-document-assistant-5k8d.onrender.com";

// Optional override; it must be a bare http(s) origin so paths cannot be smuggled in.
export function backendProxyOrigin(env: Environment): string {
  const configured = env.BACKEND_PROXY_ORIGIN?.trim();
  if (!configured) return DEFAULT_BACKEND_PROXY_ORIGIN;
  let url: URL;
  try {
    url = new URL(configured);
  } catch {
    throw new Error("BACKEND_PROXY_ORIGIN must be an http(s) origin without a path");
  }
  if ((url.protocol !== "https:" && url.protocol !== "http:") || url.origin !== configured.replace(/\/$/, "")) {
    throw new Error("BACKEND_PROXY_ORIGIN must be an http(s) origin without a path");
  }
  return url.origin;
}

export function backendProxyRewrite(env: Environment) {
  return {
    source: "/api/backend/:path*",
    destination: `${backendProxyOrigin(env)}/:path*`,
  };
}
