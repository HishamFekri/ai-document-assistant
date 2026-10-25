// Ready-with-warnings display for documents whose extracted images could not all be
// stored. Executes the real TypeScript modules; no browser, backend or network.
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

function loader() {
  const modules = new Map();
  function load(relative) {
    if (modules.has(relative)) return modules.get(relative);
    const compiled = ts.transpileModule(readFileSync(path.join(root, relative), "utf8"), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
    } }).outputText;
    const exports = {};
    modules.set(relative, exports);
    const dependency = (name) => name.startsWith("@/") ? load(`src/${name.slice(2)}.ts`) : require(name);
    vm.runInNewContext(compiled, { exports, require: dependency, process: { env: {} },
      fetch: () => { throw new Error("Unexpected network request"); }, console }, { filename: relative });
    return exports;
  }
  return load;
}

const load = loader();
const warnings = load("src/lib/document-warnings.ts");

const document = (extra = {}) => ({
  id: 4, filename: "report.pdf", file_type: "pdf", pages_count: 26,
  processing_status: "ready", processing_stage: "ready", processing_progress: 100,
  processing_error: null, created_at: "2026-10-25T12:00:00", ...extra,
});

const WARNING_3 = "Ready with warnings: 3 images could not be processed. Some information may be missing from AI responses.";
const WARNING_1 = "Ready with warnings: 1 image could not be processed. Some information may be missing from AI responses.";

test("warning text and label for ready documents with missing images", () => {
  assert.equal(warnings.missingImageWarning(document({ missing_image_count: 3 })), WARNING_3);
  assert.equal(warnings.missingImageWarning(document({ missing_image_count: 1 })), WARNING_1);
  assert.equal(warnings.readyLabel(document({ missing_image_count: 3 })), "Ready with warnings");
  assert.equal(warnings.missingImageCount(document({ missing_image_count: 23 })), 23);
});

test("complete, legacy and not-yet-ready documents show no warning", () => {
  for (const extra of [
    { missing_image_count: 0 },                       // tracked, nothing missing
    { missing_image_count: null },                    // processed before tracking existed
    {},                                               // older API response without the field
    { missing_image_count: -1 },                      // defensive: never invent a warning
    { missing_image_count: 2.5 },
    { missing_image_count: "3" },
    { processing_status: "processing", processing_progress: 40, missing_image_count: 3 },
    { processing_status: "failed", missing_image_count: 3 },
  ]) {
    assert.equal(warnings.missingImageWarning(document(extra)), null, JSON.stringify(extra));
    if ((extra.processing_status ?? "ready") === "ready") {
      assert.equal(warnings.readyLabel(document(extra)), "Ready", JSON.stringify(extra));
    }
  }
});

test("chat document row shows the warning instead of a plain Ready", () => {
  const Row = load("src/components/chat/DocumentRow.tsx").default;
  const render = (extra) => renderToStaticMarkup(React.createElement(Row, { document: document(extra), onRemove() {} }));

  const warned = render({ missing_image_count: 3 });
  assert.match(warned, /Ready with warnings/);
  assert.ok(warned.includes(WARNING_3));
  assert.doesNotMatch(warned, />Ready</);
  assert.doesNotMatch(warned, /text-emerald-600/);

  for (const extra of [{ missing_image_count: 0 }, { missing_image_count: null }, {}]) {
    const plain = render(extra);
    assert.match(plain, />Ready</);
    assert.doesNotMatch(plain, /warnings|could not be processed/);
  }
});

test("documents panel keeps warned documents openable and shows the warning", () => {
  const Panel = load("src/components/chat/UploadedDocsPanel.tsx").default;
  const html = renderToStaticMarkup(React.createElement(Panel, {
    open: true, onClose() {}, onRemove() {}, onOpenSummary() {},
    documents: [document({ id: 1, missing_image_count: 3 }), document({ id: 2, filename: "old.pdf", missing_image_count: null })],
  }));
  assert.ok(html.includes(WARNING_3));
  assert.equal(html.match(/could not be processed/g).length, 1);       // only the affected document
  assert.match(html, /Ready with warnings/);
  assert.match(html, />Ready</);                                       // the legacy document is unchanged
  // Ready-with-warnings stays interactive (summary, chat), like any ready document.
  assert.equal(html.match(/role="button"/g).length, 2);
});
