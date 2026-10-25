// Sidebar updates for new chats and the per-frame batching of streamed answers.
// Executes the real TypeScript modules; no browser, backend or network.
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

function loader() {
  const modules = new Map();
  function load(relative) {
    if (modules.has(relative)) return modules.get(relative);
    const compiled = ts.transpileModule(readFileSync(path.join(root, relative), "utf8"), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
    } }).outputText;
    const exports = {};
    modules.set(relative, exports);
    const dependency = (name) => name.startsWith("@/") ? load(`src/${name.slice(2)}.ts`) : require(name);
    vm.runInNewContext(compiled, { exports, require: dependency, TextDecoder, Error, DOMException },
      { filename: relative });
    return exports;
  }
  return load;
}

const load = loader();
const { upsertChatListItem, retitleChatListItem } = load("src/lib/chat-list.ts");
const { createFrameBatcher } = load("src/lib/frame-batcher.ts");
const { readNDJSON } = load("src/lib/ndjson.ts");

// VM-built objects have their own prototypes; compare plain copies.
const plain = (value) => JSON.parse(JSON.stringify(value));

const item = (id, created_at, extra = {}) => ({
  id, title: `Chat ${id}`, is_pinned: false, is_archived: false, created_at, ...extra,
});

const sidebar = () => [
  item(1, "2026-10-25T09:00:00", { is_pinned: true, title: "Pinned" }),
  item(2, "2026-10-24T09:00:00"),
  item(3, "2026-10-23T09:00:00"),
  item(9, "2026-10-01T09:00:00", { is_archived: true, title: "Archived" }),
];


test("a newly created chat joins Recent at the top, below pinned chats", () => {
  const created = { ...item(10, "2026-10-26T12:00:00", { title: "New chat" }), user_id: 1, documents: [] };
  const list = upsertChatListItem(sidebar(), created);
  assert.deepEqual(plain(list.map((chat) => chat.id)), [1, 10, 2, 3, 9]);
  assert.equal("documents" in list[1], false, "only list fields are kept");
});


test("adding the same chat again never duplicates it", () => {
  const created = { ...item(10, "2026-10-26T12:00:00"), user_id: 1, documents: [] };
  const once = upsertChatListItem(sidebar(), created);
  const twice = upsertChatListItem(once, { ...created, title: "Termination notice" });
  assert.deepEqual(plain(twice.map((chat) => chat.id)), [1, 10, 2, 3, 9]);
  assert.equal(twice.find((chat) => chat.id === 10).title, "Termination notice");
});


test("pinned chats stay pinned and archived chats stay archived", () => {
  const pinnedAgain = upsertChatListItem(sidebar(), item(1, "2026-10-25T09:00:00", { is_pinned: true, title: "Renamed" }));
  assert.deepEqual(plain(pinnedAgain.map((chat) => chat.id)), [1, 2, 3, 9]);
  const archivedAgain = upsertChatListItem(sidebar(), item(9, "2026-10-01T09:00:00", { is_archived: true }));
  assert.equal(archivedAgain.at(-1).id, 9);
  assert.equal(archivedAgain.at(-1).is_archived, true);
});


test("a generated title updates only its own chat", () => {
  const list = retitleChatListItem(sidebar(), 2, "Revenue by region");
  assert.deepEqual(plain(list.map((chat) => chat.title)), ["Pinned", "Revenue by region", "Chat 3", "Archived"]);
  assert.deepEqual(plain(retitleChatListItem(sidebar(), 404, "Missing")), plain(sidebar()));
});


function fakeFrames() {
  const callbacks = new Map();
  let next = 1;
  return {
    schedule: (callback) => { callbacks.set(next, callback); return next++; },
    cancel: (handle) => { callbacks.delete(handle); },
    runFrame() { const pending = [...callbacks.values()]; callbacks.clear(); pending.forEach((callback) => callback()); },
    get pending() { return callbacks.size; },
  };
}


test("many tokens in one frame render once, with the latest text", () => {
  const frames = fakeFrames();
  let answer = "";
  const rendered = [];
  const batcher = createFrameBatcher(() => rendered.push(answer), frames.schedule, frames.cancel);
  for (const token of ["The ", "agreement ", "allows ", "termination"]) { answer += token; batcher.request(); }
  assert.equal(frames.pending, 1, "one frame scheduled for the burst");
  frames.runFrame();
  assert.deepEqual(rendered, ["The agreement allows termination"]);
  frames.runFrame();
  assert.equal(rendered.length, 1, "no work without new tokens");
});


test("finishing or stopping never leaves a stale frame behind", () => {
  const frames = fakeFrames();
  let flushes = 0;
  const batcher = createFrameBatcher(() => { flushes += 1; }, frames.schedule, frames.cancel);
  batcher.request();
  batcher.flushNow();
  assert.equal(flushes, 1);
  assert.equal(frames.pending, 0);
  batcher.request();
  batcher.cancel();
  frames.runFrame();
  assert.equal(flushes, 1, "a cancelled frame does not render");
});


test("a streamed answer renders completely and at most once per frame", async () => {
  const frames = fakeFrames();
  const tokens = Array.from({ length: 200 }, (_, index) => `word${index} `);
  const lines = tokens.map((content) => JSON.stringify({ type: "token", content }) + "\n");
  lines.push(JSON.stringify({ type: "done", sources: [] }) + "\n");
  // Deliver the NDJSON in uneven network chunks, as a real response does.
  const text = lines.join("");
  const encoder = new TextEncoder();
  const chunks = [];
  for (let start = 0; start < text.length; start += 997) chunks.push(encoder.encode(text.slice(start, start + 997)));
  const body = new ReadableStream({ start(controller) { chunks.forEach((chunk) => controller.enqueue(chunk)); controller.close(); } });

  let answer = "";
  let shown = "";
  let renders = 0;
  const batcher = createFrameBatcher(() => { renders += 1; shown = answer; }, frames.schedule, frames.cancel);
  let chunkCount = 0;
  await readNDJSON(body, async (event) => {
    if (event.type === "token") {
      answer += event.content;
      batcher.request();
      if (++chunkCount % 25 === 0) frames.runFrame();
    }
    if (event.type === "done") batcher.flushNow();
  });

  assert.equal(shown, tokens.join(""), "nothing is lost or reordered");
  assert.ok(renders <= 9, `rendered ${renders} times for 200 tokens`);
});


// Drives the real useChatStream hook through a draft (new chat) send.
const { runtime, deferred, tick } = await import("./helpers/hook-runtime.mjs");

function streamBody() {
  let controller;
  const body = new ReadableStream({ start(c) { controller = c; } });
  const encoder = new TextEncoder();
  return { body, send: (text) => controller.enqueue(encoder.encode(text)), end: () => controller.close() };
}

function draftRuntime(fetch, createPersistedChat) {
  const app = runtime({ fetch });
  let messages = [];
  const calls = { created: [], discarded: [], titles: [] };
  const props = { chatId: 0, chat: null, messages,
    setMessages: (next) => { messages = typeof next === "function" ? next(messages) : next; },
    refreshMessages: async () => {}, getToken: () => "__cookie__", attachment: null, clearComposerAttachment() {},
    createPersistedChat,
    onChatCreated: (chat) => calls.created.push(chat.id),
    onChatDiscarded: (id) => calls.discarded.push(id),
    onChatTitleGenerated: ({ title, chatId }) => calls.titles.push([chatId, title]) };
  return { app, calls, render: () => app.render("src/hooks/useChatStream.ts", "useChatStream", props), messages: () => messages };
}


test("a new chat is announced once when created and keeps it through a successful answer", async () => {
  const source = streamBody();
  const draft = draftRuntime(async () => new Response(source.body), async () => ({ id: 9, title: "New chat", documents: [] }));
  const pending = draft.render().sendQuestionText("What notice is needed?");
  await tick(); await tick();
  assert.deepEqual(plain(draft.calls.created), [9], "listed as soon as it exists");
  source.send('{"type":"chat_title","title":"Termination notice"}\n{"type":"token","content":"Ninety days."}\n{"type":"done","sources":[]}\n');
  source.end();
  await pending;
  assert.deepEqual(plain(draft.calls.titles), [[9, "Termination notice"]], "the title is keyed by the new chat id");
  assert.deepEqual(plain(draft.calls.discarded), []);
  assert.deepEqual(plain(draft.app.events.filter(([kind]) => kind === "replace")), [["replace", "/chat/9"]]);
  assert.equal(draft.messages().find((m) => m.role === "assistant").content, "Ninety days.");
  draft.app.unmount();
});


test("a new chat whose first message never reaches the backend is removed again", async () => {
  const draft = draftRuntime(async () => new Response(JSON.stringify({ detail: "Busy" }), { status: 503 }),
    async () => ({ id: 9, title: "New chat", documents: [] }));
  await draft.render().sendQuestionText("What notice is needed?");
  assert.deepEqual(plain(draft.calls.created), [9]);
  assert.deepEqual(plain(draft.calls.discarded), [9]);
  assert.equal(draft.render().composerError, "Busy");
  draft.app.unmount();
});


test("stopping after the chat is created but before the answer starts removes it", async () => {
  const response = deferred();
  const draft = draftRuntime(() => response.promise, async () => ({ id: 9, title: "New chat", documents: [] }));
  const pending = draft.render().sendQuestionText("What notice is needed?");
  await tick(); await tick();
  assert.deepEqual(plain(draft.calls.created), [9]);
  draft.render().stopGeneration();
  assert.deepEqual(plain(draft.calls.discarded), [9]);
  response.resolve(new Response(streamBody().body));
  await pending;
  assert.deepEqual(plain(draft.calls.discarded), [9], "discarded only once");
  draft.app.unmount();
});
