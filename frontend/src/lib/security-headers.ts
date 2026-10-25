// Response security headers used by next.config.ts.
// Pure functions of the build environment so they can be tested directly.

type Environment = Record<string, string | undefined>;

export type Header = { key: string; value: string };

export const DEFAULT_API_URL = "http://localhost:8000";

const GOOGLE_IDENTITY_ORIGIN = "https://accounts.google.com";
const GOOGLE_AVATAR_ORIGIN = "https://*.googleusercontent.com";
const VERCEL_PREVIEW_TOOLBAR_ORIGIN = "https://vercel.live";

function httpOrigin(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.origin : null;
  } catch {
    return null;
  }
}

// Mirrors the client's `NEXT_PUBLIC_API_URL ?? "http://localhost:8000"`. A relative
// URL such as /api/backend is same-origin and already covered by 'self'.
export function apiOrigin(env: Environment): string | null {
  const configured = env.NEXT_PUBLIC_API_URL ?? DEFAULT_API_URL;
  return configured.startsWith("/") ? null : httpOrigin(configured);
}

export function contentSecurityPolicy(env: Environment): string {
  const api = apiOrigin(env);
  const preview = env.VERCEL_ENV === "preview" ? [VERCEL_PREVIEW_TOOLBAR_ORIGIN] : [];
  const sources = (...values: (string | null)[]) => values.filter(Boolean).join(" ");

  const directives: [string, string][] = [
    ["default-src", "'self'"],
    // Next.js bootstrap and the theme script are inline; Google Identity loads its client script.
    ["script-src", sources("'self'", "'unsafe-inline'", GOOGLE_IDENTITY_ORIGIN, ...preview)],
    ["style-src", sources("'self'", "'unsafe-inline'", GOOGLE_IDENTITY_ORIGIN)],
    // Source images are fetched from the API and shown as blob: URLs; avatars come from Google.
    ["img-src", sources("'self'", "data:", "blob:", GOOGLE_AVATAR_ORIGIN, api, ...preview)],
    ["font-src", sources("'self'", "data:")],
    ["connect-src", sources("'self'", api, GOOGLE_IDENTITY_ORIGIN, ...preview)],
    ["frame-src", sources(GOOGLE_IDENTITY_ORIGIN, ...preview)],
    ["form-action", "'self'"],
    ["base-uri", "'self'"],
    ["object-src", "'none'"],
    ["frame-ancestors", "'none'"],
  ];
  return directives.map(([name, value]) => `${name} ${value}`).join("; ");
}

export function securityHeaders(env: Environment): Header[] {
  const headers: Header[] = [
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=(), browsing-topics=()" },
    // Clickjacking protection is enforced; the full policy is report-only until verified.
    { key: "X-Frame-Options", value: "DENY" },
    { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
    { key: "Content-Security-Policy-Report-Only", value: contentSecurityPolicy(env) },
  ];
  // Vercel production is always served over HTTPS; previews and local servers are not covered.
  if (env.VERCEL_ENV === "production") {
    headers.push({ key: "Strict-Transport-Security", value: "max-age=63072000" });
  }
  return headers;
}
