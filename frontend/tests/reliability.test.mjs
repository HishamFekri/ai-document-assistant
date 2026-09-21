import assert from "node:assert/strict";
import test from "node:test";
import { runtime, deferred, tick } from "./helpers/hook-runtime.mjs";

const encode = value => new TextEncoder().encode(value);
const json = value => new Response(JSON.stringify(value));
function stream() {
  let controller, cancelled = 0;
  const body = new ReadableStream({ start(c) { controller = c; }, cancel() { cancelled++; } });
  return { body, send: text => controller.enqueue(encode(text)), end: () => controller.close(),
    fail: () => controller.error(new Error("Disconnected")), cancelled: () => cancelled };
}
const message = (id, extra = {}) => ({ id, chat_id: 1, role: "assistant", content: `message ${id}`,
  status: "completed", error: null, sources: [], documents: [], created_at: "2026-09-01T12:00:00", ...extra });
const summary = (document_id, id = document_id) => ({ id, chat_id: 1, document_id, mode: "summary", status: "completed",
  version: 1, content: { title: "Local", sections: [] }, is_selected: true, error: null, created_at: "2026-09-01" });

test("NDJSON flushes the final buffer and split UTF-8, requires done, and closes upstream", async () => {
  const { readNDJSON } = runtime().load("src/lib/ndjson.ts");
  const source = stream(), events = [];
  const pending = readNDJSON(source.body, e => events.push(e));
  source.send('{"type":"token","content":"hello"}\r\n');
  source.send('{"type":"done"}'); source.end();
  await pending;
  assert.equal(events.length, 2); assert.equal(events[1].type, "done");
  const bytes = encode('{"type":"token","content":"مرحبا"}\n{"type":"done"}\n');
  const received = [];
  await readNDJSON(new ReadableStream({ start(c) { for (const byte of bytes) c.enqueue(new Uint8Array([byte])); c.close(); } }), e => received.push(e));
  assert.equal(received[0].content, "مرحبا");
  const open = stream();
  const complete = readNDJSON(open.body, () => {});
  open.send('{"type":"done"}\n{"type":"token","content":"late"}\n');
  await complete;
  assert.equal(open.cancelled(), 1); assert.equal(open.body.locked, false);
});

test("NDJSON rejects premature EOF, malformed data and transport failures", async () => {
  const { readNDJSON } = runtime().load("src/lib/ndjson.ts");
  for (const text of ['', '{"type":"token","content":"partial"}\n', '{"type":"done"']) {
    const source = stream(); const pending = readNDJSON(source.body, () => {});
    source.send(text); source.end();
    await assert.rejects(pending, /completion|Invalid stream/);
    assert.equal(source.body.locked, false);
  }
  const source = stream(); const pending = readNDJSON(source.body, () => {}); source.fail();
  await assert.rejects(pending, /Disconnected/); assert.equal(source.body.locked, false);
});

test("NDJSON abort unblocks a pending read and ignores buffered later events", async () => {
  const { readNDJSON } = runtime().load("src/lib/ndjson.ts");
  const source = stream(), controller = new AbortController(), events = [];
  const pending = readNDJSON(source.body, e => { events.push(e); controller.abort(); }, controller.signal);
  source.send('{"type":"token"}\n{"type":"done"}\n');
  await assert.rejects(pending, { name: "AbortError" });
  assert.equal(events.length, 1); assert.equal(source.cancelled(), 1); assert.equal(source.body.locked, false);
});

test("request scopes ignore superseded results, abort on disposal, and survive Strict Mode replay", () => {
  const { RequestScope } = runtime().load("src/lib/request-scope.ts");
  const scope = new RequestScope(), old = scope.begin("read"), next = scope.begin("read");
  assert.equal(old.current(), false); assert.equal(old.signal.aborted, true);
  old.finish(); assert.equal(next.current(), true);
  scope.dispose(); assert.equal(next.signal.aborted, true);
  scope.activate(); const newest = scope.begin("read"); assert.equal(newest.current(), true);
  assert.equal(next.current(), false); newest.finish(); assert.equal(newest.current(), false);
});

test("switching chats aborts an in-flight read and ignores a server that returns anyway", async () => {
  const old = deferred(); const calls = [];
  const app = runtime({ passive: false, fetch: (url, options) => { calls.push(options); return url.includes('/chats/1/messages') ? old.promise : Promise.resolve(json([message(20, { chat_id: 2 })])); } });
  const render = id => app.render("src/hooks/useChat.ts", "useChat", id);
  const pending = render(1).refreshMessages();
  await render(2).refreshMessages();
  old.resolve(json([message(1)])); await pending;
  assert.equal(calls[0].signal.aborted, true);
  assert.equal(JSON.stringify(render(2).messages.map(m => m.id)), "[20]");
  app.unmount();
});

test("latest chat refetch wins and persisted rows replace optimistic duplicates without dropping older pages", async () => {
  const old = deferred(); let count = 0;
  const app = runtime({ passive: false, fetch: () => ++count === 1 ? old.promise : Promise.resolve(json([message(3), message(4)])) });
  const render = () => app.render("src/hooks/useChat.ts", "useChat", 1);
  render().setMessages([message(1), message(2), message(-1), message(-2)]);
  const pending = render().refreshMessages();
  await render().refreshMessages([-1, -2]);
  old.resolve(json([message(3, { content: "stale" })])); await pending;
  await render().refreshMessages();
  assert.equal(JSON.stringify(render().messages.map(m => m.id)), "[1,2,3,4]");
  assert.equal(render().messages[2].content, "message 3");
  app.unmount();
});

test("a successfully queued upload remains processing instead of becoming failed", async () => {
  const app = runtime({ passive: false, stubs: { "@/lib/chat-api": {
    uploadDocument: async () => ({ id: 9, processing_status: "processing",
      processing_stage: "uploaded", processing_progress: 5, processing_error: null }),
  } } });
  const render = () => app.render("src/hooks/useChat.ts", "useChat", null);
  const target = { files: [{ name: "queued.txt", size: 6 }], value: "selected" };
  await render().handleUpload({ target });
  assert.equal(target.value, "");
  assert.equal(render().attachment.documentId, 9);
  assert.equal(render().attachment.status, "processing");
  assert.equal(render().attachment.error, null);
  app.unmount();
});

test("switching documents ignores a stale selected-summary response", async () => {
  const old = deferred(); const calls = [];
  const app = runtime({ fetch: (url, options) => { calls.push(options); return url.includes('/documents/1/') ? old.promise : Promise.resolve(json(summary(2))); } });
  const render = id => app.render("src/hooks/useDocumentSummary.ts", "useDocumentSummary", { token: "__cookie__", chatId: 1, documentId: id, mode: "summary" });
  const pending = render(1).refreshSummary(); await render(2).refreshSummary();
  old.resolve(json(summary(1))); await pending;
  assert.equal(calls[0].signal.aborted, true); assert.equal(render(2).selectedSummary.document_id, 2);
  app.unmount();
});

test("assistant switching documents cancels sends and suppresses stale summary callbacks and finally state", async () => {
  const first = deferred(), second = deferred(); const calls = [], generated = [];
  const app = runtime({ fetch: (url, options) => { calls.push(options); return url.includes('/documents/1/') ? first.promise : second.promise; } });
  const render = id => app.render("src/hooks/useSummaryAssistant.ts", "useSummaryAssistant", { token: "__cookie__", chatId: 1, documentId: id, onSummaryGenerated: s => generated.push(s) });
  const a = render(1).sendMessage("first"); const b = render(2).sendMessage("second");
  first.resolve(json({ user_message: message(1), assistant_message: message(2), generated_summary: summary(1) }));
  assert.equal(await a, null); assert.equal(calls[0].signal.aborted, true);
  assert.equal(render(2).sending, true); assert.equal(generated.length, 0);
  second.resolve(json({ user_message: message(3), assistant_message: message(4), generated_summary: summary(2) })); await b;
  assert.equal(generated[0].document_id, 2);
  assert.equal(JSON.stringify(render(2).messages.map(m => m.id)), "[3,4]");
  app.unmount();
});

test("assistant cancellation and unmount prevent late UI updates", async () => {
  const source = deferred(); let signal;
  const app = runtime({ fetch: (url, options) => { signal = options.signal; return source.promise; } });
  const props = { token: "__cookie__", chatId: 1, documentId: 1 };
  const render = () => app.render("src/hooks/useSummaryAssistant.ts", "useSummaryAssistant", props);
  const pending = render().sendMessage("instruction"); render().cancelPending();
  assert.equal(render().sending, false); assert.equal(signal.aborted, true);
  source.resolve(json({ user_message: message(1), assistant_message: message(2) }));
  assert.equal(await pending, null); assert.equal(render().messages.length, 0); app.unmount();
});

test("summary Stop before start closes immediately and an old start cannot change the next generation", async () => {
  const callbacks = [], waits = [], signals = [], cancelled = [];
  const app = runtime({ stubs: { "@/lib/summary-api": {
    streamDocumentSummary: (token, chat, doc, callback, mode, signal) => { const wait = deferred(); callbacks.push(callback); waits.push(wait); signals.push(signal); return wait.promise; },
    cancelDocumentSummaryGeneration: async (...args) => cancelled.push(args),
  } } });
  const render = () => app.render("src/hooks/useDocumentSummary.ts", "useDocumentSummary", { token: "__cookie__", chatId: 1, documentId: 1, mode: "summary" });
  const a = render().generateSummary(); render().stopGeneration();
  assert.equal(signals[0].aborted, true); assert.equal(render().generating, false);
  const b = render().generateSummary();
  callbacks[0]({ type: "start", summary_id: 99 }); callbacks[0]({ type: "title", title: "stale" });
  waits[0].resolve(summary(1, 99)); assert.equal(await a, null);
  assert.equal(render().generating, true); assert.notEqual(render().selectedSummary.id, 99);
  callbacks[1]({ type: "start", summary_id: 100 }); render().stopGeneration();
  assert.equal(cancelled[0][3], 100); assert.equal(cancelled.length, 1);
  waits[1].resolve(summary(1, 100)); await b; assert.equal(render().selectedSummary.status, "cancelled"); app.unmount();
});

test("summary premature EOF remains visibly failed instead of silently refreshing away the error", async () => {
  const source = stream();
  const app = runtime({ fetch: async () => new Response(source.body) });
  const render = () => app.render("src/hooks/useDocumentSummary.ts", "useDocumentSummary", { token: "__cookie__", chatId: 1, documentId: 1, mode: "summary" });
  const pending = render().generateSummary();
  source.send('{"type":"start","summary_id":1}\n{"type":"title","title":"Partial"}\n'); source.end();
  await pending;
  assert.equal(render().selectedSummary.status, "failed"); assert.match(render().error, /completion/);
  assert.equal(render().generating, false); app.unmount();
});

function chatRuntime(fetch, refresh = async () => {}) {
  let messages = [], refreshes = 0;
  const app = runtime({ fetch });
  const props = id => ({ chatId: id, chat: { id, documents: [] }, messages,
    setMessages: next => { messages = typeof next === "function" ? next(messages) : next; },
    refreshMessages: async () => { refreshes++; await refresh(); }, getToken: () => "__cookie__",
    attachment: null, clearComposerAttachment() {}, });
  return { app, render: id => app.render("src/hooks/useChatStream.ts", "useChatStream", props(id)),
    messages: () => messages, refreshes: () => refreshes };
}

test("chat accepts final done without newline and completes normally", async () => {
  const source = stream(); const chat = chatRuntime(async () => new Response(source.body));
  const pending = chat.render(1).sendQuestionText("question");
  source.send('{"type":"token","content":"Answer"}\n{"type":"done","sources":[]}'); source.end();
  await pending;
  assert.equal(chat.messages().find(m => m.role === "assistant").status, "completed");
  assert.equal(chat.refreshes(), 1); assert.equal(chat.render(1).sending, false); chat.app.unmount();
});

test("chat missing done and disconnect report errors rather than success", async () => {
  for (const disconnect of [false, true]) {
    const source = stream(); const chat = chatRuntime(async () => new Response(source.body));
    const pending = chat.render(1).sendQuestionText("question");
    await tick(); source.send('{"type":"token","content":"Partial"}\n');
    if (disconnect) source.fail(); else source.end();
    await pending;
    assert.ok(chat.render(1).composerError); assert.equal(chat.render(1).sending, false);
    assert.equal(chat.messages().some(m => m.role === "assistant" && m.status === "completed"), false);
    assert.equal(source.body.locked, false); chat.app.unmount();
  }
});

test("chat switch during fetch ignores late events, navigation, refresh and cleanup", async () => {
  const first = deferred(), second = stream(); let n = 0, firstSignal;
  const chat = chatRuntime((url, options) => { if (++n === 1) { firstSignal = options.signal; return first.promise; } return Promise.resolve(new Response(second.body)); });
  const a = chat.render(1).sendQuestionText("old");
  const b = chat.render(2).sendQuestionText("new");
  first.resolve(json({})); await a;
  assert.equal(firstSignal.aborted, true); assert.equal(chat.render(2).sending, true);
  assert.equal(chat.refreshes(), 0); assert.equal(chat.app.events.length, 0);
  second.send('{"type":"done","sources":[]}\n'); await b;
  assert.equal(chat.refreshes(), 1); chat.app.unmount();
});

test("chat Stop is immediate while fetch ignores abort, and old completion cannot stop the next send", async () => {
  const old = deferred(), current = stream(); let count = 0;
  const chat = chatRuntime(() => ++count === 1 ? old.promise : Promise.resolve(new Response(current.body)));
  const a = chat.render(1).sendQuestionText("old"); chat.render(1).stopGeneration();
  assert.equal(chat.render(1).sending, false);
  const b = chat.render(1).sendQuestionText("new");
  old.resolve(json({})); await a;
  assert.equal(chat.render(1).sending, true); assert.equal(chat.refreshes(), 0);
  current.send('{"type":"done","sources":[]}\n'); await b; chat.app.unmount();
});

test("chat unmount cancels an open reader without refetch or navigation", async () => {
  const source = stream(); const chat = chatRuntime(async () => new Response(source.body));
  const pending = chat.render(1).sendQuestionText("question"); await tick(); chat.app.unmount(); await pending;
  assert.equal(source.cancelled(), 1); assert.equal(chat.refreshes(), 0); assert.equal(chat.app.events.length, 0);
});

test("summary validates terminal ownership and mode, and accepts a valid final buffer", async () => {
  for (const completed of [summary(1), summary(2), { ...summary(1), mode: "transcription" }, { ...summary(1), status: "generating" }]) {
    const source = stream(); const app = runtime({ fetch: async () => new Response(source.body) });
    const api = app.load("src/lib/summary-api.ts"); const events = [];
    const pending = api.streamDocumentSummary("__cookie__", 1, 1, e => events.push(e));
    source.send(JSON.stringify({ type: "done", summary: completed })); source.end();
    if (completed.document_id === 1 && completed.mode === "summary" && completed.status === "completed") {
      assert.equal((await pending).id, 1); assert.equal(events.length, 1);
    } else { await assert.rejects(pending, /Invalid summary completion/); assert.equal(events.length, 0); }
  }
});

test("switching documents during streaming summary cancels only the old record", async () => {
  const streams = [stream(), stream()], cancellations = [];
  const app = runtime({ fetch: async (url) => {
    if (url.includes('/cancel')) { cancellations.push(url); return json({}); }
    return new Response(streams[url.includes('/documents/1/') ? 0 : 1].body);
  } });
  const render = id => app.render("src/hooks/useDocumentSummary.ts", "useDocumentSummary", { token: "__cookie__", chatId: 1, documentId: id, mode: "summary" });
  const old = render(1).generateSummary();
  streams[0].send('{"type":"start","summary_id":10}\n'); await tick();
  const current = render(2).generateSummary();
  await old;
  assert.equal(streams[0].cancelled(), 1); assert.match(cancellations[0], /documents\/1\/summaries\/10\/cancel/);
  assert.equal(render(2).generating, true);
  streams[1].send(JSON.stringify({ type: "done", summary: summary(2) }) + '\n'); await current;
  assert.equal(render(2).selectedSummary.document_id, 2); app.unmount();
});

test("assistant initial history cannot overwrite a newer send", async () => {
  const history = deferred();
  const app = runtime({ fetch: (url, options) => options.method === "GET" ? history.promise
    : Promise.resolve(json({ user_message: message(1), assistant_message: message(2) })) });
  const render = () => app.render("src/hooks/useSummaryAssistant.ts", "useSummaryAssistant", { token: "__cookie__", chatId: 1, documentId: 1 });
  const pending = render().loadMessages(); await render().sendMessage("new instruction");
  history.resolve(json({ messages: [] })); await pending;
  assert.equal(render().messages.length, 2); app.unmount();
});

test("older message page arriving after a chat switch cannot merge into the new history", async () => {
  const oldPage = deferred(); let pageSignal;
  const app = runtime({ passive: false, fetch: (url, options) => {
    if (url.includes('cursor=')) { pageSignal = options.signal; return oldPage.promise; }
    return Promise.resolve(new Response(JSON.stringify([message(url.includes('/chats/2/') ? 20 : 10)]), { headers: { "X-Next-Cursor": "older" } }));
  } });
  const render = id => app.render("src/hooks/useChat.ts", "useChat", id);
  await render(1).refreshMessages(); const pending = render(1).loadOlderMessages();
  await render(2).refreshMessages(); oldPage.resolve(json([message(1)])); await pending;
  assert.equal(pageSignal.aborted, true); assert.equal(render(2).messages.some(m => m.id === 1), false); app.unmount();
});

test("Stop during draft creation aborts creation and prevents attachment or stream requests", async () => {
  const create = deferred(); let signal, posts = 0;
  const app = runtime({ fetch: async () => { posts++; throw new Error("No stream expected"); } });
  let messages = [];
  const props = { chatId: 0, chat: null, messages, setMessages: next => { messages = typeof next === 'function' ? next(messages) : next; },
    refreshMessages: async () => {}, getToken: () => "__cookie__", attachment: null, clearComposerAttachment() {},
    createPersistedChat: abort => { signal = abort; return create.promise; }, };
  const render = () => app.render("src/hooks/useChatStream.ts", "useChatStream", props);
  const pending = render().sendQuestionText("new chat"); render().stopGeneration();
  assert.equal(signal.aborted, true); assert.equal(render().sending, false);
  create.resolve({ id: 9, documents: [] }); await pending;
  assert.equal(posts, 0); assert.equal(app.events.length, 0); app.unmount();
});

test("chat rejects invalid done and closes upstream on a server error event", async () => {
  for (const event of [{ type: "done", sources: null }, { type: "error", message: "Generation unavailable" }]) {
    const source = stream(); const chat = chatRuntime(async () => new Response(source.body));
    const pending = chat.render(1).sendQuestionText("question");
    source.send(JSON.stringify(event) + '\n'); await pending;
    assert.ok(chat.render(1).composerError); assert.equal(source.cancelled(), 1); chat.app.unmount();
  }
});

test("cancelled draft creation cannot update chat or clear a newer creation's busy state", async () => {
  const first = deferred(), second = deferred(); let calls = 0;
  const app = runtime({ passive: false, fetch: () => ++calls === 1 ? first.promise : second.promise });
  const render = () => app.render("src/hooks/useChat.ts", "useChat", null);
  const controller = new AbortController();
  const old = render().createPersistedChat(controller.signal);
  controller.abort(); assert.equal(render().creatingChat, false);
  const current = render().createPersistedChat();
  const rejected = assert.rejects(old, { name: "AbortError" });
  first.resolve(json({ id: 1, documents: [] })); await rejected;
  assert.equal(render().creatingChat, true); assert.equal(render().chat, null);
  second.resolve(json({ id: 2, documents: [] })); await current;
  assert.equal(render().creatingChat, false); assert.equal(render().chat.id, 2); app.unmount();
});


test("Stop after terminal done does not relabel completed content while refetch is pending", async () => {
  const source = stream(), refresh = deferred();
  const chat = chatRuntime(async () => new Response(source.body), () => refresh.promise);
  const pending = chat.render(1).sendQuestionText("question");
  source.send('{"type":"token","content":"Complete"}\n{"type":"done","sources":[]}\n');
  await tick(); chat.render(1).stopGeneration();
  assert.equal(chat.render(1).sending, false);
  assert.equal(chat.messages().every(message => message.status === "completed"), true);
  refresh.resolve(); await pending; chat.app.unmount();
});
