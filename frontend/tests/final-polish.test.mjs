// Timestamps in the device time zone, archive/pin ordering, list motion planning
// and the workspace cache. Executes the real TypeScript modules; no browser,
// backend or network.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function loader(globals = {}) {
  const modules = new Map();
  function load(relative) {
    if (modules.has(relative)) return modules.get(relative);
    const compiled = ts.transpileModule(readFileSync(path.join(root, relative), "utf8"), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
    } }).outputText;
    const exports = {};
    modules.set(relative, exports);
    const dependency = (name) => name.startsWith("@/") ? load(`src/${name.slice(2)}.ts`) : require(name);
    vm.runInNewContext(compiled, { exports, require: dependency, Intl, Date, Number, Math, ...globals },
      { filename: relative });
    return exports;
  }
  return load;
}

const load = loader();
const datetime = load("src/lib/datetime.ts");
const { mergePageItems } = load("src/lib/pagination.ts");
const { withUpdatedFlags } = load("src/lib/chat-list.ts");
const { planListMotion } = load("src/lib/list-motion.ts");
const plain = (value) => JSON.parse(JSON.stringify(value));


// ---- Timestamps -------------------------------------------------------------------

test("server timestamps without an offset are UTC; explicit offsets are respected", () => {
  const utc = Date.UTC(2026, 9, 26, 12, 5, 0);
  for (const value of ["2026-10-26T12:05:00", "2026-10-26T12:05:00Z", "2026-10-26T12:05:00+00:00",
    "2026-10-26T15:05:00+03:00", "2026-10-26T12:05:00.000000", " 2026-10-26T12:05:00 "]) {
    assert.equal(datetime.parseServerTimestamp(value).getTime(), utc, value);
  }
  for (const value of [null, undefined, "", "not a date"]) {
    assert.equal(datetime.parseServerTimestamp(value), null);
  }
});


test("the same instant is shown in each device's own time zone", () => {
  const value = "2026-10-26T12:05:00";          // 12:05 UTC
  const now = new Date(Date.UTC(2026, 9, 26, 18, 0));
  const at = (timeZone) => datetime.formatMessageTimestamp(value, now, { timeZone, locale: "en-GB" });
  assert.equal(at("UTC"), "12:05");
  assert.equal(at("Europe/Istanbul"), "15:05");
  assert.equal(at("America/New_York"), "08:05");
  assert.equal(at("Asia/Kolkata"), "17:35");
});


test("midnight boundaries follow the device's calendar day, not UTC's", () => {
  const message = "2026-10-25T22:30:00";                    // 22:30 UTC on the 25th
  const now = new Date(Date.UTC(2026, 9, 26, 6, 0));        // 06:00 UTC on the 26th
  const zone = (timeZone) => ({ timeZone, locale: "en-GB" });
  // UTC: the previous day. Istanbul (+3): 01:30 on the 26th, i.e. today.
  assert.equal(datetime.formatMessageTimestamp(message, now, zone("UTC")), "Yesterday, 22:30");
  assert.equal(datetime.formatMessageTimestamp(message, now, zone("Europe/Istanbul")), "01:30");
  // Older messages carry a date, and the year only when it differs.
  assert.match(datetime.formatMessageTimestamp("2026-10-20T09:00:00", now, zone("UTC")), /^20 Oct, 09:00$/);
  assert.match(datetime.formatMessageTimestamp("2025-12-31T09:00:00", now, zone("UTC")), /2025/);
});


test("calendar days are counted correctly across a daylight-saving change", () => {
  // Europe/Berlin moves its clocks forward at 01:00 UTC on 29 March 2026 (a 23-hour day).
  const zone = { timeZone: "Europe/Berlin" };
  const before = new Date(Date.UTC(2026, 2, 28, 22, 30));   // 23:30 local on the 28th
  const after = new Date(Date.UTC(2026, 2, 29, 22, 30));    // 00:30 local on the 30th
  assert.equal(datetime.calendarDaysBetween(before, after, zone), 2);
  assert.equal(datetime.calendarDaysBetween(before, new Date(Date.UTC(2026, 2, 29, 21, 30)), zone), 1);
  // New York's autumn change (1 November 2026, a 25-hour day).
  const ny = { timeZone: "America/New_York" };
  assert.equal(datetime.calendarDaysBetween(new Date(Date.UTC(2026, 10, 1, 3, 30)), new Date(Date.UTC(2026, 10, 2, 4, 30)), ny), 1);
});


test("formatting is identical after a refresh, whatever the serialized form", () => {
  const now = new Date(Date.UTC(2026, 9, 26, 18, 0));
  const zone = { timeZone: "Europe/Istanbul", locale: "en-GB" };
  const forms = ["2026-10-26T12:05:00", "2026-10-26T12:05:00Z", "2026-10-26T12:05:00.000000"];
  const shown = forms.map((value) => datetime.formatMessageTimestamp(value, now, zone));
  assert.deepEqual(shown, ["15:05", "15:05", "15:05"]);
  assert.equal(datetime.formatMessageTimestamp(forms[0], now, zone), shown[0], "stable across calls");
  assert.match(datetime.formatFullTimestamp(forms[0], zone), /26 October 2026/);
});


test("the display clock notices a device time zone change while the app is open", () => {
  let deviceZone = "Europe/Istanbul";
  // Like the real constructor, usable with or without `new`.
  function DeviceDateTimeFormat(locale, options) {
    return new Intl.DateTimeFormat(locale, { timeZone: deviceZone, ...options });
  }
  const handlers = {};
  const fakeWindow = {
    setInterval: () => 1, clearInterval: () => {},
    addEventListener: (type, handler) => { handlers[type] = handler; }, removeEventListener: () => {},
  };
  const fakeDocument = { addEventListener: () => {}, removeEventListener: () => {} };
  const clock = loader({ Intl: { ...Intl, DateTimeFormat: DeviceDateTimeFormat }, window: fakeWindow, document: fakeDocument })(
    "src/lib/datetime.ts");
  let notified = 0;
  const unsubscribe = clock.subscribeDisplayClock(() => { notified += 1; });
  assert.match(clock.getDisplayClockSnapshot(), /^Europe\/Istanbul\|/);
  deviceZone = "America/New_York";
  handlers.focus();
  assert.equal(notified, 1, "components re-render once with the new zone");
  assert.match(clock.getDisplayClockSnapshot(), /^America\/New_York\|/);
  handlers.focus();
  assert.equal(notified, 1, "no re-render when nothing changed");
  unsubscribe();
  assert.equal(clock.getServerDisplayClockSnapshot(), "", "server render shows no time, so hydration never mismatches");
});


// ---- Recent ordering on archive, restore, pin and unpin -------------------------

const chat = (id, title, last_activity_at, extra = {}) => ({
  id, title, created_at: "2026-10-01T09:00:00", last_activity_at, is_pinned: false, is_archived: false, ...extra,
});
const sorted = (list) => mergePageItems([], list, "chats");
const titles = (list) => plain(list.map((item) => item.title));
// A: 15:00 today, B: 13:00 today, C: yesterday.
const abc = () => sorted([
  chat(1, "A", "2026-10-26T15:00:00"), chat(2, "B", "2026-10-26T13:00:00"), chat(3, "C", "2026-10-25T10:00:00"),
  chat(9, "Pinned", "2026-10-20T10:00:00", { is_pinned: true }),
]);
const flip = (list, id, changes) => withUpdatedFlags(list, id, { ...list.find((item) => item.id === id), ...changes });


test("a restored chat returns to its place by latest activity, not to the bottom or top", () => {
  let list = flip(abc(), 1, { is_archived: true });
  assert.deepEqual(titles(list), ["Pinned", "B", "C", "A"], "archived A moves to the archive");
  list = flip(list, 1, { is_archived: false });
  assert.deepEqual(titles(list), ["Pinned", "A", "B", "C"], "A is still the newest, so it is first again");

  list = flip(abc(), 3, { is_archived: true });
  list = flip(list, 3, { is_archived: false });
  assert.deepEqual(titles(list), ["Pinned", "A", "B", "C"], "C returns below A and B");
});


test("archiving and restoring never change the activity time", () => {
  const archived = flip(abc(), 2, { is_archived: true });
  const restored = flip(archived, 2, { is_archived: false });
  assert.equal(restored.find((item) => item.id === 2).last_activity_at, "2026-10-26T13:00:00");
});


test("unpinning returns a chat to its activity position; pinned keep creation order", () => {
  const list = sorted([
    chat(1, "Old pin", "2026-10-26T16:00:00", { is_pinned: true, created_at: "2026-09-01T09:00:00" }),
    chat(2, "New pin", "2026-10-01T09:00:00", { is_pinned: true, created_at: "2026-10-01T09:00:00" }),
    chat(3, "Recent", "2026-10-26T15:00:00"),
  ]);
  assert.deepEqual(titles(list), ["New pin", "Old pin", "Recent"]);
  assert.deepEqual(titles(flip(list, 1, { is_pinned: false })), ["New pin", "Old pin", "Recent"],
    "unpinned chat's latest message (16:00) is newer than Recent (15:00)");
  assert.deepEqual(titles(flip(list, 2, { is_pinned: false })), ["Old pin", "Recent", "New pin"]);
});


test("a restored chat merges with Load More pages without duplicates or gaps", () => {
  const firstPage = flip(abc(), 1, { is_archived: false });
  const nextPage = [chat(4, "D", "2026-10-24T10:00:00"), chat(3, "C", "2026-10-25T10:00:00")];
  const merged = mergePageItems(firstPage, nextPage, "chats");
  assert.deepEqual(titles(merged), ["Pinned", "A", "B", "C", "D"]);
  assert.equal(new Set(merged.map((item) => item.id)).size, merged.length);
});


test("ordering compares instants, so mixed timestamp formats still sort correctly", () => {
  const list = sorted([
    chat(1, "Naive later", "2026-10-26T13:00:00.5"),
    chat(2, "With offset earlier", "2026-10-26T15:59:59+03:00"),   // 12:59:59 UTC
    chat(3, "Z earliest", "2026-10-26T12:00:00Z"),
  ]);
  assert.deepEqual(titles(list), ["Naive later", "With offset earlier", "Z earliest"]);
});


// ---- Sidebar motion planning ------------------------------------------------------

test("list motion: nothing on first render, moved rows glide, new rows fade in", () => {
  const positions = (entries) => new Map(entries);
  assert.deepEqual(plain(planListMotion(new Map(), positions([["a", 0], ["b", 40]]))), { moved: [], entered: [] });
  const plan = planListMotion(positions([["a", 0], ["b", 40], ["c", 80]]), positions([["c", 0], ["a", 40], ["b", 80], ["d", 120]]));
  assert.deepEqual(plain(plan), {
    moved: [{ id: "c", fromOffset: 80 }, { id: "a", fromOffset: -40 }, { id: "b", fromOffset: -40 }],
    entered: ["d"],
  });
  assert.deepEqual(plain(planListMotion(positions([["a", 0]]), positions([["a", 0.4]]))), { moved: [], entered: [] },
    "sub-pixel changes do not animate");
});


// ---- Workspace cache --------------------------------------------------------------

test("the cached chat list never outlives the session", async () => {
  const cacheLoad = loader({ fetch: async () => ({ ok: true }), process: { env: {} } });
  const cache = cacheLoad("src/lib/workspace-cache.ts");
  const { logoutSession } = cacheLoad("src/lib/logout.ts");
  cache.workspaceCache.current = { user: { id: 1 }, chats: [chat(1, "Private", "2026-10-26T15:00:00")], chatCursor: null };
  let signedOut = false;
  await logoutSession(() => { signedOut = true; }, () => {});
  assert.equal(signedOut, true);
  assert.equal(cache.workspaceCache.current, null);
});
