# Batch 14: observability, CI and runtime operations

This batch adds a small Python logging boundary, request instrumentation, dependency
readiness and CI. It does not install an observability platform or change RAG,
stream protocols, admission limits, database pools or migrations.

## Safe logs and correlation

API and Celery logs use one JSON object per line on stdout. Use the provided
`python runtime.py` container entry point and the normal
`celery -A app.worker.celery_app worker` entry point. API imports configure logging;
Celery's `setup_logging` signal installs the same formatter instead of Celery's raw
task/exception formatter. Do not replace the handler with a raw formatter, enable
SQL echo/provider debug dumps, or install SDK auto-capture in production.

Fields are allowlisted: `event`, `operation`, `request_id`, `correlation_id`,
`job_id`, numeric `document_id`, `summary_id`, `chat_id`, `message_id`,
`exception_type`, `failure_category`, `dependency`, `outcome`, numeric counts,
HTTP method/status, elapsed milliseconds and response byte count. The formatter
adds timestamp, level, logger, source function and line number. Ordinary library
records have an opaque `library_log` event and their code location/type only.
Their message, arguments, URLs and traceback text are never formatted.

JWTs, cookies, Authorization headers, secrets, filenames/paths, document contents,
prompts, SQL parameters and provider payloads are excluded. This is an allowlist,
not regex replacement of possible secrets. Callers must pass code-owned event/
operation names and typed IDs/counts, never user/provider strings in those fields.
Raw `print` and `logger.exception` calls in application code are rejected by the
static CI check. Malformed records produce `logging_record_rejected`; logging
output failures cannot trigger Python's raw message/argument dump to stderr.

Batch 4 still chooses the same public error messages and timeout categories.
Its bounded cause-chain diagnostics retain exception types and module/function/
line locations, without messages, source text, locals or exception attributes.

Every HTTP request gets a new server UUID in `X-Request-ID`, including CORS
rejections, 404s and generated 500s. CORS exposes this response header alongside
the existing pagination and resource headers. An optional `X-Correlation-ID`
is accepted only as a UUID and normalized; otherwise correlation defaults to the
new request ID. Correlation IDs are untrusted hints, never authorization or
uniqueness guarantees. No client header is echoed as the server request ID.

Context is isolated across simultaneous requests and restored on exceptions and
cancellation. Background document processing inherits request correlation; Celery
publish headers carry only the two UUID fields, and worker logs use the Celery task
UUID as `job_id`. Development background jobs allocate an invocation UUID. Existing
thread pools explicitly carry only this safe logging context. Document processing
logs starts, checkpoints, completion/failure, retries and skipped work with IDs.

Summary claim acquisition/release and failure logs carry document/chat/summary IDs
and a stable UUID derived from the summary record ID. A release event indicates
that the claim scope exited; it does not assert successful generation. Inspect the
persisted summary status when investigating cancellation. No ContextVar token spans
a synchronous streaming-generator yield, which may resume in a different thread.

Example (synthetic IDs):

```json
{"event":"document_retry_scheduled","operation":"document_processing","request_id":"12cbbad1-dafe-4f0b-b3bb-df1ce0e72c22","job_id":"1bbc6f9f-1bce-4c16-bcc4-7b874fd6cc11","document_id":7,"retry":1,"delay_seconds":2}
```

Restrict log access and retention even though payloads are excluded: internal IDs
still reveal activity. Configure the platform log collector to retain JSON and
rotate stdout storage. The application cannot sanitize reverse-proxy/access logs,
container host diagnostics or independently configured external SDK handlers.

## Lightweight instrumentation and monitoring hooks

The outer pure-ASGI wrapper records `http_request_completed` with the code-owned
route **name** (or `unmatched`), method, status, duration, byte count and transport
outcome. It forwards chunks immediately, does not read request bodies, and does
not parse NDJSON. HTTP completion does not prove a generation `done` event or a
successful summary; correlate generation errors and persisted job status.
Duration includes streaming and request-attached background work. Disconnection
or cancellation keeps existing cleanup/exception propagation intact. A hard kill
cannot emit a final event.

`install_observers(error_reporter=..., request_observer=...)` is an optional trusted
boot-time integration hook. Both callbacks receive safe copies, never Request or
Exception objects. Error notifications contain operation/IDs/type/category, not
tracebacks or payloads. Request observations omit request/job/document IDs, raw
paths and queries so they can feed counters and latency histograms with bounded
labels. Use only operation/method/status/outcome as labels; duration/bytes are
measurements. Callbacks must enqueue into a bounded nonblocking adapter; exceptions
are suppressed and logged safely. No exporter, SDK, DSN, outbound monitoring call
or public `/metrics` endpoint is enabled by default. Do not turn on automatic
request capture, breadcrumbs, session replay, local-variable capture or PII capture.

## Liveness and readiness

| Endpoint | Meaning | Checks | HTTP result |
| --- | --- | --- | --- |
| `/health` | Process responds | None | 200, unchanged `{"status":"ok"}` |
| `/ready` | Critical connections available | PostgreSQL `SELECT 1`, resource Redis `PING`; Celery broker/result Redis when enabled | 200 ready, 503 not ready |

Readiness reports only dependency names and `ok`/`unavailable`. It uses `Cache-Control:
no-store`, never returns exception text or connection URLs, and never calls AI,
Datalab, Cloudinary, Google, embeddings or other paid providers. Identical Redis
URLs are probed once per cycle. Successes and failures are cached for five seconds
per API process. Only one probe cycle runs per process; overlapping callers without
a fresh result receive 503 `probe: in_progress` instead of queuing connections.

PostgreSQL probes use one transient read-only connection, a two-second connection
timeout and two-second statement timeout. They bypass the application checkout
queue and close on success/failure. Redis probes have two-second connection/read
timeouts, no retries, and close/disconnect their pools. URL query settings cannot
override the Redis probe timeouts/retry policy. These are I/O budgets, not a global
wall-clock deadline; DNS/multiple addresses can add time. Checks are sequential.
Allow approximately 20 seconds for a cold readiness probe, then measure the real
network behavior in staging. Probe at intervals of at least 10 seconds with a
failure threshold; avoid synchronized duplicate probe callers per process.

Readiness does not verify migration head, pool saturation, write privileges,
worker availability/backlog or provider account health. Keep these as separate
deployment/operational checks. Do not make dependency failures restart the process:
use `/health` for restart policy and `/ready` for traffic admission.

## Container and deployment requirements

The backend image runs as UID/GID **10001**, keeps application code root-owned,
and makes only `/app/uploads` writable. Explicit COPY inputs include app code,
entry points and migrations/config, excluding tests, env files and local uploads;
the ignore file also excludes nested env files, bytecode and private key formats.
Never put credentials in source files or Docker build arguments. Inject secrets
at runtime using the deployment secret store.

The entry point validates the port and settings and uses `exec` to run Uvicorn as
PID 1. Forwarded headers and Uvicorn raw access logging are disabled. If deploying
behind a proxy, preserve existing HTTPS/cookie/origin settings and apply body/rate
limits at the edge. Only override proxy flags after restricting trusted proxy IPs;
never trust `*` on a publicly reachable listener. Ambient `FORWARDED_ALLOW_IPS`
does not enable proxy trust in this entry point.

Production **and staging** require valid explicit PostgreSQL and Redis broker/
result configuration, `TASK_QUEUE=celery`, and a JWT secret of at least 32 characters.
Existing auth-origin/cookie, resource-limit, upload-limit and pool validation also
runs at API lifespan/worker startup. Worker configuration failure exits even
though Celery normally catches exceptions from signal receivers. Do not rotate an
existing JWT secret solely for this rollout if it already meets policy.

API and workers must share the correct upload volume; provision ownership for
10001:10001 before changing the runtime UID. Verify assets/subdirectories remain
writable. `/tmp` must be writable for bounded spooling/parser temporary files.
Consider a read-only root filesystem with explicit uploads and `/tmp` mounts,
capability dropping and `no-new-privileges` in the deployment. These flags are not
forced here because mounts and platform requirements must be verified first.
The image liveness check targets loopback `/health` with environment proxies
disabled; a worker container using this same
image must disable/override that API healthcheck and use its own worker supervision.

Pool settings and their limits are unchanged. Budget **at most one additional
transient PostgreSQL readiness connection per API process** alongside application
pool ceilings. Do not assume the theoretical six-process × 15 = 90 application
connection ceiling is a recommended database budget. Choose API/worker process
counts × per-process capacity, plus readiness, migration/admin connections and
headroom, within the real PostgreSQL limit. Batch 6/7/8 claims and permits can retain
checked-out connections during operations. See [database scalability](database-scalability.md).

## CI and security checks

`.github/workflows/ci.yml` runs on pull requests, pushes to `main` and
`production-hardening`, manual dispatch, and a weekly schedule. All jobs use
`contents: read`, SHA-pinned maintained actions, checkout without persisted
credentials, timeouts and cancellation of superseded runs. There is no deployment
step, repository-secret reference, `pull_request_target`, production service or
migration execution.

* **Backend:** Python 3.11; install pinned runtime/dev requirements; parse/compile
  source with 3.11 grammar and reject unsafe logging calls; require one complete
  migration chain and render upgrade/downgrade SQL entirely in memory; run explicit
  standalone regression suites through `scripts/ci_tests.py`.
* **Frontend:** Node 22; `npm ci`; all `tests/*.test.mjs`; TypeScript no-emit;
  ESLint (zero errors, maximum 19 existing warnings); production build.
* **Dependency security:** `pip-audit==2.10.1 --strict -r requirements-dev.txt`
  includes resolved transitive/test packages and fails on any known advisory or
  collection error. `npm audit --package-lock-only --audit-level=high` includes
  production/dev dependencies and fails on high/critical findings. Both use public
  registry/advisory services only; there are no automatic fixes or suppressions.
* **Workflow/container:** actionlint 1.7.12 validates workflow expressions/shell;
  Docker builds the image and checks UID 10001 with networking disabled and the
  entry point replaced by `id`. It never starts the application or probes services.

Dependabot configuration covers pip, npm, Actions and Docker weekly. Enable Actions,
dependency graph/security alerts, and require these four CI jobs in branch
protection after the workflow is published. Scheduled workflows/Dependabot are
activated from the repository default branch; merely leaving this branch locally
does not enable them. No repository settings have been changed in this batch.

`ci_tests.py` strips inherited app/provider/PG credentials, blanks both database
URLs and disables dotenv. It never uses generic pytest discovery. The selected
suites use existing mocked-service/SQLite harnesses in separate processes. The
older private-image fixture now includes the same admission mocks and bounded
pagination query methods required by Batches 8/11. Guarded live PostgreSQL/Redis
tests remain separate: `TEST_DATABASE_URL` must pass the existing loopback/name
checks, and **DATABASE_URL is never a fallback**. Migration rendering does not load
`migrations/env.py`, import application engines or execute SQL; connections are
explicitly forbidden in the checker. No historical migration was edited.

Local equivalents from the repository root:

```text
backend/venv/Scripts/python.exe -B backend/scripts/ci_checks.py
backend/venv/Scripts/python.exe -B backend/scripts/ci_tests.py
```

From `frontend`: `node --test tests/*.test.mjs`, `npx --no-install tsc --noEmit`,
`npm run lint -- --max-warnings 19`, `npm run build`, and
`npm audit --package-lock-only --audit-level=high`.

## Validation and rollout follow-up

Local validation uses Python 3.14 with a Python 3.11 grammar check; the actual
Python 3.11 and Linux image checks are in GitHub Actions. Docker is not installed
in this workspace, so no local image build/runtime claim is made. Verify volume
ownership, startup/shutdown signals, readiness failure/recovery and proxy behavior
on the staging platform before deployment. No live database, Redis, provider,
load-test or migration operation is part of this validation.

Validation results: 348 backend tests across 12 isolated suites (including 24 new
observability tests), 42 frontend tests, TypeScript no-emit and production build
passed. ESLint has 0 errors / 19 existing warnings. Python syntax/static safety
checked 114 files, the four-revision migration graph and both SQL directions
rendered offline, actionlint passed, and Dependabot YAML/config validated.

The 2026-09-14 backend audit resolved 68 packages and found no known vulnerabilities.
The frontend lockfile audit found two existing high advisories:
[js-yaml GHSA-2883-xcg3-v3hh](https://github.com/advisories/GHSA-2883-xcg3-v3hh)
(4.3.1, fixed in 4.3.2) and
[sharp GHSA-rgj7-g3m4-5g8c](https://github.com/advisories/GHSA-rgj7-g3m4-5g8c)
(fixed in 0.35.4). They are not suppressed. The security job will remain red until
the lockfile is patched and the frontend checks rerun; this batch adds detection
and gating without changing frontend dependencies. A clean package scan does not
cover OS packages, undisclosed vulnerabilities or deployed artifact drift. Scan
the actual deployment image and pin/promote its digest through the deployment
process before release.

Choose the log collector/retention policy and optional safe monitoring adapter;
define alerts for dependency unavailability, failed/interrupted jobs, error rates
and sustained latency using staging measurements. Worker/backlog monitoring and
provider health require separate operational signals, not paid readiness calls.
Existing staging query-plan/load measurements remain in the earlier runbooks.
