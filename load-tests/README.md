# Batch 15: isolated load-test tooling

This directory adds tooling only. No load test, application request, PostgreSQL,
Redis, worker or provider call was executed while implementing this batch. There
are no measured latency, throughput, saturation or scalability conclusions yet.
Application code, limits, migrations and earlier batches are unchanged.

## Offline validation (safe now)

Node's built-in test runner needs no npm install or third-party packages. Run from
the repository root:

```text
node --test load-tests/tests/*.test.mjs
node load-tests/run.mjs validate --example
node load-tests/run.mjs inspect --example
node load-tests/run.mjs inspect --example --scenario=chat
```

`validate` checks configuration without launching k6. `inspect` compiles the local
k6 modules and resolves execution requirements; it does not invoke setup, VUs or
HTTP. Both example commands use a reserved synthetic hostname/IP and fake token.
`run --example` is forbidden. k6 2.2.0 and Node 24.12.0 were used for local checks;
inspect with your installed k6 version before any separately authorized execution.
The launcher explicitly selects `--new-machine-readable-summary=false` for runs
to keep the aggregate schema stable across versions; unsupported summary shapes
fail visibly. See the [custom summary contract](https://grafana.com/docs/k6/latest/results-output/end-of-test/custom-summary/).
Use the official [k6 options](https://grafana.com/docs/k6/latest/using-k6/k6-options/reference/),
[ramping VUs](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/ramping-vus/)
and [execution API](https://grafana.com/docs/k6/latest/javascript-api/k6-execution/)
references when evaluating a version change. No remote JavaScript imports are used.

## Scenarios

Each run selects exactly one scenario. Every execution first reads `/health` and
`/ready` and requires their `ok`/`ready` contracts. Authenticated VUs verify their
fixture ID through `/auth/me` before sending workload requests.

| `LOAD_SCENARIO` | Traffic | Provider/write boundary |
| --- | --- | --- |
| `health` | Alternates GET `/health` and `/ready` | No auth or providers; dependency probes are cached server-side |
| `reads` | GET identity, document/chat/message pages, fixture document/chat details, upload policy | Read-only; no search, auth exchange, upload or generation |
| `search-admission` | POST empty query to fixture chat search | Expected 400 or known 429; rejected before embedding |
| `upload-admission` | POST one byte named `load-probe.invalid` | Expected `unsupported_file` 400 or known 429; no supported file, save or job dispatch |
| `chat-admission` | POST empty question to fixture chat ask | Expected 400 or known 429; rejected before generation |
| `search` | POST a fixed synthetic question to fixture chat search | Embedding/provider traffic: mock or separately enabled real mode |
| `chat` | POST a fixed synthetic question with a fixture document to `/ask` | Provider traffic and bounded synthetic message creation; extra write acknowledgement |

The admission probes match current backend validation order. They consume normal
Batch 8 admission budgets and exercise short permit acquisition, not sustained
provider occupancy or worker throughput. Recheck this ordering after backend
changes. Unexpected acceptance of an invalid probe aborts the run. No supported
upload, retry, delete, logout, credential exchange, summary generation or fixture
creation endpoint is included. Chat uses the bounded non-streaming ask endpoint;
streaming/NDJSON correctness remains covered by previous batch regression tests.

Document, chat and message pages request `limit=50` by default (maximum 100) and
follow the signed `X-Next-Cursor` header on the same resource. No response URL is
followed. Message traversal uses Batch 11's older-message cursor; the array within
each page remains in display order. Duplicate IDs, malformed/repeated cursors,
oversized pages and foreign chat IDs abort. A configurable page cap bounds local
memory and traversal; `pagination_capped` explicitly differs from completion.
Use frozen synthetic fixtures with more than 50 documents, nonempty chats and
messages, including tied timestamps, and compare completed traversal against
known fixture counts. These checks do not prove completeness on a changing dataset.

## Target, fixtures and isolation

No default live target exists and no `.env` file is automatically loaded. Review
[.env.example](.env.example) and supply values through the environment:

| Setting | Required meaning |
| --- | --- |
| `LOAD_BASE_URL`, `LOAD_CONFIRMED_ORIGIN` | Identical explicitly reviewed API origin, with no path, trailing slash, query, fragment or credentials |
| `LOAD_ALLOWED_HOST`, `LOAD_TARGET_IP` | Exact hostname and manually verified canonical IPv4; k6 pins that host to this IP |
| `LOAD_ENVIRONMENT` | `staging` or `isolated-local` |
| `LOAD_TARGET_ACK` | `dedicated-non-production-stack` |
| `LOAD_SCENARIO` | One scenario from the table |
| `LOAD_CREDENTIALS_ACK` | `dedicated-test-accounts-no-production-tokens` for authenticated scenarios |
| `LOAD_USERS_FILE` or `LOAD_USERS_JSON` | Exactly one explicit fixture source; never application environment credentials |

Staging requires HTTPS and an explicit `staging`, `stage`, `loadtest` or `test`
hostname label. Production labels, URL credentials, redirects, insecure TLS,
metadata addresses, loopback staging IPs and ambiguous IP syntax are refused.
Local testing requires exactly `127.0.0.1`, an explicit non-default port such as
18000, a dedicated disposable stack, and
`LOAD_LOCAL_ACK=dedicated-stack-no-production-tunnel`. Default ports 3000, 8000,
5432 and 6379 are refused. IPv6 and custom base paths are intentionally unsupported.

These are accident-prevention guards, not proof of environment ownership. A test
hostname can still route to production; a local port can be a production tunnel;
a valid token can still be a production token. Before execution independently
verify the target IP/TLS routing, backend settings, separate DB/Redis, workers,
object storage, provider egress and credentials. Never use production data or
credentials. Do not tunnel, proxy or relabel production to satisfy the guards.
Do not fall back to application `DATABASE_URL`, `REDIS_URL` or existing auth tokens.

Create test fixtures separately in the isolated stack; the tooling never seeds or
cleans them. Each fixture must own a ready synthetic document and an attached chat
with messages. Provide an unexpired dedicated test bearer token and IDs:

```json
[{"token":"DEDICATED_TEST_TOKEN_ONLY","user_id":1,"chat_id":1,"document_id":1}]
```

Prefer `LOAD_USERS_FILE=private/users.json`, resolved relative to this directory.
The launcher accepts only a real `.json` file inside `load-tests/private/`, at most
1 MiB. Private files, results and local env files are ignored by Git; restrict their
filesystem permissions too. Never commit tokens or paste them into shell history.
No OAuth login or token refresh is performed. Expired/rejected identities stop the
run. `LOAD_USER_MODE=per-vu` (default) needs one distinct account per peak VU;
`shared` requires exactly one fixed account and measures contention/rate admission.
Identities never rotate in response to rejection. Do not add accounts to evade a
limit; choose representative synthetic user populations before a run.

## Provider and execution gates

Ordinary traffic works with `LOAD_PROVIDER_MODE=blocked` (default). Admission,
search and chat additionally require
`LOAD_PROVIDER_GUARD_ACK=isolated-egress-and-fixtures-reviewed`.
Blocked/mock modes are operator attestations, not a server-side provider switch:
configure denied provider egress or compatible local stubs on the API **and worker**
deployment, remove real keys, and verify all provider paths before proceeding.
The tools install no server mocks. For meaningful concurrency measurements, mocks
should model bounded provider delays and errors without changing admission limits.

Valid search/chat requires `LOAD_PROVIDER_MODE=mock` or `real`. Real mode also
requires `LOAD_ENABLE_PAID_PROVIDERS=I-accept-provider-spending`; it is never selected
automatically. Chat additionally requires
`LOAD_WRITE_ACK=synthetic-chat-messages-only`, including with mocks. One API chat
request can make several provider requests (intent, title, embeddings, answer);
the workload request cap is not a billing cap. Paid testing requires a separately
reviewed provider budget, billing controls and authorization. Client cancellation
does not guarantee that already admitted server work or billing stops.

After independently authorizing a target and reviewing its environment, the manual
sequence from the repository root is:

```text
node load-tests/run.mjs validate
node load-tests/run.mjs inspect
```

Inspect output contains the reviewed host/IP; keep it private. Only then explicitly
set `LOAD_EXECUTION_ACK=run-against-isolated-stack` in the same shell and invoke
`node load-tests/run.mjs run`. Unset that acknowledgement afterward. This command
is documentation for future staging use; it was not executed during Batch 15.
Use the guarded launcher, never raw `k6 run`, cloud execution or parallel/distributed
generators: request ceilings are per process, not a distributed quota.

The launcher strips inherited K6 overrides, proxies, exporters, cloud tokens,
database URLs and provider keys, uses a known empty k6 config, disables usage
reporting for runs and suppresses k6 logs. Arbitrary CLI options are refused. Only
aggregate allowlisted metrics are saved; response bodies, URLs, cookies, bearer
tokens and prompts are not reported. Protect the load-generator host and staging
access logs independently. Environment credentials remain visible to privileged
local process inspection.

## Profiles, limits and interpretation

| Setting | Default and bounds |
| --- | --- |
| `LOAD_PROFILE` | `smoke`: 1 VU for 10 seconds, then 10-second ramp down; `staged`: profiles below |
| Ordinary staged VUs | 10 → 50 → 100 → 250 → 500 |
| Admission/provider staged VUs | 2 → 5 → 10 → 20 |
| `LOAD_RAMP_SECONDS`, `LOAD_HOLD_SECONDS` | 30 each per stage; each configurable 10–120; final 10-second ramp down |
| `LOAD_THINK_SECONDS` | Ordinary/admission 1, provider 10; range 1–60 seconds |
| `LOAD_MAX_REQUESTS` | Ordinary 10,000 (cap 200,000); admission 100 (cap 1,000); provider 20 (cap 200) |
| `LOAD_PAGE_SIZE`, `LOAD_MAX_PAGES` | 50 rows (1–100); 10 pages per chain (1–100) |
| Timeouts/grace | Ordinary/admission requests 20 seconds and 30-second grace; provider requests/grace 120 seconds |

One workload iteration makes at most one HTTP request, including initial identity
verification; two preflight GETs are additional. A global iteration number gates
requests across VUs. Reaching the request cap deliberately aborts with nonzero exit;
report a capped run, not a completed staged profile. Conservative default request
budgets can stop **before the peak VU stage**, especially provider runs. Review and
explicitly adjust budgets within their caps only when the planned fixture/provider
cost permits. Remaining in-flight requests can finish or be cancelled at shutdown.

500 VUs is not 500 requests/second. This closed workload waits for responses, think
time and the full numeric `Retry-After` after recognized admission rejection. It
never clamps retry delays or immediately retries. Rate limits stay enabled: the
current defaults include ordinary reads 120/min/user, search 20/min/user, chat
10/min/user and uploads 5/hour/user. Shared-account tests will hit these limits;
do not interpret a successful backpressure test as achieved business throughput.
See [Batch 8 resource admission](../backend/docs/resource-admission.md).

`requests`/`http_reqs` report counts and requests/sec. `request_latency` and HTTP
trends record avg, p90, p95, p99 and max. `raw_failures` includes every non-2xx
response, including deliberate 400 probes. `unexpected_failures` excludes only
the exact expected probe 400 and known admission 429 on admission/provider runs.
Ordinary 429 and all 503 dependency/admission failures remain failures. Separate
admission rejection/unavailability rates and retry-delay trends expose backpressure.
Setup and identity verification are included in aggregate request metrics.

Thresholds require zero contract failures and unexpected failures below 1%;
ordinary runs also require HTTP failure rate below 1%. There is no AI latency gate.
Record p95 first; an optional ordinary-only `LOAD_P95_MS` enables a reviewed baseline
gate later. Thresholds are evaluated at completion; operators must watch the
staging service during a run. A passing admission run may consist entirely of safe
rejections, so always report accepted work, raw failures and rejection rate too.

## Dependency pressure and staging measurements

Results are written as aggregate JSON to stdout and `results/<run_id>.json`. The
safe run ID is printed before execution. There is no public DB/Redis/worker metrics
endpoint and the load generator holds no service credentials. Collect trusted,
read-only aggregate samples separately on staging and attach them to the same run
window. Do not collect SQL text, Redis keys/values, Celery task args or document data.

Use existing staging monitoring to sample PostgreSQL total/active/waiting sessions,
max connections and per-process pool checkouts/capacities; Redis connected clients,
rejected connections and memory; worker active/reserved jobs, concurrency and queue
depth. Record process counts, CPU/memory/I/O, collection scope and sampling interval
alongside results. Missing instrumentation must remain marked missing. Ordinary
reads and invalid uploads do not generate worker jobs: worker saturation needs a
separately provisioned, bounded synthetic job workload with providers mocked.

Offline aggregation accepts a JSON array with the run ID and elapsed seconds:

```json
[{"run_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","elapsed_seconds":1,"db_connections":8,"worker_active":2}]
```

These are illustrative numbers, not measurements. Allowed numeric fields are
`db_connections`, `db_active`, `db_waiting`, `db_max_connections`,
`db_pool_checked_out`, `db_pool_capacity`, `redis_connected_clients`,
`redis_rejected_connections`, `redis_used_memory_bytes`, `worker_active`,
`worker_reserved`, `worker_concurrency`, `queue_depth`. Supply only nonnegative
integers from the same observed scope. Cumulative counters such as Redis rejected
connections need a starting sample to interpret deltas. Unknown fields and samples
outside the run duration are rejected. Run locally after obtaining actual samples:

```text
node load-tests/observations.mjs load-tests/results/RUN_ID.json load-tests/private/observations.json
```

Output reports min/max and sample counts only for provided metrics, plus a missing
fields list. It never connects to dependencies or estimates missing values.

Budget connections using API process count × its pool capacity + worker process
count × its pool capacity + readiness/admin/migration/other-client headroom, within
the actual PostgreSQL limit (including rolling-deployment overlap). The default
illustration `(4 API + 2 workers) × (5 + 10) = 90` is a theoretical application
ceiling, **not a safe recommended budget**. Batch 6/7/8 claims and permits can retain
checked-out connections. Readiness uses up to one extra transient PostgreSQL
connection per API process; a ready response does not prove pool or worker health.
No pool setting or admission limit is raised by this tooling. See the
[database staging plan runbook](../backend/docs/database-scalability.md) and
[observability runbook](../backend/docs/observability-ci.md).

Start with a smoke run only after staging approval, then evaluate one profile at a
time. Stop on unexpected target/fixture identity, auth/redirect/contract failure,
sustained service failures, pool starvation, growing queues, provider leakage or
load-generator saturation. Ctrl+C stops further client work; separately check
admitted jobs and retained permits before authorizing another run. Do not retry a
failed profile automatically or disable limits to make a threshold pass.

For each staging report record revision/index deployment state, fixture cardinality,
target identity privately, provider/mock mode, process/pool/worker configuration,
run ID, stages actually reached, request cap/exit status, duration, requests/sec,
raw/unexpected/rejection rates, avg/p90/p95/p99, pagination completion/caps, dependency
samples, generator resource use and observed bottlenecks. Distinguish cached
readiness behavior from dependency throughput. Reconcile query-plan evidence with
latency before proposing tuning; do not change HNSW or indexes based on guesses.
All live measurements and any resulting capacity recommendation remain pending.

Offline validation completed: 27 Node regression tests passed; k6 inspection passed
for all seven scenarios in both smoke and staged profiles (14 combinations); all
seven JavaScript modules passed syntax checks. JSON and whitespace checks passed.
The k6 run flag was checked through `--help` only. No scenario was executed.
