// Docker entry point for the standalone server.
// The /api/backend rewrite is compiled into the build, so BACKEND_PROXY_ORIGIN
// cannot be changed at runtime. Refuse to start when the runtime value differs
// from the compiled one instead of silently proxying somewhere else.
import { readFileSync } from "node:fs";

const SOURCE = "/api/backend/:path*";

function fail(message) {
  console.error(`start-standalone: ${message}`);
  process.exit(1);
}

const manifest = JSON.parse(readFileSync(new URL("./.next/routes-manifest.json", import.meta.url), "utf8"));
const { rewrites } = manifest;
const rules = Array.isArray(rewrites)
  ? rewrites
  : [...(rewrites?.beforeFiles ?? []), ...(rewrites?.afterFiles ?? []), ...(rewrites?.fallback ?? [])];
const rewrite = rules.find((rule) => rule.source === SOURCE);
if (!rewrite) fail(`no ${SOURCE} rewrite in the build`);

const compiled = new URL(rewrite.destination).origin;
const configured = process.env.BACKEND_PROXY_ORIGIN?.trim();
if (configured) {
  let origin;
  try {
    origin = new URL(configured).origin;
  } catch {
    fail("BACKEND_PROXY_ORIGIN is not a valid origin");
  }
  if (origin !== compiled) {
    fail(`BACKEND_PROXY_ORIGIN (${origin}) differs from the build (${compiled}); rebuild the image to change it`);
  }
}

await import("./server.js");
