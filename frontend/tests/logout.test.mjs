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

function application(fetch) {
  const events = [], states = [], modules = new Map();
  const router = { replace: (url) => events.push(["replace", url]), push: (url) => events.push(["push", url]) };
  const hooks = {
    useCallback: (callback) => callback,
    useMemo: (create) => create(),
    useLayoutEffect: () => {},
    useEffect: () => {}, // No background queries in this isolated handler test.
    useRef: (current) => ({ current }),
    useState: (initial) => {
      const index = states.length;
      const value = index === 0 ? { id: 1, name: "Synthetic", email: "user@example.test" } : initial === true ? false : initial;
      states.push(value);
      return [value, (next) => { states[index] = next; events.push(["state", index]); }];
    },
  };
  function load(relative) {
    if (modules.has(relative)) return modules.get(relative);
    const source = readFileSync(path.join(root, relative), "utf8");
    const output = ts.transpileModule(source, { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
    } }).outputText;
    const exports = {};
    modules.set(relative, exports);
    const dependency = (name) => {
      if (name === "@/hooks/useRequestScope") return load("src/hooks/useRequestScope.ts");
      if (name === "@/lib/request-scope") return load("src/lib/request-scope.ts");
      if (name === "@/lib/pagination") return load("src/lib/pagination.ts");
      if (name === "@/lib/logout") return load("src/lib/logout.ts");
      if (name === "@/lib/chat-api") return {};
      if (name === "@/hooks/useUploadPolicy") return { useUploadPolicy: () => ({ policy: null }) };
      if (name === "@/components/documents/UploadGuidance") return { default: () => null };
      if (name === "next/navigation") return { useRouter: () => router };
      if (name === "react") return hooks;
      if (name === "lucide-react") return new Proxy({}, { get: () => () => null });
      return require(name);
    };
    vm.runInNewContext(output, { exports, require: dependency, process: { env: {} }, fetch,
      window: { alert: (message) => events.push(["error", message]) }, console }, { filename: relative });
    return exports;
  }
  return { load, events, states };
}

function findLogout(element) {
  if (!element || typeof element !== "object") return null;
  if (element.props?.onClick?.name === "logout") return element.props.onClick;
  const children = element.props?.children;
  for (const child of Array.isArray(children) ? children : [children]) {
    const found = findLogout(child);
    if (found) return found;
  }
  return null;
}

for (const view of ["chat", "dashboard"]) {
  function handler(app) {
    return view === "chat" ? app.load("src/hooks/useChat.ts").useChat(null).logout
      : findLogout(app.load("src/app/dashboard/page.tsx").default());
  }

  test(`${view}: waits for server success before clearing state and redirecting`, async () => {
    let complete;
    const app = application((url, options) => {
      assert.equal(url, "http://localhost:8000/auth/logout");
      assert.equal(options.method, "POST");
      assert.equal(options.credentials, "include");
      return new Promise((resolve) => { complete = resolve; });
    });
    const logout = handler(app);
    assert.equal(typeof logout, "function");
    const pending = logout();
    assert.deepEqual(app.events, []);
    assert.notEqual(app.states[0], null);
    complete(new Response(null, { status: 200 }));
    await pending;
    assert.equal(app.states[0], null);
    assert.deepEqual(app.events.at(-1), [view === "chat" ? "replace" : "push", "/"]);
    assert.equal(app.events.filter(([kind]) => kind === "error").length, 0);
  });

  test(`${view}: HTTP failure keeps state and reports unconfirmed logout`, async () => {
    for (const status of [401, 403, 429, 500, 503]) {
      const app = application(async () => new Response("private proxy details", { status }));
      await handler(app)();
      assert.notEqual(app.states[0], null);
      assert.equal(app.events.length, 1);
      assert.equal(app.events[0][0], "error");
      assert.match(app.events[0][1], /Could not confirm sign out.*may still be signed in/);
      assert.doesNotMatch(app.events[0][1], /private/);
    }
  });

  test(`${view}: network failure does not redirect or clear state`, async () => {
    const app = application(async () => { throw new Error("private backend URL"); });
    await handler(app)();
    assert.notEqual(app.states[0], null);
    assert.deepEqual(app.events.map(([kind]) => kind), ["error"]);
    assert.doesNotMatch(app.events[0][1], /private/);
  });
}
