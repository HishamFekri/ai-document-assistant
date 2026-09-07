# Resource admission (Batch 8)

Application admission uses the existing Redis and PostgreSQL services. No new
dependency, migration, accounting table, billing system, or queue replacement is
required. It bounds admitted starts and concurrent operations; it is not a dollar
budget or a complete DDoS defense.

## Endpoint classification and defaults

All authenticated requests use the verified JWT user ID. Rate policies are
independent: an expensive request does not also consume the ordinary API budget.
Windows slide continuously and use Redis server time.

| Class | Entry points / resources | Default rate | Per-user concurrency |
| --- | --- | --- | --- |
| Cheap reads | Chats, messages, documents, summaries, assets, `/auth/me` | 120/minute/user | Existing request handling |
| Ordinary writes | Chat/message CRUD, attachments, summary selection, preference resets | Same ordinary 120/minute/user budget | Existing context locks |
| Search | `POST /chats/{id}/search`, including query embeddings | 20/minute/user | 2 |
| AI chat | `POST /chats/{id}/ask`, `/ask/stream`, summary-assistant message POST; queued answer execution | 10 starts/minute/user | 2 |
| Upload/processing | `POST /documents`, `POST /documents/{id}/retry` | Shared 5 starts/hour/user | 1 processing job, plus serialized upload reservation |
| Summary/transcription | Direct generate and generate/stream; chat-triggered generation, in either mode | Shared 3 starts/hour/user | 1 across all documents/chats/modes |
| Authentication | Google token login, OAuth start/callback, logout | Shared 30/minute/peer IP | No additional permit |
| Recovery | Authenticated DELETE and summary cancellation | 30/minute/user | Existing context locks |

The summary assistant performs an instruction LLM call despite its earlier
"preferences only" route comment, so it uses chat admission. Intent detection,
title generation, retrieval embeddings and answer calls within one admitted chat
share that chat start and permit. Each document summary triggered from chat also
consumes one summary start and acquires summary capacity before allocation or
provider work. Direct summary dependencies pass their existing permit into the
service, avoiding double charging. Stream chunks consume no rate units.

Authenticated attempts consume their rate unit before route ownership/input
checks and concurrency admission; failed attempts are not refunded. Invalid JWTs
are rejected before user admission. OAuth exchanges use multiple auth requests.
Queued answers consume a chat start when execution begins. Worker retries use
the processing concurrency guard and Batch 6's existing bounded retry policy;
they do not consume a fresh upload start. Admission denial leaves queued answers
failed with the existing safe error; processing denial follows the retryable
processing path, without invoking extraction or embedding.

## Configuration and production requirements

`app/services/resource_limits.py` centralizes defaults. `.env.example` lists all
settings. Use `RESOURCE_<CATEGORY>_LIMIT` and
`RESOURCE_<CATEGORY>_WINDOW_SECONDS` for the table's rate categories (`API`,
`SEARCH`, `CHAT`, `UPLOAD`, `SUMMARY`, `AUTH`, `RECOVERY`). Values must be positive;
window maximum is 86400 seconds. Use `RESOURCE_<CATEGORY>_CONCURRENCY` for `CHAT`,
`SEARCH`, `SUMMARY`, `PROCESSING` (1–64). All processes must use identical settings.
Configuration is cached per process; restart/drain the application and workers
when changing it. Old processes do not enforce this batch's new controls.

**Production Redis is required.** Set `RESOURCE_REDIS_URL` explicitly to a shared
`redis://` or `rediss://` service/database. If absent, the existing
`CELERY_BROKER_URL` is used only if it is a Redis URL. Development otherwise uses
`redis://127.0.0.1:6379/2`. There is no in-memory or disabled fallback, including in
development. The existing repository does not provision production Redis.

Use the same writable Redis primary/database for every API instance and worker;
do not point individual processes at separate caches or replicas. The limiter
uses single-key Lua `EVAL` with `TIME`, sorted-set operations and expiration.
Allow these commands for the `resource:v1:rate:*` namespace. One atomic script
prunes expired starts, checks the limit, and records an accepted start with a
unique token. Keys expire with their windows; no process clock or local counter
decides admission. The client has one-second connect/read timeouts, zero automatic
retries, and at most 32 connections per process. DNS/service resolution is still
deployment dependent.

Protect Redis access and capacity; use a suitable no-eviction policy/dedicated
capacity for admission data. Eviction, administrative key deletion, loss on
restart or asynchronous failover can reset rate history. A separate logical DB
does not isolate Redis memory/eviction policy from Celery. Choose persistence and
failover settings for the acceptable window of lost history. Redis outage does
not release PostgreSQL concurrency permits.

**PostgreSQL requires direct connections or session pooling. Transaction pooling
is incompatible**, as already required by Batches 6 and 7. Per-user slot locks use
distinct namespaces from those existing per-document/per-summary claims. They
complement rather than replace those claims. A permit retains a physical session
without a long transaction; it has no TTL that can expire during provider work.
Unlock happens before returning the connection to the pool. Uncertain acquisition
or release invalidates the connection. Lost permits cannot reconnect as owners.

Account for retained connections in the existing pool/server connection budget:
chat/search/summary request permits each need a connection, in addition to route
sessions and Batch 7 context ownership. Processing reuses the Batch 6 connection;
chat-triggered summaries reuse their Batch 7 connection for the summary permit.
Upload reservation retains its own connection through file saving and row commit.
Pool exhaustion fails admission after the existing database pool timeout. Limits
are per user, so aggregate capacity still needs deployment-level controls.

## Upload and stored-document admission

Defaults: `RESOURCE_MAX_DOCUMENTS=100` and
`RESOURCE_MAX_ORIGINAL_BYTES=1073741824` (1 GiB) per user. Existing per-file size
and content validation remain in place. The count includes every retained
document row, including failed documents. Original byte usage is computed from
the files referenced by those rows, plus the incoming file. No size column or
historical backfill is introduced. Null original paths contribute zero bytes
but still count as documents.

A per-user PostgreSQL lock serializes quota checking, original-file saving and
document commit. Another upload/retry cannot concurrently reserve the same
capacity. Existing `processing` rows reserve the processing allowance before
dispatch. Retry excludes its own row but checks other active rows, and the worker
also checks actual per-user processing capacity before expensive work. Multiple
documents/tabs/processes cannot bypass either gate. Ready/permanent-failure guards
and Batch 6's individual document claim remain in place.

All API/worker instances must see the same immutable originals at the same stored
paths under `uploads`. Missing, unreadable or out-of-root tracked originals cause
safe 503 rejection rather than under-counting. This is a quota for tracked original
files, not Cloudinary assets, generated files, chunks/vectors, logs, temporary
multipart files or orphan files. Existing deletion semantics are unchanged;
deleting a row removes it from the tracked quota, even if some derived/orphan bytes
remain. Stale `processing` rows can conservatively block uploads until the existing
retry/recovery workflow resolves them. No automatic cleanup is introduced.

## Streaming and errors

FastAPI request-scoped dependencies acquire permits before request setup and hold
them through the complete response. `AdmittedStreamingResponse` closes its
underlying generator on normal completion, failure, cancellation and detectable
disconnect. An in-flight synchronous iterator call retains a reference until it
returns and cleans up. Background title generation separately retains the chat
permit until its future finishes, even if the response has already ended.
Provider cancellation is not guaranteed: capacity is retained while that local
work runs, but an external provider may still complete or bill an abandoned call.
Process/database-session loss releases server locks; externally running work
cannot be fenced by an application permit.

Before response headers, limits return HTTP 429 with a safe string `detail`,
machine-readable `code`, integer `retry_after`, and `Retry-After` seconds. No Redis
keys, counters or infrastructure details are exposed. Codes include `rate_limit`,
`concurrency_limit`, `processing_quota`, `document_quota`, and `storage_quota`.
The rate wait uses the oldest still-active start; concurrency/processing waits
suggest 5 seconds, and storage/count limits suggest 60 seconds but also require
deletion to free quota. Retry-After is guidance, not a capacity reservation.
The resource headers are exposed through existing CORS configuration.

Backend failures return safe HTTP 503 / `admission_unavailable` / Retry-After 5.
New expensive work, ordinary reads/writes and authentication fail closed. Only
authenticated DELETE and summary cancellation continue if Redis is unavailable,
so users can recover capacity. They still require authentication and ownership,
and are rate-limited when Redis works. PostgreSQL failure never enables new work.
Requests already admitted may finish during a Redis outage.

If a nested operation is denied after stream headers have been sent, the stream
emits the existing NDJSON `error` event with `message`, `code`, and `retry_after`;
HTTP status cannot then change. Earlier context conflicts retain Batch 7 handling
unless the per-user gate rejects first. The frontend already displays string
`detail` and stream `message` errors, so no frontend changes or automatic retry
behavior are introduced.

## Trusted peers and ingress

Auth limits use only `request.client.host`, normalized as an IP. Application code
never reads `X-Forwarded-For` or `Forwarded`; unknown peers share one conservative
bucket. The Docker command disables Uvicorn proxy headers by default. Apply the
same setting to non-Docker launch commands. For a deployment behind a trusted
proxy, explicitly enable proxy handling with an allowlist of actual proxy peer
addresses (never `*`), prevent direct origin access, and make the proxy overwrite
untrusted forwarded headers. Otherwise users behind that proxy share its auth
bucket. OAuth token verification/state/cookies/redirect flow is unchanged.

Use the edge/CDN/WAF or reverse proxy for request-body and multipart spooling size,
upload timeouts, unauthenticated traffic, source-IP abuse, aggregate request and
connection limits, and origin protection. FastAPI can parse/spool multipart data
before authenticated dependencies run; these controls do not stop all ingress
bandwidth or temporary-disk abuse. Per-user limits cannot stop account farming or
large numbers of users exhausting a global provider budget. Provider-side spend
caps remain appropriate; durable token/dollar accounting is outside this batch.

## Safe validation

From `backend`, database/provider-free commands:

```powershell
.\venv\Scripts\python.exe -B tests/test_resource_admission.py
.\venv\Scripts\python.exe -B tests/test_resource_admission_redis.py
.\venv\Scripts\python.exe -B tests/test_database_safety.py
.\venv\Scripts\python.exe -B tests/test_public_generation_errors.py
.\venv\Scripts\python.exe -B tests/test_embedding_recovery.py
.\venv\Scripts\python.exe -B tests/test_document_processing_reliability.py
.\venv\Scripts\python.exe -B tests/test_summary_concurrency.py
```

The Redis file runs offline safety guards and skips live checks unless an explicit
`TEST_REDIS_URL` is set. Live Redis checks require a dedicated loopback test DB
numbered 3 or higher (for example, 15), different from configured application
Redis databases. URL options/non-loopback targets are rejected; only UUID test
keys are deleted, never FLUSHDB. It executes the actual Lua source without
importing application database/provider modules.

`tests/test_resource_admission_postgres.py` prepares live physical-session,
per-user isolation, release, borrowed-claim and committed quota checks. It uses
Batch 1's existing `TEST_DATABASE_URL` fixture/guards, which require an explicit
local test target and create a separate UUID database with the existing schema.
Ordinary pytest integration fixtures stub application Redis so they cannot use
application credentials. No TEST_DATABASE_URL guard is bypassed. Run the live
PostgreSQL suite only in a separately authorized test environment; the fixture
applies existing migrations to its disposable database.

Live Redis and PostgreSQL verification has not been performed for this batch:
neither safe test URL was configured. Mocked/ASGI coverage verifies the application
contracts, not live infrastructure behavior. Verify the guarded integration tests,
shared Redis configuration, session pooling and shared original paths before
production rollout. No production services, paid provider calls, migrations or
load tests were run while preparing this batch.
