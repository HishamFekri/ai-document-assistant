// Citation chip interaction and placement. Executes the real TypeScript module;
// no browser, backend or network.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const compiled = ts.transpileModule(readFileSync(path.join(root, "src/lib/citation-popover.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const popover = {};
vm.runInNewContext(compiled, { exports: popover }, { filename: "citation-popover.ts" });
const { CLOSED, nextPopoverState, placeTip } = popover;

const run = (events, state = CLOSED) => events.reduce(nextPopoverState, state);
const open = (state) => state.open;

// Event order a browser produces for one tap on a touch screen.
const tap = [
  { type: "pointer-enter", pointerType: "touch" },
  { type: "focus", keyboard: false },
  { type: "press", pointerType: "touch" },
];


test("a touch tap opens the details on the first tap and closes on the second", () => {
  const afterFirst = run(tap);
  assert.equal(open(afterFirst), true);
  assert.equal(open(run(tap, afterFirst)), false);
});


test("tapping elsewhere, scrolling or leaving focus closes the details", () => {
  for (const close of [{ type: "dismiss" }, { type: "blur" }]) {
    assert.equal(open(run([...tap, close])), false);
  }
  // Touch pointers never close on pointer-leave; the popover stays until dismissed.
  assert.equal(open(run([...tap, { type: "pointer-leave", pointerType: "touch" }])), true);
});


test("mouse: hover previews, leaving hides, a click pins and a second click closes", () => {
  const hover = { type: "pointer-enter", pointerType: "mouse" };
  const leave = { type: "pointer-leave", pointerType: "mouse" };
  assert.equal(open(run([hover])), true);
  assert.equal(open(run([hover, leave])), false);
  const pinned = run([hover, { type: "focus", keyboard: false }, { type: "press", pointerType: "mouse" }]);
  assert.equal(open(pinned), true, "a click on an open preview keeps it open");
  assert.equal(open(run([leave], pinned)), true, "a pinned popover survives the pointer leaving");
  assert.equal(open(run([{ type: "press", pointerType: "mouse" }], pinned)), false);
});


test("keyboard: focus previews, Enter and Space toggle, Escape closes", () => {
  const focused = run([{ type: "focus", keyboard: true }]);
  assert.equal(open(focused), true);
  assert.equal(open(run([{ type: "key", key: "Escape" }], focused)), false);
  const pinned = run([{ type: "key", key: "Enter" }], focused);
  assert.equal(pinned.pinned, true);
  assert.equal(open(run([{ type: "key", key: " " }], pinned)), false);
  assert.equal(open(run([{ type: "key", key: "Tab" }], focused)), true, "other keys do nothing");
});


test("the details are placed above the chip when they fit, otherwise below", () => {
  const viewport = { width: 390, height: 844 };
  const tip = { width: 280, height: 60 };
  const above = placeTip({ top: 400, bottom: 418, left: 150, width: 40 }, tip, viewport);
  assert.deepEqual([above.placement, above.top], ["above", 400 - 8 - 60]);
  const below = placeTip({ top: 30, bottom: 48, left: 150, width: 40 }, tip, viewport);
  assert.deepEqual([below.placement, below.top], ["below", 48 + 8]);
});


test("the details always stay inside the screen", () => {
  const viewport = { width: 390, height: 844 };
  const tip = { width: 280, height: 60 };
  const cases = [
    { top: 300, bottom: 318, left: 0, width: 30 },      // left edge
    { top: 300, bottom: 318, left: 370, width: 30 },    // right edge
    { top: 830, bottom: 848, left: 180, width: 30 },    // bottom edge, partly off screen
    { top: -20, bottom: -2, left: 180, width: 30 },     // scrolled above the screen
  ];
  for (const chip of cases) {
    const placed = placeTip(chip, tip, viewport);
    assert.ok(placed.left >= 12 && placed.left + tip.width <= viewport.width - 12, JSON.stringify(placed));
    assert.ok(placed.top >= 12 && placed.top + tip.height <= viewport.height - 12, JSON.stringify(placed));
  }
  // Wider than a narrow screen (CSS caps it at 100vw - 24px): pinned to the gutter.
  const narrow = placeTip({ top: 300, bottom: 318, left: 100, width: 30 }, { width: 400, height: 60 }, { width: 320, height: 600 });
  assert.equal(narrow.left, 12);
});
