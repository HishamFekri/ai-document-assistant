# Summary generation concurrency (Batch 7)

The generation context is `(chat_id, document_id, mode)`. Existing routes check
that the user owns the chat and document and that the document belongs to the
chat. Modes `summary` and `transcription` are independent. The storage model,
public statuses and response schemas are unchanged; no migration is required.

## Ownership and versions

All three entry points (ordinary HTTP, NDJSON streaming, and chat-triggered
generation) acquire a nonblocking PostgreSQL session advisory lock before
allocating a record or making an AI call. Namespace `0x53554D47` is distinct from
Batch 6 document processing. The key is a deterministic signed 32-bit SHA-256
prefix of the context. A hash collision only causes extra contention; every SQL
write still predicates on the full context and record ID.

One physical database connection remains checked out until generation finishes,
fails, or unwinds after cancellation/disconnection. No ORM transaction remains
open across provider calls. The connection is also used for short lifecycle and
context-read transactions. An invalidated connection cannot reconnect and write
as the old owner. Uncertain lock acquisition/release invalidates the connection
instead of returning a possibly locked session to the pool.

Short transaction advisory locks in namespace `0x53554D54` serialize version
allocation, completion, cancellation, selection, deletion and cleanup. MAX+1 is
calculated under this lock and INSERT commits before release. The existing
`uq_chat_document_summary_mode_version` constraint provides an additional
backstop. Versions remain unique among retained records in a context; deleting
all history can reuse version numbers, as before. Nullable legacy chat contexts
can be stopped safely but cannot start new generation.

Duplicate ordinary HTTP requests return 409 with a fixed message. Duplicate
streams emit the existing `error` event without a `start` event, preventing the
duplicate client from treating the original generation as its own cancellable
stream. No new version or provider call occurs. The chat adapter uses the same
guard; its existing enclosing chat error handler handles a busy context.

## Lifecycle and cancellation

- `pending -> generating` is a conditional update that must succeed before AI
  work starts. An existing terminal record cannot be restarted in place.
- `generating -> completed` is conditional. Content, selection and cleanup
  commit together only when completion wins that update.
- `pending/generating -> cancelled` is conditional. A late completion or failure
  cannot overwrite cancellation; cancellation also cannot overwrite completion.
- `pending/generating -> failed` is conditional and stores Batch 4 safe errors.
- Regenerate creates a new version after the previous owner releases its lock.

The cancel endpoint uses the separate transaction lock and does not wait for the
long generation claim. The claim remains held until the provider/stream returns.
Before each subsequent paid call, generation checks its current state again,
including instruction interpretation, transcription pages and fallback calls.
Already-sent provider requests may still finish and incur charges. Cancellation
may also occur just after a pre-call check; final conditional writes remain the
authority on whether a result can be published.

Streaming cancellation saves only the partial content already emitted by that
stream. Its owner may fill an empty cancelled record once, without reselecting
it or overwriting an existing partial. Late sections/final results are discarded.
Provider streams and delegated generators close when the consumer unwinds.
Explicit deletion cannot be undone by late lifecycle writes.

## Selection and cleanup

A successful new result becomes selected. An older legacy generation finishing
after a newer visible terminal result cannot replace it. Selection mutations
share the context transaction lock. Cleanup deletes only older, unselected
completed/failed/cancelled records, preserving pending/generating rows, newer
versions, the retained record and other contexts/modes.

Cleanup also tries a transaction lock in the generation namespace. This is
reentrant for the owning physical session. A different request skips cleanup
while generation owns the context, including after cancellation while provider
work is still in flight. Skipped cleanup is deferred to a later successful
completion/selection; there is no new background cleanup scheduler.

## Deployment and recovery limits

Use direct PostgreSQL or session pooling, with the application's default READ
COMMITTED isolation. Transaction pooling cannot preserve session advisory lock
ownership. Budget one database connection per active summary and drain older
API processes before enabling these guarantees; older code does not obey the
new locks. No deployment or database configuration was changed in this batch.

A hard process failure can leave a pending/generating record after PostgreSQL
releases the dead session's lock. Use the existing list/read and cancel endpoint,
then Generate/Regenerate. Starts deliberately do not repeat work of uncertain
outcome automatically. If the old provider/session has not exited, retries remain
busy. Persisting a partial result on transport disconnect is best effort; an
explicit successful cancellation is durable. There is no durable request-ID
deduplication for requests arriving after a previous generation has finished.

## Validation

Database-independent commands, run from `backend`:

```text
python -B tests/test_summary_concurrency.py
python -B tests/test_public_generation_errors.py
python -B tests/test_database_safety.py
```

The new suite uses only SQLite memory for actual lifecycle SQL, mocks PostgreSQL
lock boundaries and providers, and blocks application engines/network access.
It validates overlapping requests but does not establish live PostgreSQL safety.

Six live PostgreSQL tests are prepared in
`tests/test_summary_concurrency_postgres.py`. Run them only through unchanged
Batch 1 fixtures with an explicitly configured safe local `TEST_DATABASE_URL`:

```text
python -m pytest tests/test_summary_concurrency_postgres.py
```

The fixtures create an isolated per-run database and never fall back to the
application database. These tests were not run because `TEST_DATABASE_URL` was
unavailable. No real providers, production data, or workers were used.
