// Compact labels for source references written inside answers. Executes the real
// TypeScript module; no browser, backend or network.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function load(relative) {
  const compiled = ts.transpileModule(readFileSync(path.join(root, relative), "utf8"), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports }, { filename: relative });
  return exports;
}

const { citationLabel, splitCitations, rehypeCitations } = load("src/lib/citations.ts");

// Objects built inside the VM context have its own prototypes; compare plain copies.
const plain = (value) => JSON.parse(JSON.stringify(value));
const citations = (text) => splitCitations(text).filter((part) => part.kind === "citation");


test("page references become short page labels", () => {
  assert.equal(citationLabel("(Source: Q3 Supplier Agreement (sample).pdf, Page 18, Clause 14.2)"), "p. 18");
  assert.equal(citationLabel("[Page 37]"), "p. 37");
  assert.equal(citationLabel("(Clause 14.4, page 19)"), "p. 19");
  assert.equal(citationLabel("(Source: report.pdf, Pages 11-12, Section 9)"), "pp. 11–12");
  assert.equal(citationLabel("(p. 4)"), "p. 4");
  assert.equal(citationLabel("(المصدر: تقرير.pdf، صفحة 7)"), "p. 7");
});


test("references without a page fall back to a short filename or clause", () => {
  assert.equal(citationLabel("(Source: Annual Financial Statements 2026 final.pdf)"), "Annual Financial…");
  assert.equal(citationLabel("(Clause 9.3)"), "§ 9.3");
  assert.equal(citationLabel("(Source: internal memo)"), "Source");
});


test("ordinary parentheses and brackets stay as text", () => {
  for (const text of ["(supplier)", "(30 days)", "[1]", "(see the table below)", "(e.g. 4% or more)", "(two pages)"]) {
    assert.equal(citationLabel(text), null, text);
  }
});


test("splitting keeps the answer text and the full reference", () => {
  const parts = splitCitations("Either party may exit (Source: Report (final).pdf, Page 18, Clause 14.2). Then more (Halden Retail).");
  assert.deepEqual(plain(parts), [
    { kind: "text", text: "Either party may exit" },
    { kind: "citation", label: "p. 18", full: "Report (final).pdf, Page 18, Clause 14.2" },
    { kind: "text", text: ". Then more (Halden Retail)." },
  ]);
  assert.deepEqual(plain(splitCitations("No references here.")), [{ kind: "text", text: "No references here." }]);
  assert.equal(citations("Notice [Page 37] and (page 19, Figure 14.1)").length, 2);
});


test("the rehype plugin leaves links, code and math untouched", () => {
  const text = (value) => ({ type: "text", value });
  const tree = { type: "root", children: [
    { type: "element", tagName: "p", properties: {}, children: [text("Exit notice (Page 18).")] },
    { type: "element", tagName: "a", properties: { href: "#" }, children: [text("link (Page 2)")] },
    { type: "element", tagName: "code", properties: {}, children: [text("call(page 3)")] },
    { type: "element", tagName: "span", properties: { className: ["math", "math-inline"] }, children: [text("f(page 4)")] },
  ] };
  rehypeCitations()(tree);
  const [paragraph, link, code, math] = tree.children;
  assert.equal(paragraph.children.length, 3);
  assert.deepEqual(plain(paragraph.children[1].properties), { className: ["aura-cite"], dataCite: "Page 18" });
  assert.equal(paragraph.children[1].children[0].value, "p. 18");
  assert.equal(link.children[0].value, "link (Page 2)");
  assert.equal(code.children[0].value, "call(page 3)");
  assert.equal(math.children[0].value, "f(page 4)");
});
