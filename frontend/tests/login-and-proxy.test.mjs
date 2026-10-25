// OAuth failure messages and the backend rewrite target: real TypeScript modules, no network.
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
  vm.runInNewContext(compiled, { exports, URL, URLSearchParams }, { filename: relative });
  return exports;
}

const feedback = load("src/lib/login-feedback.ts");
const proxy = load("src/lib/backend-proxy.ts");

test("each backend OAuth failure reason maps to a fixed message", () => {
  for (const reason of ["cancelled", "expired", "failed", "unavailable"]) {
    const message = feedback.googleLoginMessage(`?google_login=${reason}`);
    assert.equal(typeof message, "string");
    assert.ok(message.length > 10);
  }
});

test("unknown or hostile reasons are ignored and never echoed", () => {
  for (const search of ["", "?other=1", "?google_login=", "?google_login=<script>alert(1)</script>",
    "?google_login=constructor", "?google_login=__proto__", "?google_login=toString"]) {
    assert.equal(feedback.googleLoginMessage(search), null, search);
  }
});

test("the reason is removed from the URL while other parts are kept", () => {
  assert.equal(feedback.withoutGoogleLogin("https://app.example.test/?google_login=failed"), "/");
  assert.equal(feedback.withoutGoogleLogin("https://app.example.test/?ref=x&google_login=expired#features"), "/?ref=x#features");
});

test("rewrite keeps the current default destination", () => {
  assert.equal(proxy.DEFAULT_BACKEND_PROXY_ORIGIN, "https://ai-document-assistant-5k8d.onrender.com");
  assert.deepEqual({ ...proxy.backendProxyRewrite({}) }, {
    source: "/api/backend/:path*",
    destination: "https://ai-document-assistant-5k8d.onrender.com/:path*",
  });
  assert.equal(proxy.backendProxyOrigin({ BACKEND_PROXY_ORIGIN: "   " }), proxy.DEFAULT_BACKEND_PROXY_ORIGIN);
});

test("rewrite override accepts only a bare http(s) origin", () => {
  assert.equal(proxy.backendProxyOrigin({ BACKEND_PROXY_ORIGIN: "https://api.example.test/" }), "https://api.example.test");
  for (const value of ["https://api.example.test/path", "ftp://api.example.test", "not a url",
    "https://user:pass@api.example.test", "https://api.example.test?x=1", "javascript:alert(1)"]) {
    assert.throws(() => proxy.backendProxyOrigin({ BACKEND_PROXY_ORIGIN: value }), /origin without a path/, value);
  }
});
