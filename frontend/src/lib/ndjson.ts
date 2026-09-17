/** Consume through the terminal event, including an unterminated final line. */
export async function readNDJSON(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: Record<string, unknown>) => void | Promise<void>,
  signal?: AbortSignal,
) {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "";
  let terminal = false;
  const checkAbort = () => {
    if (signal?.aborted) throw new DOMException("Request cancelled", "AbortError");
  };
  const cancel = () => { void reader.cancel().catch(() => {}); };
  signal?.addEventListener("abort", cancel, { once: true });
  async function line(raw: string) {
    checkAbort();
    if (!raw.trim() || terminal) return;
    let event: Record<string, unknown>;
    try {
      event = JSON.parse(raw);
      if (!event || typeof event !== "object" || typeof event.type !== "string") throw new Error();
    } catch {
      throw new Error("Invalid stream data");
    }
    await onEvent(event);
    checkAbort();
    terminal = event.type === "done";
  }
  try {
    checkAbort();
    while (!terminal) {
      const { done, value } = await reader.read();
      checkAbort();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const raw of lines) {
        await line(raw);
        if (terminal) break;
      }
      if (done) {
        if (!terminal) await line(buffer);
        if (!terminal) throw new Error("Stream ended before completion. Please retry.");
        break;
      }
    }
  } finally {
    signal?.removeEventListener("abort", cancel);
    // Also close an upstream stream that remains open after done or errors.
    try { await reader.cancel(); } catch { /* Preserve the original result. */ }
    reader.releaseLock();
  }
}
