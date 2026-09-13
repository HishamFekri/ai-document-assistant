# Batch 13: frontend reliability

The baseline is Batch 11 at `223b7b4`, including Batch 12 retrieval behavior.
This batch changes only frontend request ownership, stream consumption and the
small metadata configuration. No backend, migration, RAG or provider changes.

## Request ownership

Chat workspaces remount when the chat route changes; summary panels remount for
chat/document/mode/session changes and unmount when closed. A request scope also
invalidates pending work at committed scope changes, unmount and confirmed logout.
Transport abort is not the only safeguard: callbacks, errors and cleanup must
still own their request before changing state or navigating. New requests supersede
older requests in the same channel. Message pagination has a separate channel so
loading an older page can coexist with a refresh of recent messages.

Summary and assistant reads are invalidated before writes/generation, preventing
old history from replacing a new result. Summary completion validates the chat,
document, mode, ID and terminal status before delivery. A stale start event cannot
replace a newer run's cancellation ID. Assistant send/reset operations are
serialized, and duplicate message IDs are merged.

## Streams and Stop

The shared NDJSON reader incrementally decodes UTF-8, processes the last buffered
line even without a newline, and requires a terminal `done`. Malformed data,
premature EOF and network failure do not count as successful completion. A valid
done completes immediately; later frames are ignored. Reader cancellation and
lock release run on completion, error and abort, including a pending read.

Stop updates local chat/summary state immediately and invalidates the request
before a late continuation can run. It also cancels a pending assistant instruction
without starting subsequent summary generation. Draft creation/attachment stages
receive abort signals and check ownership before continuing.

Summary Stop closes the stream even before `start`. If the server summary ID is
known, cancellation is sent separately for that captured record. Otherwise the
existing backend disconnect cleanup owns cancellation. The browser does not wait
indefinitely for an ID. Cancellation requests are best effort: aborting a browser
request cannot undo an accepted write or guarantee immediate interruption of a
synchronous provider call already running on the server. Existing backend claim
and permit lifetimes remain authoritative. Paid POST requests are not retried
automatically after ambiguous disconnections.

## Pagination and configuration

Batch 11 limits, signed cursors and Load older messages remain unchanged. Older
loaded pages survive refetches; successful reconciliation removes this run's
temporary IDs and deduplicates persisted IDs. Stop creates locally stopped content;
a later successful refetch reconciles it with persisted history. No virtualization
or broad rendering redesign was introduced.

`metadataBase` uses `NEXT_PUBLIC_SITE_URL`, otherwise the HTTPS
`VERCEL_PROJECT_PRODUCTION_URL`, otherwise `http://localhost:3000`. Set the first
variable to the canonical public origin for deployments outside Vercel or using a
custom domain. The local fallback is not a claim about the production hostname.

## Offline validation

Run all four `tests/*.test.mjs` suites with Node's test runner, TypeScript with
`node node_modules/typescript/bin/tsc --noEmit`, the production build with
`node node_modules/next/dist/bin/next build`, and ESLint. Reliability tests execute
the real hook/API code with controlled lifecycle effects, promises, timers and
ReadableStreams. All fetches are mocked; no provider or backend is contacted.

These are focused regression tests, not browser end-to-end or load tests. Staging
should still exercise rapid route/document changes, Stop before and after start,
offline/reconnect behavior, and scrolling after loading older messages in a real
browser, using mocked providers. Verify canonical social-image URLs against the
chosen deployment origin. Backend streaming tests were not rerun because no
backend code changed.
