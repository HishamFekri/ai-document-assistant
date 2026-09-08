// No browser, backend or provider requests. Execute the real TypeScript modules
// with the installed compiler and Node's test runner; no extra dependencies.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const policy = { supported_extensions: [".pdf", ".docx", ".xlsx", ".txt"], max_file_bytes: 50 * 1024 ** 2, max_pdf_pages: 500 };

function application(fetch = () => { throw new Error("Unexpected network request"); }) {
  const modules = new Map();
  function load(relative) {
    if (modules.has(relative)) return modules.get(relative);
    const source = readFileSync(path.join(root, relative), "utf8");
    const compiled = ts.transpileModule(source, { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
    } }).outputText;
    const exports = {};
    modules.set(relative, exports);
    const dependency = (name) => name.startsWith("@/")
      ? load(`src/${name.slice(2)}.ts`) : require(name);
    vm.runInNewContext(compiled, { exports, require: dependency, process: { env: {} }, fetch, FormData, console }, { filename: relative });
    return exports;
  }
  return { api: load("src/lib/chat-api.ts"), helpers: load("src/lib/upload-policy.ts"), load };
}

test("accepts the exact 50 MiB boundary and rejects the next byte before POST", async () => {
  const methods = [];
  const { api } = application(async (url, options) => {
    methods.push(options.method ?? "GET");
    assert.equal(options.credentials, "include");
    return Response.json(url.endsWith("upload-policy") ? policy : { id: 9 });
  });
  const tiny = new File(["normal"], "normal.TXT");
  Object.defineProperty(tiny, "size", { value: policy.max_file_bytes });
  assert.equal((await api.uploadDocument("__cookie__", tiny)).id, 9);
  assert.deepEqual(methods, ["GET", "POST"]);
  await assert.rejects(api.uploadDocument("__cookie__", { name: "large.pdf", size: policy.max_file_bytes + 1 }), /maximum allowed size is 50 MB/);
  assert.deepEqual(methods, ["GET", "POST", "GET"]);
});

test("uses a changed backend limit, rejects empty and unsupported files without POST", async () => {
  const calls = [];
  const { api } = application(async (url, options) => {
    calls.push(options.method ?? "GET");
    return Response.json({ ...policy, max_file_bytes: 1024 ** 2 });
  });
  for (const [file, message] of [
    [{ name: "large.txt", size: 1024 ** 2 + 1 }, /1 MB/],
    [{ name: "empty.pdf", size: 0 }, /empty/],
    [{ name: "malware.exe", size: 10 }, /Unsupported file type/],
    [{ name: "no-extension", size: 10 }, /Unsupported file type/],
  ]) await assert.rejects(api.uploadDocument("__cookie__", file), message);
  assert.deepEqual(calls, ["GET", "GET", "GET", "GET"]);
});

test("renders supported types, configured file size and PDF pages before upload", () => {
  const { load } = application();
  const Guidance = load("src/components/documents/UploadGuidance.tsx").default;
  const html = renderToStaticMarkup(React.createElement(Guidance, { policy }));
  assert.match(html, /Supported: PDF, DOCX, XLSX, TXT/);
  assert.match(html, /Maximum file size: 50 MB/);
  assert.match(html, /PDFs: up to 500 pages/);
  assert.doesNotMatch(html, /Datalab|compression|chunk/);
  const changed = renderToStaticMarkup(React.createElement(Guidance, { policy: { ...policy, max_pdf_pages: 12 } }));
  assert.match(changed, /up to 12 pages/);
  assert.match(renderToStaticMarkup(React.createElement(Guidance, { policy: null, error: true })), /Refresh the page/);
});

test("PDF page rejection preserves the current server limit in the upload error", async () => {
  const { api } = application(async (url) => url.endsWith("upload-policy")
    ? Response.json(policy)
    : Response.json({ code: "pdf_pages", detail: "This PDF has too many pages. The maximum allowed is 400 pages." }, { status: 413 }));
  await assert.rejects(api.uploadDocument("__cookie__", new File(["small"], "large.pdf")), /maximum allowed is 400 pages/);
});

test("parser secrets, proxy HTML and malformed details never become upload messages", async () => {
  const { helpers } = application();
  for (const response of [
    Response.json({ code: "pdf_pages", detail: "secret /internal/path" }, { status: 413 }),
    Response.json({ code: "invalid_pdf", detail: "secret /internal/path" }, { status: 400 }),
    Response.json({ detail: { msg: "secret /internal/path" } }, { status: 500 }),
    new Response("<html>secret proxy</html>", { status: 413 }),
    Response.json(null, { status: 500 }),
  ]) assert.doesNotMatch(await helpers.uploadError(response), /secret|internal|<html>/);
  assert.match(await helpers.uploadError(Response.json({ code: "storage_quota" }, { status: 429 })), /Delete documents/);
});

test("unavailable or invalid policy blocks POST with a safe error", async () => {
  for (const reply of [() => Response.json({ max_file_bytes: -1 }), () => new Response("private proxy", { status: 503 }), () => { throw new Error("private network"); }]) {
    const { api } = application(async (url, options) => {
      assert.notEqual(options.method, "POST");
      return reply();
    });
    await assert.rejects(api.uploadDocument("__cookie__", new File(["valid"], "normal.txt")), /^Error: Upload limits are unavailable\. Please try again shortly\.$/);
  }
});
