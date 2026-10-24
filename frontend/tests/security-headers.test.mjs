// Security header policy: executes the real TypeScript module; no network.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function load(relative) {
  const source = readFileSync(path.join(root, relative), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports, URL }, { filename: relative });
  return exports;
}

const policy = load("src/lib/security-headers.ts");
const byName = (headers) => Object.fromEntries(headers.map(({ key, value }) => [key.toLowerCase(), value]));
const directives = (csp) => Object.fromEntries(csp.split("; ").map((part) => {
  const [name, ...values] = part.split(" ");
  return [name, values];
}));

test("baseline headers protect against sniffing, framing and leaking referrers", () => {
  const headers = byName(policy.securityHeaders({}));
  assert.equal(headers["x-content-type-options"], "nosniff");
  assert.equal(headers["referrer-policy"], "strict-origin-when-cross-origin");
  assert.equal(headers["x-frame-options"], "DENY");
  assert.equal(headers["content-security-policy"], "frame-ancestors 'none'");
  assert.match(headers["permissions-policy"], /camera=\(\), microphone=\(\), geolocation=\(\)/);
});

test("full content security policy is report-only, never enforced", () => {
  const headers = byName(policy.securityHeaders({}));
  assert.ok(headers["content-security-policy-report-only"]);
  assert.doesNotMatch(headers["content-security-policy"], /script-src|default-src/);
});

test("HSTS is sent only for Vercel production", () => {
  for (const env of [{}, { VERCEL_ENV: "preview" }, { VERCEL_ENV: "development" }, { NODE_ENV: "production" }]) {
    assert.equal(byName(policy.securityHeaders(env))["strict-transport-security"], undefined);
  }
  assert.equal(byName(policy.securityHeaders({ VERCEL_ENV: "production" }))["strict-transport-security"], "max-age=63072000");
});

test("policy allows Google Identity, the API origin, blob images and Google avatars", () => {
  const csp = directives(policy.contentSecurityPolicy({ NEXT_PUBLIC_API_URL: "https://api.example.test/" }));
  assert.ok(csp["script-src"].includes("https://accounts.google.com"));
  assert.ok(csp["frame-src"].includes("https://accounts.google.com"));
  assert.ok(csp["connect-src"].includes("https://api.example.test"));
  assert.ok(csp["img-src"].includes("https://api.example.test"));
  assert.ok(csp["img-src"].includes("blob:"));
  assert.ok(csp["img-src"].includes("https://*.googleusercontent.com"));
  assert.deepEqual(csp["object-src"], ["'none'"]);
  assert.deepEqual(csp["frame-ancestors"], ["'none'"]);
});

test("API origin mirrors the client default and treats relative proxy URLs as same-origin", () => {
  assert.equal(policy.apiOrigin({}), "http://localhost:8000");
  assert.equal(policy.apiOrigin({ NEXT_PUBLIC_API_URL: "https://api.example.test/base" }), "https://api.example.test");
  assert.equal(policy.apiOrigin({ NEXT_PUBLIC_API_URL: "/api/backend" }), null);
  assert.equal(policy.apiOrigin({ NEXT_PUBLIC_API_URL: "javascript:alert(1)" }), null);
  const csp = directives(policy.contentSecurityPolicy({ NEXT_PUBLIC_API_URL: "/api/backend" }));
  assert.ok(!csp["connect-src"].some((source) => source.includes("backend")));
});

test("Vercel toolbar origin is allowed on previews only", () => {
  assert.doesNotMatch(policy.contentSecurityPolicy({ VERCEL_ENV: "production" }), /vercel\.live/);
  assert.match(policy.contentSecurityPolicy({ VERCEL_ENV: "preview" }), /vercel\.live/);
});

test("header values are single-line and safe to emit", () => {
  for (const { value } of policy.securityHeaders({ VERCEL_ENV: "preview", NEXT_PUBLIC_API_URL: "https://api.example.test" })) {
    assert.doesNotMatch(value, /[\r\n]/);
  }
});
