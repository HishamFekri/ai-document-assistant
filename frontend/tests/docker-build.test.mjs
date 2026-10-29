// Standalone output switch and the Docker entry point's backend-origin guard; no network.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
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
  vm.runInNewContext(compiled, { exports }, { filename: relative });
  return exports;
}

const output = load("src/lib/build-output.ts");

test("default output is unchanged unless the Docker build opts in", () => {
  assert.equal(output.buildOutput({}), undefined);
  assert.equal(output.buildOutput({ NEXT_OUTPUT: "  " }), undefined);
  assert.equal(output.buildOutput({ NEXT_OUTPUT: "standalone" }), "standalone");
  for (const value of ["export", "Standalone", "standalone,export"]) {
    assert.throws(() => output.buildOutput({ NEXT_OUTPUT: value }), /standalone or unset/, value);
  }
});

test("only standalone builds change config, raising the self-hosted proxy limits", () => {
  // Vercel/default builds: no output, no experimental overrides at all.
  assert.deepEqual({ ...output.standaloneConfig({}) }, {});
  const config = output.standaloneConfig({ NEXT_OUTPUT: "standalone" });
  assert.equal(config.output, "standalone");
  // Above the backend's largest request: 50 MiB upload + 1 MiB multipart overhead.
  assert.ok(config.experimental.proxyClientMaxBodySize > 51 * 1024 * 1024);
  // Above Next.js' 30s default and the backend's 90s LLM timeout.
  assert.ok(config.experimental.proxyTimeout > 90_000);
});

// A fake standalone layout: the real routes-manifest shape and a stub server.js.
function standalone(destination) {
  const directory = mkdtempSync(path.join(tmpdir(), "standalone-"));
  mkdirSync(path.join(directory, ".next"));
  writeFileSync(path.join(directory, ".next", "routes-manifest.json"), JSON.stringify({
    rewrites: { beforeFiles: [], afterFiles: [{ source: "/api/backend/:path*", destination }], fallback: [] },
  }));
  writeFileSync(path.join(directory, "server.js"), "console.log('server started');\n");
  copyFileSync(path.join(root, "scripts", "start-standalone.mjs"), path.join(directory, "start-standalone.mjs"));
  return directory;
}

function start(directory, origin) {
  const env = { PATH: process.env.PATH, SYSTEMROOT: process.env.SYSTEMROOT };
  if (origin !== undefined) env.BACKEND_PROXY_ORIGIN = origin;
  return spawnSync(process.execPath, ["start-standalone.mjs"], { cwd: directory, env, encoding: "utf8" });
}

test("entry point starts only when the runtime origin matches the build", (t) => {
  const directory = standalone("http://ai-backend:8000/:path*");
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  for (const origin of [undefined, "", "http://ai-backend:8000", "http://ai-backend:8000/"]) {
    const result = start(directory, origin);
    assert.equal(result.status, 0, `${origin}: ${result.stderr}`);
    assert.match(result.stdout, /server started/);
  }
  for (const origin of ["http://other:8000", "https://ai-backend:8000", "http://ai-backend:9000", "not a url"]) {
    const result = start(directory, origin);
    assert.equal(result.status, 1, origin);
    assert.doesNotMatch(result.stdout, /server started/);
    assert.match(result.stderr, /start-standalone:/);
  }
});

test("entry point refuses a build without the backend rewrite", (t) => {
  const directory = standalone("http://ai-backend:8000/:path*");
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  writeFileSync(path.join(directory, ".next", "routes-manifest.json"), JSON.stringify({ rewrites: [] }));
  const result = start(directory, "http://ai-backend:8000");
  assert.equal(result.status, 1);
  assert.match(result.stderr, /no \/api\/backend\/:path\* rewrite/);
});
