import assert from "node:assert/strict";
import test from "node:test";
import { runtime, tick } from "./helpers/hook-runtime.mjs";


const documentState = (processing_status, processing_progress, extra = {}) => ({
  id: 9,
  filename: "status.pdf",
  file_type: "pdf",
  pages_count: null,
  processing_status,
  processing_stage: processing_status === "processing" ? "analyzing_document" : processing_status,
  processing_progress,
  processing_error: null,
  created_at: "2026-09-21T12:00:00",
  ...extra,
});


function apiStub(overrides = {}) {
  return {
    getCurrentUser: async () => ({ id: 1, name: "User", picture: null }),
    getChats: async () => ({ items: [], nextCursor: null }),
    getMessages: async () => ({ items: [], nextCursor: null }),
    getChat: async () => ({
      id: 1,
      title: "Chat",
      is_pinned: false,
      is_archived: false,
      created_at: "2026-09-21T12:00:00",
      documents: [],
    }),
    uploadDocument: async () => documentState("processing", 5, {
      processing_stage: "uploaded",
    }),
    ...overrides,
  };
}


test("new-chat upload follows backend progress to ready and stops polling", async () => {
  const responses = [
    documentState("processing", 20),
    documentState("ready", 100),
  ];
  let polls = 0;
  const app = runtime({
    stubs: {
      "@/lib/chat-api": apiStub({
        getDocument: async () => {
          polls += 1;
          return responses.shift();
        },
      }),
    },
  });
  const render = () => app.render("src/hooks/useChat.ts", "useChat", null);

  const target = { files: [{ name: "status.pdf", size: 10 }], value: "selected" };
  await render().handleUpload({ target });
  render();

  app.flushTimers();
  await tick();
  assert.equal(render().attachment.status, "processing");
  assert.equal(render().attachment.stage, "analyzing_document");
  assert.equal(render().attachment.progress, 20);

  app.flushTimers();
  await tick();
  assert.equal(render().attachment.status, "ready");
  assert.equal(render().attachment.stage, "ready");
  assert.equal(render().attachment.progress, 100);

  app.flushTimers();
  await tick();
  assert.equal(polls, 2);
  app.unmount();
});


test("new-chat upload reflects terminal processing failure and error", async () => {
  let polls = 0;
  const app = runtime({
    stubs: {
      "@/lib/chat-api": apiStub({
        getDocument: async () => {
          polls += 1;
          return documentState("failed", 20, {
            processing_stage: "retry_exhausted",
            processing_error: "Document processing failed. Please retry.",
          });
        },
      }),
    },
  });
  const render = () => app.render("src/hooks/useChat.ts", "useChat", null);

  await render().handleUpload({
    target: { files: [{ name: "status.pdf", size: 10 }], value: "selected" },
  });
  render();
  app.flushTimers();
  await tick();

  assert.equal(render().attachment.status, "failed");
  assert.equal(render().attachment.stage, "retry_exhausted");
  assert.equal(render().attachment.error, "Document processing failed. Please retry.");
  app.flushTimers();
  await tick();
  assert.equal(polls, 1);
  app.unmount();
});


test("removing an attachment cancels its document polling loop", async () => {
  let polls = 0;
  const app = runtime({
    stubs: {
      "@/lib/chat-api": apiStub({
        getDocument: async () => {
          polls += 1;
          return documentState("processing", 20);
        },
      }),
    },
  });
  const render = () => app.render("src/hooks/useChat.ts", "useChat", null);

  await render().handleUpload({
    target: { files: [{ name: "status.pdf", size: 10 }], value: "selected" },
  });
  render();
  app.flushTimers();
  await tick();
  assert.equal(polls, 1);

  await render().handleRemoveAttachment();
  assert.equal(render().attachment, null);
  app.flushTimers();
  await tick();
  assert.equal(polls, 1);
  app.unmount();
});


test("existing-chat refresh owns polling without a duplicate document loop", async () => {
  const processingDocument = documentState("processing", 5, {
    processing_stage: "queued",
  });
  const chat = {
    id: 1,
    title: "Chat",
    is_pinned: false,
    is_archived: false,
    created_at: "2026-09-21T12:00:00",
    documents: [processingDocument],
  };
  let documentPolls = 0;
  let chatRefreshes = 0;
  const app = runtime({
    stubs: {
      "@/lib/chat-api": apiStub({
        getChat: async () => {
          chatRefreshes += 1;
          return chat;
        },
        attachDocument: async () => chat,
        getDocument: async () => {
          documentPolls += 1;
          return processingDocument;
        },
      }),
    },
  });
  const render = () => app.render("src/hooks/useChat.ts", "useChat", 1);

  render();
  await tick();
  await render().handleUpload({
    target: { files: [{ name: "status.pdf", size: 10 }], value: "selected" },
  });
  render();
  app.flushTimers();
  await tick();
  render();
  app.flushTimers();
  await tick();

  assert.ok(chatRefreshes >= 2);
  assert.equal(documentPolls, 0);
  app.unmount();
});


function jsx(type, props, key) {
  return { type, props: props ?? {}, key };
}


function treeText(value) {
  if (value === null || value === undefined || typeof value === "boolean") return "";
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (Array.isArray(value)) return value.map(treeText).join(" ");
  if (typeof value.type === "function") return treeText(value.type(value.props));
  return treeText(value.props?.children);
}


test("dashboard refreshes a processing document through its terminal state", async () => {
  const responses = [
    documentState("processing", 20),
    documentState("ready", 100),
  ];
  let documentPolls = 0;
  const app = runtime({
    fetch: async (url) => {
      const path = new URL(url).pathname;
      if (path === "/auth/me") {
        return new Response(JSON.stringify({ id: 1, email: "user@example.test", name: "User" }));
      }
      if (path === "/chats") {
        return new Response("[]");
      }
      if (path === "/documents/9") {
        documentPolls += 1;
        return new Response(JSON.stringify(responses.shift()));
      }
      if (path === "/documents") {
        return new Response(JSON.stringify([
          documentState("processing", 5, { processing_stage: "uploaded" }),
        ]));
      }
      throw new Error(`Unexpected request: ${url}`);
    },
    stubs: {
      "react/jsx-runtime": { jsx, jsxs: jsx, Fragment: "fragment" },
      "lucide-react": new Proxy({}, { get: () => () => null }),
      "@/components/documents/UploadGuidance": { default: () => null },
      "@/hooks/useUploadPolicy": { useUploadPolicy: () => ({ policy: null }) },
    },
  });
  const render = () => app.render("src/app/dashboard/page.tsx", "default");

  render();
  app.flushTimers();
  await tick();
  assert.match(treeText(render()), /5\s*%/);

  app.flushTimers();
  await tick();
  assert.match(treeText(render()), /20\s*%/);

  app.flushTimers();
  await tick();
  assert.match(treeText(render()), /Ready/);
  app.flushTimers();
  await tick();
  assert.equal(documentPolls, 2);
  app.unmount();
});
