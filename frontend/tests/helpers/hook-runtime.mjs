import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
export const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
export const tick = () => new Promise(resolve => setImmediate(resolve));

// Runs the actual hook bodies with dependency-aware memoization, committed
// layout/effect cleanup and explicitly controlled timers. No real requests.
export function runtime({ fetch = () => { throw new Error("Unexpected network"); }, stubs = {}, passive = true } = {}) {
  const slots = [], modules = new Map(), timers = new Map(), events = [];
  let index = 0, nextTimer = 0, layouts = [], effects = [];
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  function memo(create, deps) {
    const key = index++;
    if (!slots[key] || !same(slots[key].deps, deps)) slots[key] = { value: create(), deps };
    return slots[key].value;
  }
  function effect(callback, deps, queue) {
    const key = index++;
    if (!slots[key] || !same(slots[key].deps, deps)) {
      const old = slots[key];
      slots[key] = { deps, cleanup: null };
      queue.push(() => { old?.cleanup?.(); slots[key].cleanup = callback(); });
    }
  }
  const hooks = {
    useState(initial) {
      const key = index++;
      if (!(key in slots)) slots[key] = { value: typeof initial === "function" ? initial() : initial };
      return [slots[key].value, next => { slots[key].value = typeof next === "function" ? next(slots[key].value) : next; }];
    },
    useRef(initial) { return memo(() => ({ current: initial }), []); },
    useMemo: memo,
    useCallback: (fn, deps) => memo(() => fn, deps),
    useLayoutEffect: (fn, deps) => effect(fn, deps, layouts),
    useEffect: (fn, deps) => effect(fn, deps, effects),
  };
  const schedule = fn => { timers.set(++nextTimer, fn); return nextTimer; };
  function load(file) {
    if (modules.has(file)) return modules.get(file);
    const exports = {}; modules.set(file, exports);
    const code = ts.transpileModule(readFileSync(path.join(root, file), "utf8"), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
    const require = name => {
      if (name in stubs) return stubs[name];
      if (name === "react") return hooks;
      if (name === "next/navigation") return { useRouter: () => router };
      if (name.startsWith("@/")) return load(`src/${name.slice(2)}.ts`);
      throw new Error(`Unexpected module ${name}`);
    };
    vm.runInNewContext(code, { exports, require, fetch, process: { env: {} },
      Error, AbortController, DOMException, TextDecoder, TextEncoder, ReadableStream, Response, URLSearchParams,
      Event, console: { error: (...args) => events.push(["error", ...args]) },
      window: { setTimeout: schedule, clearTimeout: id => timers.delete(id),
        setInterval: schedule, clearInterval: id => timers.delete(id),
        addEventListener() {}, removeEventListener() {}, dispatchEvent() {},
        alert: message => events.push(["alert", message]) },
      alert: message => events.push(["alert", message]),
    }, { filename: file });
    return exports;
  }
  const router = { push: url => events.push(["push", url]), replace: url => events.push(["replace", url]) };
  return {
    load, events,
    render(file, name, props) {
      index = 0; layouts = []; effects = [];
      const result = load(file)[name](props);
      layouts.forEach(fn => fn()); if (passive) effects.forEach(fn => fn());
      return result;
    },
    flushTimers() { const pending = [...timers.values()]; timers.clear(); pending.forEach(fn => fn()); },
    unmount() { for (const slot of slots) slot?.cleanup?.(); },
  };
}
