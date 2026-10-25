// Coalesces rapid updates, such as streamed answer tokens, into at most one flush
// per animation frame. Nothing is delayed beyond the next frame, and the caller
// flushes immediately when the stream ends.

type Schedule = (callback: () => void) => number;
type Cancel = (handle: number) => void;


// Falls back to a ~60 Hz timer where animation frames are unavailable.
const frames =
  () => typeof window.requestAnimationFrame === "function";

const nextFrame: Schedule = (callback) =>
  frames()
    ? window.requestAnimationFrame(callback)
    : window.setTimeout(callback, 16);

const cancelFrame: Cancel = (handle) =>
  frames()
    ? window.cancelAnimationFrame(handle)
    : window.clearTimeout(handle);


export function createFrameBatcher(
  flush: () => void,
  schedule: Schedule = nextFrame,
  cancel: Cancel = cancelFrame,
) {
  let handle: number | null = null;

  function clear() {
    if (handle !== null) {
      cancel(handle);
      handle = null;
    }
  }

  return {
    request() {
      if (handle === null) {
        handle = schedule(() => {
          handle = null;
          flush();
        });
      }
    },

    flushNow() {
      clear();
      flush();
    },

    cancel: clear,
  };
}
