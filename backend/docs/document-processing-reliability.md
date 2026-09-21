# Document dispatch, ownership and retries

This batch keeps the existing database schema and public processing statuses:
`processing`, `ready`, `failed`. Stage strings identify dispatch failure,
retryable/permanent failure and exhausted retries. No migration, outbox, startup
sweeper or production job was run or introduced.

## Execution ownership

Both `document_processing_service.process_document` and the legacy parser entry
point use the same PostgreSQL session advisory lock, keyed by a fixed namespace
and document ID. The claim is nonblocking. Another invocation returns `busy`
without extraction, uploads, chunk creation or embeddings. A ready document is
skipped only after Batch 5's SQL completeness check. Ready-but-incomplete documents
return `embedding_recovery_required` internally and require the explicit Batch 5
maintenance workflow; duplicate delivery does not silently reprocess them.

The claim retains one physical database connection for its lifetime, without an
open transaction while waiting on providers. Each state transition, checkpoint
and vector batch uses a short ORM session/transaction. Processing locks the
document row during each write transaction to coordinate with deletion. A lost or
invalidated connection cannot reconnect through that claim. Cleanup releases the
session advisory lock before returning the connection to the pool; uncertain
acquisition or failed cleanup invalidates the connection instead.

Deployment requires direct PostgreSQL or session pooling. **Transaction pooling
is incompatible with session advisory locks.** Check the actual deployment
connection mode before rollout; a hostname alone cannot prove it. Ensure the
existing connection budget accommodates one retained connection per active
document execution. This batch does not change pool sizes or add indexes.
Workers on other machines/containers must also be able to read the uploaded source
at its stored path; shared file storage/mounts remain a deployment requirement.

All worker processes must run this implementation. Stop/drain old workers before
rollout: an older worker does not honor the claim. Every processing caller must
continue to use the claimed entry point. Batch 5 maintenance should still run
with processing of its selected documents quiesced.

## Checkpoints and partial work

Uploaded source files remain authoritative; a stale task's file-path argument
cannot override the document's stored path. With no existing chunks, extraction
runs outside database transactions. Matching existing asset rows are reused;
new assets and chunks are committed together, with NULL vectors initially.
There is no wholesale asset/chunk deletion. Existing chunk IDs and valid vectors
are preserved on retries. Existing chunks constitute the recoverable checkpoint,
so retries with chunks do not invoke extraction or Cloudinary again.

Missing/unusable vectors are selected and validated using Batch 5's rules. Each
batch contains at most 32 chunks and commits only after count, index correspondence,
dimension and value validation. Conditional updates recheck content/eligibility.
Current source metadata is merged with embedding provenance at update time.
Final completeness and the ready transition share one transaction. Failure rolls
back only the current transaction; committed checkpoints and earlier vectors stay.

Source extraction before a checkpoint is not itself resumable. Datalab work may
repeat if a worker fails before the checkpoint commits. Cloudinary already uses
content-based public IDs in a document-specific folder, authenticated delivery
and `overwrite=False`; repeated requests do not overwrite existing objects.
Uncertain provider outcomes can still be billed twice. This is not exactly-once
external execution. If legacy assets no longer match the new extraction, processing
stops for explicit review before adding alternate copies or removing references.
Existing valid data is not wiped to force a retry to succeed.

## Dispatch and explicit retry

Upload validates/saves the file and commits its document, then ends the refresh
transaction before queue submission. A known submission failure changes only
`processing`/`uploaded` rows to `failed`/`dispatch_failed`, with a fixed public error.
The route returns the saved document state and retains its source file. If an
ambiguous broker response has already started a worker, the failure handler does
not overwrite a later processing stage or completed result.

The owner can explicitly submit `POST /documents/{document_id}/retry` with the
same authentication used by the other document routes. It returns the existing
DocumentResponse shape. An active execution returns 409; missing/not-owned rows
return 404. A verified ready document returns unchanged. An incomplete ready
document returns 409 directing the operator to explicit embedding recovery.

For failed/interrupted work, retry resets the existing status/stage to
`processing`/`uploaded` and releases its claim before submitting the task.
Existing source/chunks/assets are retained. It deliberately permits an explicit
retry after a permanent failure or exhausted retries, for example after fixing a
configuration issue. It does not add a frontend retry control in this batch.

The commit-before-submission gap remains: an API crash can leave an uploaded row
without a queued job. The same retry endpoint recovers that state. This design
does not claim atomic publication or automatic stale-job discovery. Operators
should review stalled documents and retry them explicitly.

## Celery and BackgroundTasks

Known timeouts, network failures, retryable HTTP responses and selected database
disconnect/serialization failures qualify for Celery retry. Datalab's exhausted
429/5xx path now retains a typed transient cause. File/structure/validation errors
and unknown deterministic failures do not qualify based on error-message text.

Celery schedules at most three retries after the initial attempt, with delays of
2, 4 and 8 seconds (the delay helper caps at 60 seconds). A permanent failure is
persisted immediately and duplicate delivery skips it. Exhaustion records
`retry_exhausted` where ownership/database availability allow. Broker failure
while scheduling a retry leaves the document explicitly retryable and raises a
sanitized worker error. There is no blanket Exception autoretry.

Existing Datalab/Voyage per-request retry limits still apply inside an attempt.
The Celery retry counter bounds that delivery's automatic retries; it is not a
persistent lifetime attempt counter across independently submitted tasks. Explicit
operator retries start a new bounded sequence. No attempt-count field was added.

Late acknowledgment remains enabled. `task_reject_on_worker_lost=False` prevents
automatic poison-message loops after a killed child worker. A killed/interrupted
job may leave `processing` visible until explicit retry. Once PostgreSQL releases
the old session lock, that retry can claim and resume its checkpoint. Whole-worker
or broker loss may still redeliver unacknowledged messages; the claim/completed
guard applies. No claim is made that Celery provides exactly-once delivery.

`TASK_QUEUE=background` is supported for an explicitly configured single-process
web deployment. FastAPI starts processing after the upload response, and a shared
in-process semaphore caps heavy document work at
`RESOURCE_PROCESSING_CONCURRENCY` (default one). Capacity deferrals retry with the
same 2, 4, 8, 16, 32, then 60 second capped backoff as Celery without consuming
the three-retry genuine-failure budget. Unknown values fail visibly rather than
silently selecting a fallback.

BackgroundTasks are not durable. A web-process restart, redeploy, crash or service
suspension loses pending in-memory work. Background-mode queued rows therefore
remain protected while their retry loop refreshes them, but become eligible for
the existing 15-minute stale reconciliation after that loop disappears. The
stored Cloudinary original and explicit retry endpoint remain available. Celery
is recommended whenever a separate durable worker exists.

## Deletion and diagnostics

Deletion remains available during processing. The worker rechecks document
existence at transaction boundaries, including immediately before every checkpoint
and vector write. A deleted row stops execution; it is never recreated. Foreign
keys and short document row locks protect checkpoint commits against deletion.
An already-started extraction/provider operation may finish before the worker
observes deletion. In-flight Cloudinary uploads can therefore leave remote objects;
broader storage cleanup remains separate Batch 3 follow-up work. This batch adds
no extraction cache or temporary image files requiring another cleanup policy.

Focused lifecycle logs contain document IDs, states, counts and retry delays.
Processing failure diagnostics reuse Batch 4's bounded exception-type/frame
logging with a document operation; public errors and exceptions sent to Celery
use fixed messages. A queued-question wake-up failure cannot repeat successfully
completed document processing.

## Validation and rollout

Database-free tests block real engines/networking and use synthetic credentials:

```text
python -B tests/test_document_processing_reliability.py
python -B tests/test_embedding_recovery.py
python -B tests/test_public_generation_errors.py
python -B tests/test_database_safety.py
```

Three PostgreSQL claim tests are prepared in
`tests/test_document_processing_claim_postgres.py`. With a separately provisioned,
isolated local TEST_DATABASE_URL, run them through the unchanged conftest safety
fixtures using pytest. They check competing physical sessions, release after
failure, and refusal to reuse an invalidated claim. They perform no provider calls.
Do not bypass those fixtures or substitute the application's database URL.
Live PostgreSQL concurrency and broker/worker integration remain to be verified
before deployment; no test database was supplied during this batch.

References: [PostgreSQL advisory locks](https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS),
[Celery worker-loss behavior](https://docs.celeryq.dev/en/stable/userguide/configuration.html#task-reject-on-worker-lost).
