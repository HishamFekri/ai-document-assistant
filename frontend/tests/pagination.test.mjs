import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function app(fetch = () => { throw new Error("Unexpected network"); }) {
  const modules = new Map(), slots = [];
  let index = 0;
  const hooks = {
    useState(initial) {
      const key = index++;
      if (!(key in slots)) slots[key] = initial;
      return [slots[key], (next) => { slots[key] = typeof next === "function" ? next(slots[key]) : next; }];
    },
    useRef(initial) {
      const key = index++;
      if (!(key in slots)) slots[key] = { current: initial };
      return slots[key];
    },
    useCallback: (fn) => fn,
    useEffect: () => {},
  };
  function load(name) {
    if (modules.has(name)) return modules.get(name);
    const exports = {};
    modules.set(name, exports);
    const source = readFileSync(path.join(root, name), "utf8");
    const code = ts.transpileModule(source, { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
    } }).outputText;
    const require = (dependency) => {
      if (dependency === "react") return hooks;
      if (dependency === "next/navigation") return { useRouter: () => ({}) };
      if (dependency.startsWith("@/")) return load("src/" + dependency.slice(2) + ".ts");
      throw new Error(dependency);
    };
    vm.runInNewContext(code, { exports, require, process: { env: {} }, fetch, Response,
      URLSearchParams, console, window: { dispatchEvent() {}, alert(message) { throw new Error(message); } } });
    return exports;
  }
  return { load, render(chatId = 1) { index = 0; return load("src/hooks/useChat.ts").useChat(chatId); } };
}

const item = (id, extra = {}) => ({ id, created_at: "2026-09-01T12:00:00", ...extra });
const response = (items, cursor) => new Response(JSON.stringify(items), {
  headers: cursor ? { "X-Next-Cursor": cursor } : {},
});

test("readPage preserves array contract and continuation", async () => {
  const { readPage } = app().load("src/lib/pagination.ts");
  const page = await readPage(response([item(1)], "signed-token"));
  assert.equal(page.items[0].id, 1);
  assert.equal(page.nextCursor, "signed-token");
  assert.equal((await readPage(response([]))).nextCursor, null);
  await assert.rejects(readPage(response({ bad: true })), /Invalid page/);
});

test("merge keeps older messages, replaces updates and uses ID tie-breakers", () => {
  const { mergePageItems } = app().load("src/lib/pagination.ts");
  const rows = mergePageItems([item(3), item(4, { content: "old" })], [item(1), item(2), item(4, { content: "new" })]);
  assert.equal(JSON.stringify(rows.map(r => r.id)), "[1,2,3,4]");
  assert.equal(rows[3].content, "new");
});

test("chat merge matches archive/pin/date/id ordering", () => {
  const { mergePageItems } = app().load("src/lib/pagination.ts");
  const rows = mergePageItems([item(1)], [item(2), item(3, { is_pinned: true }), item(4, { is_archived: true })], "chats");
  assert.equal(JSON.stringify(rows.map(r => r.id)), "[3,2,1,4]");
});

test("all three API wrappers send bounded pages and encoded cursors", async () => {
  const calls = [];
  const api = app(async (url, options) => { calls.push([url, options]); return response([], "next"); }).load("src/lib/chat-api.ts");
  await api.getDocuments("__cookie__", "signed/a+b=");
  await api.getChats("__cookie__", "signed/a+b=");
  await api.getMessages("__cookie__", 7, "signed/a+b=");
  for (const [url, options] of calls) {
    const parsed = new URL(url);
    assert.equal(parsed.searchParams.get("limit"), "50");
    assert.equal(parsed.searchParams.get("cursor"), "signed/a+b=");
    assert.equal(options.credentials, "include");
    assert.equal(options.headers.Authorization, undefined);
  }
});

test("hook reaches older messages and refresh preserves loaded history", async () => {
  const calls = [];
  const application = app(async url => {
    calls.push(url);
    return new URL(url).searchParams.has("cursor")
      ? response([item(1), item(2)]) : response([item(3), item(4)], "older");
  });
  await application.render().refreshMessages();
  let hook = application.render();
  assert.equal(hook.hasOlderMessages, true);
  await hook.loadOlderMessages();
  hook = application.render();
  assert.equal(hook.hasOlderMessages, false);
  assert.equal(JSON.stringify(hook.messages.map(m => m.id)), "[1,2,3,4]");
  await hook.refreshMessages();
  assert.equal(JSON.stringify(application.render().messages.map(m => m.id)), "[1,2,3,4]");
  assert.equal(new URL(calls[1]).searchParams.get("cursor"), "older");
});

test("hook appends another chat page without duplicates", async () => {
  const application = app(async url => new URL(url).searchParams.has("cursor")
    ? response([item(2), item(1)]) : response([item(3), item(2)], "next"));
  await application.render().refreshChats();
  await application.render().loadMoreChats();
  const hook = application.render();
  assert.equal(hook.hasMoreChats, false);
  assert.equal(JSON.stringify(hook.chats.map(c => c.id)), "[3,2,1]");
});
