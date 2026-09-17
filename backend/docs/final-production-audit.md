# Final production-hardening re-audit

Validation performed: 2026-09-15. Report finalized: 2026-09-17.
Branch: `production-hardening`.
Audited application commit: `b0c8744523f489493f5fef23062bc28c5a599036`
(`b0c8744 fix final production audit blockers`).

The application working tree was clean at entry; this existing, untracked audit
report was the only outstanding file. This re-audit updates only this report.
No application/configuration/dependency changes, commit, push or merge were made.
This report supersedes the 2026-09-14 assessment of `7de609d`.

## Verdict and evidence boundary

**PASS for the three previous code blockers and the available local validation
gates. Substantially hardened and suitable for isolated staging acceptance;
not yet approved for unrestricted public production.**

No remaining confirmed FAIL item was found in this review. Residual repository
warnings and deployment acceptance work remain. In particular, existing public
image origins, browser/proxy behavior, database deployment and measured capacity
cannot be certified by offline tests. A source merge and a production release are
separate decisions.

- **PASS:** implementation/source review and the executed checks support the
  stated repository scope.
- **WARNING:** residual limitation, configuration dependency or coverage gap.
- **FAIL:** confirmed unmet code requirement or failed required validation.
- **NOT LIVE-VERIFIED:** deployed-system evidence was not obtained; this is neither
  a measured pass nor a measured failure.

The original major-finding comparison is reconstructed from the prior audit,
Batches 1-15, Git changes and repository runbooks. A separate complete original
Phase 1 audit artifact was not available in the checked repository. This is a
source review and offline validation, not a penetration test or proof that every
possible defect has been found. Public dependency registries were used only for
the requested security audits; application services and real providers were not.

## Previous three blockers: all PASS

### F1: public exception disclosure - PASS

The non-streaming `ask_chat` handler in `app/routes/chats.py` and
`create_summary_assistant_message` in `app/routes/summary_assistant.py` now send
provider-facing `ValueError` instances through Batch 4's
`log_generation_failure`. Both preserve HTTP 400 and return fixed public messages,
including the existing timeout classification. They do not return arbitrary
exception text. Explicit safe input validation remains intact.

Diagnostics retain exception types, bounded code locations, operation and numeric
chat/document/message IDs, plus safe request context. They omit exception text,
locals, prompts, document contents and provider payloads. The structured logging
boundary re-allowlists these fields.

Evidence: 22 public-generation-error tests pass, including actual HTTP requests
against both routes with synthetic private markers, an exception whose `__str__`
raises, and a wrapped timeout. Responses and diagnostic records do not contain the
markers. Source search found no remaining `detail=str(error)` pattern in the
application. This closes the two confirmed boundaries, not a claim that every
conceivable information-disclosure path is impossible.

### F2: provider stream cleanup - PASS

`app/services/llm_service.py::generate_answer_stream` closes the SDK stream in
`finally`. The outer chat response generator also closes its answer generator in
`finally`. Cleanup exceptions are safely logged without replacing the original
failure. Repeated application-generator closure does not repeat SDK cleanup.

The 42 resource-admission tests cover normal completion, explicit close,
injected generator exceptions, provider iteration failures, cleanup failures,
client disconnect and task cancellation through the actual ASGI chat response.
They also cover a disconnect during an in-flight synchronous read and retained
permit ownership. The 42 frontend tests preserve Stop/abort, stale-result rejection,
terminal events and pagination behavior.

An additional audit-only probe used the installed OpenAI 3.3.1 SDK and an in-memory
HTTP transport with sockets blocked. Normal completion, cancellation and injected
generator error each closed the response body exactly once, including repeated
application `close()` calls. No real provider request occurred.

Limit: cancellation during an active synchronous SDK read waits for that read to
return or fail before generator cleanup. The permit remains retained until then.
This is not instant interruption, reversal of already-incurred billing, or a promise
that all remote provider work stops immediately.

### F3: ENVIRONMENT normalization - PASS

`app/services/environment.py` now owns the shared `.strip().lower()` interpretation.
Auth, runtime validation and resource policy use it. Both `production` and `staging`
apply the secure policy; neither silently defaults resource Redis to localhost.
`development` and `test` keep their local defaults. Unknown/empty values fail with a
fixed configuration error that does not echo the supplied value.

Evidence: 33 auth/config and 26 runtime/observability tests pass, including canonical
and padded production, uppercase/mixed case, canonical/padded staging, development,
test, empty/whitespace and invalid values. They verify secure cookies and consistent
Celery, Redis, PostgreSQL URL and JWT-strength requirements. Source search confirms
this helper is the only application interpretation of `ENVIRONMENT`.

## Git history: Batches 1-15 present - PASS

| Batch | Commit | Scope |
| --- | --- | --- |
| 1 | `e3b1f12` | Test database isolation |
| 2 | `a1e74b6` | Next.js security patch |
| 3 | `6ce784d` | Private document image delivery |
| 4 | `bf5ad7b` | Safe public generation errors |
| 5 | `018da37` | Embedding inspection/recovery |
| 6 | `408928f` | Document-processing reliability |
| 7 | `a83f51c` | Summary concurrency |
| 8 | `6c5672e` | Resource admission and quotas |
| 9 | `2026bc5` | Upload/parser resource limits |
| 10 | `f39d981` | Authentication/session hardening |
| 11 | `223b7b4` | Database scalability/pagination/indexes |
| 12 | `fd1147a` | RAG correctness and retrieval safety |
| 13 | `ba588b2` | Frontend streaming/request reliability |
| 14 | `2888794` | Observability, CI and runtime hardening |
| 15 | `7de609d` | Safe load-test framework |
| Audit fixes | `b0c8744` | The three previous confirmed code failures |

Batch 11 was committed after Batch 12; both remain present. Local `main` is an
ancestor: 16 commits are unique to `production-hardening`, none to local `main`.
Remote branch state, hosted CI and deployment triggers were not queried.

## Original major findings and current repository status

PASS below applies to the described repository control; warnings and live
acceptance requirements are listed separately rather than implied to be resolved.

| Area / original concern | Classification | Current evidence and boundary |
| --- | --- | --- |
| Tests could target application DB | PASS | Explicit test URL, disposable run DB identity, no application URL fallback; 20 guard tests. Migration-running integration fixtures were excluded. |
| Vulnerable dependencies | PASS | Fresh frontend audit: zero vulnerabilities. Fresh backend requirements/dev/transitive audit: 68 dependencies, no known vulnerabilities or skips. Point-in-time results, not a vulnerability-free certification. |
| Authentication / authorization | PASS | Required JWT claims/types, explicit verification algorithm, current-user lookup, ownership/attachment checks, secure host-only HttpOnly cookies and OAuth state checks. See W3 and W8. |
| Private document/image access | PASS | Owner-checked image proxy, origin/path restrictions, private caching, bounded remote reads and authenticated future uploads; 33 tests. Legacy origins remain W1. |
| Public error safety | PASS | Previous F1 fixed; generation serializers and allowlisted diagnostics remain covered. |
| Rate limits / quotas / DoS controls | PASS | Redis sliding windows, verified-user rates, PostgreSQL permits and serialized upload quotas. New work fails closed; recovery deletion/cancellation remains available on limiter outage. See W8. |
| Upload/parser limits | PASS | Default 50 MiB file cap plus bounded multipart overhead; one file, extension/content checks, PDF/page/content, ZIP/XML/Office/text/chunk bounds; 38 tests. Host/edge isolation remains necessary. |
| Document-processing reliability | PASS | Session claims, checkpoint/resume, bounded typed retries, duplicate handling, Celery late ACK/prefetch/time limits; 29 tests. Worker-loss acceptance remains pending. |
| Summary concurrency | PASS | Scoped claims, short mutation locks, conditional transitions, cancellation/stale-completion guards; 32 tests. Real multi-session behavior remains pending. |
| Embedding recovery | PASS | Explicit-target dry-run tooling, schema/model validation, bounded writes and preservation of valid vectors; 30 tests. No live inventory/recovery was run. |
| Main document/chat/message pagination | PASS | Default 50/max 100, signed owner/resource cursors and stable ID tie-breakers; older messages remain reachable. Compatibility offset exists; deep traversal should use cursors. Assistant history is W2. |
| N+1 / SQL filters / vector projections | PASS | Covered select-in relationships, SQL filtering/scans, deferred vectors and ordinary read-transaction release; 25 database scalability tests. Not a universal query-performance claim. |
| Index/pool configuration | PASS | Exactly five approved concurrent indexes; actual chat order matches; bounded configurable pools without capacity increases. Real plans/capacity are NOT LIVE-VERIFIED. |
| RAG correctness | PASS | Formula/equation compatibility, one-based/unknown page handling, exact-page retrieval, prompt data boundaries, Datalab retry rewind, per-request embedding reuse and vector validators; 28 tests. Retrieval quality/provider conventions remain unverified. |
| Frontend streaming/cancellation | PASS | Final buffered NDJSON, required/validated done, safe premature EOF, request ownership, Stop/abort and refetch deduplication; all 42 frontend tests pass. Backend F2 fixed. |
| Observability/logging | PASS | Allowlisted JSON fields, request/correlation/job IDs, exception types and lightweight observer hooks; 26 tests. Collection/adapters/alerts remain W10. |
| Health/readiness | PASS | `/health` indicates process liveness. `/ready` uses cached, bounded PostgreSQL and required Redis probes, without provider calls. It does not prove workers, schema head or pool headroom. |
| CI/security configuration | PASS | Four jobs, safe backend suites, frontend gates, migration render, dependency scans, pinned action SHAs and Dependabot; actionlint and local YAML/config assertions pass. Hosted execution remains unverified; see W7. |
| Docker/runtime configuration | PASS | Non-root UID/GID 10001, explicit COPY inputs, no baked env files, safe startup validation, default proxy/access logs disabled; F3 fixed. Image execution/topology remain unverified. |
| Load-testing readiness | PASS | Seven guarded scenarios, separate ordinary/provider profiles, opt-ins, aggregate metrics and pressure importer; 27 tests and 14 k6 inspections. No load execution or performance result. |

## Remaining FAIL items

**None confirmed in the reviewed repository scope.** The prior F1/F2/F3 findings
are closed by `b0c8744` and current offline evidence. This does not clear the
warnings or replace deployment acceptance.

## Remaining WARNING items

**W1 - Legacy public image origins.** Existing public Cloudinary objects and
CDN/derived copies remain a privacy release gate for a deployment retaining those
objects. Application proxy authorization does not revoke old public URLs. Inventory
and remediate them, then verify unsigned URLs are denied. No live exposure was
queried, and no storage-side remediation was performed.

**W2 - Summary-assistant history and provider wait.**
`app/services/summaries/summary_assistant_service.py::send_summary_assistant_message`
loads recent ORM history and waits for its provider without ending that ordinary
read transaction. Its separate OpenAI client has no explicit timeout/retry override;
the installed SDK default is 600 seconds with two retries. This can retain an
ordinary connection in addition to the intentional admission permit. The history
GET in `app/routes/summary_assistant.py` defaults to `limit=None` (explicit limits
allow up to 500), and the frontend omits a limit, so complete instruction history
is fetched. Main chat message pagination is correct. Universal bounded-history or
short-provider-session claims remain unsupported; use a focused follow-up.

**W3 - Session limitations.** Logout deletes cookies but does not revoke copied
JWTs; default lifetime is seven days. No server-side revocation, MFA or universal
CSRF-immunity claim is supported. Validate OAuth state/redirects and exact-origin,
SameSite and cookie behavior in the chosen browser/proxy topology.

**W4 - Frontend routing/configuration.** Next's `/api/backend` rewrite has a fixed
hosted Render destination. `NEXT_PUBLIC_API_URL` can select a separate direct API,
but the rewrite itself is not environment-configurable. A staging frontend using
that proxy route could reach the existing hosted backend. Verify routing before
any browser or load execution. Missing frontend API configuration falls back to
localhost; set the public site URL too. Build success is not routing acceptance.

**W5 - Documentation/configuration examples.** The top-level README still names
Sentence Transformers although current embeddings use Voyage; `.env.example`
omits the required Voyage key and some provider/deployment settings. Batch 14's
historical runbook retains the old two-high audit result, superseded by this fresh
clean scan. Example defaults and README setup commands are not a production runbook.
No unrelated documentation was changed in this audit.

**W6 - Existing frontend lint warnings.** Nineteen warnings remain: hook dependency,
unused-value and image/navigation guidance. There are zero errors and the configured
`--max-warnings 19` gate passes. No unrelated cleanup was performed.

**W7 - CI/supply-chain coverage.** Batch 15 Node safety checks/k6 inspection are not
wired into CI. Direct backend requirements are pinned, but the full Python
transitive graph and Docker base digest are not locked. Scans here resolved on
Windows/Python 3.14; hosted Python 3.11/Linux resolution still needs its own green
gate. Registry audits do not scan the whole image/OS or certify the supply chain.
Targeted credential checks passed, but no exhaustive Git-history secret scanner
was run. Branch protection and hosted CI results remain unverified.

**W8 - Edge/resource/billing limitations.** Per-user quotas are not global cost
ceilings. Invalid-token/large JSON requests and multipart ingestion need edge
admission, body/read-time and slow-client controls; parser bounds are not process
isolation. Original-file quotas do not bound every image, message, temporary file
or provider dollar. Authentication buckets use the ASGI peer; a shared proxy can
collapse clients into one bucket until a restricted trusted-proxy/edge policy is
reviewed. Do not increase application limits to hide this behavior.

**W9 - Recovery/cancellation limitations.** Dead workers may leave visible states
requiring explicit recovery. Deletion/cancellation can leave already-started
provider work or remote assets. Answer-stream cleanup is fixed, but in-flight
synchronous reads still delay it. No exactly-once, instant cancellation, automatic
orphan cleanup or no-post-cancel-billing guarantee exists.

**W10 - Operational ownership.** Safe observer adapters, log collection, retention,
alerts, backups/restore, secret rotation, storage lifecycle and host CPU/memory/disk
limits need deployment configuration and verification. Hooks/runbooks alone do not
establish those controls.

## NOT LIVE-VERIFIED acceptance work

1. **PostgreSQL migration/data:** actual revision, pgvector version, index validity,
   concurrent migration interruption/recovery, and embedding completeness/provenance.
2. **Distributed reliability:** multi-session claims/permits, Redis atomic windows
   and outages, worker restart/redelivery, queue pressure and connection release.
3. **Query performance:** staging EXPLAIN ANALYZE/BUFFERS for first/deep pages,
   index selection/write cost, filtered vector plans and real query latency.
4. **Pool budget:** actual API/worker process counts and per-process capacities,
   retained claim/permit connections, rolling overlap and headroom. The example
   `(4 API + 2 workers) * (5 + 10) = 90` is a theoretical application ceiling,
   not a recommended PostgreSQL budget. Include up to one extra transient readiness
   connection per API process, migrations, administration and other clients.
   Session advisory locks require direct PostgreSQL or session pooling, not
   transaction pooling. Readiness does not prove sufficient capacity.
5. **HNSW recall:** measure filtered recall/shortfall against an exact baseline.
   HNSW behavior was not changed and no recall/performance result was invented.
6. **Datalab page convention:** establish selected-page numbering using trusted
   fixtures. The default `unknown` excludes unresolved pages from exact-page
   retrieval; historical labels and provider mapping are not certified.
7. **Browser E2E:** Google auth/cookies/logout, ownership/private images, uploads,
   switching chats/documents, disconnect/Stop, refetch deduplication and Load older
   messages through the actual browser-facing TLS/proxy layout.
8. **Docker/hosted CI:** Linux image build/startup, non-root permissions/shared
   mounts, worker health, signals, hosted Actions, protected branches and secret/env
   injection. Docker was unavailable locally; no image was built or run.
9. **Load/capacity:** real RPS, error rate, avg/p90/p95/p99, DB/Redis/worker saturation
   and sustainable capacity. k6 inspect starts no VUs. No workload, measured
   bottleneck or 500-user result exists.
10. **Edge/storage:** CDN/WAF limits, slow-client protection, TLS/network isolation,
    origin protection, cache invalidation and legacy-image access revocation.

## Test/build/security results

All listed results were obtained during this re-audit's 2026-09-15 validation.
Finalization on 2026-09-17 retained those successful results without rerunning the
suites, build or registry audits; only the report and final working-tree/whitespace
checks were revisited. Dependency results are point-in-time findings as of the
scan date, not a new 2026-09-17 vulnerability scan.

| Check | Classification | Result |
| --- | --- | --- |
| Backend `python -B backend/scripts/ci_tests.py` | PASS | 358 tests across 12 isolated suites |
| Python `backend/scripts/ci_checks.py` | PASS | 115 files parsed using Python 3.11 grammar, compiled without executing app code; static logging checks |
| Migration graph/render | PASS | One head; four reachable revisions; upgrade/downgrade SQL rendered only in memory with connections blocked |
| Backend `pip check` / installed direct requirements | PASS | No broken requirements; no direct requirement/version mismatches |
| Frontend `node --test tests/*.test.mjs` | PASS | 42 tests |
| TypeScript `node node_modules/typescript/bin/tsc --noEmit` | PASS | No errors |
| ESLint `npm run lint -- --max-warnings 19` | PASS | 0 errors / 19 warnings (W6) |
| Frontend `npm run build` | PASS | Next.js 16.3.3 production build; no metadataBase warning |
| Frontend `npm audit --package-lock-only --audit-level=high --json` | PASS | 0 vulnerabilities at every severity, including dev dependencies |
| Backend `pip-audit --strict -r backend/requirements-dev.txt --progress-spinner off --format json` | PASS | pip-audit 2.10.1; 68 resolved dependencies; no known vulnerabilities, no skips; isolated audit environment |
| actionlint 1.7.12 | PASS | No reported workflow errors |
| Workflow/Dependabot YAML/config assertions | PASS | Four jobs, blank DB URLs/test env, read-only permissions, SHA-pinned actions, no persisted checkout credentials, bounded jobs, four ecosystems |
| Load-tooling Node tests | PASS | 27 tests |
| Load-tooling example validation | PASS | Synthetic configuration only; no k6 execution |
| k6 2.2.0 inspection | PASS | Seven scenarios in smoke and staged profiles (14 combinations), synthetic target/credentials and shared fixture mode; no setup/VUs/HTTP |
| Load module/JSON syntax | PASS | Seven JS/MJS files including the test file; package/k6 JSON parse |
| Installed-SDK audit probe | PASS | In-memory completion/cancel/throw closed HTTP body once, sockets blocked |
| Targeted credential checks | PASS | No tracked private env/key files, no selected credential-pattern matches, no private env/key filenames in available history; limited scan (W7) |
| Docker / hosted CI / browser E2E / live services | NOT LIVE-VERIFIED | Not executed; no results inferred from local checks |
| `git diff --check` and report whitespace | PASS | No whitespace errors; final status contains only this uncommitted report |

Backend suite breakdown: database safety 20; private images 33; public errors 22;
embedding recovery 30; document processing 29; summary concurrency 32; resource
admission/stream cleanup 42; upload limits 38; auth/config 33; RAG 28; database
scalability 25; observability/runtime 26.

The total is **427 automated tests** (358 backend + 42 frontend + 27 load-tooling),
plus static/config checks and the separate installed-SDK probe. These are not
browser or live-service test counts. Backend execution used Python 3.14.7;
Python 3.11 grammar validation is not a Python 3.11 runtime test. Frontend execution
used Node 24.12.0; hosted CI specifies Node 22. Normal pytest discovery/integration
suites were excluded because their fixtures execute migrations.

The frontend build used its existing local build configuration without printing
secret values; it did not start an application server or test the hosted rewrite.
Dependency scans contacted public registries only. An initially sandbox-blocked
npm request and a pip-audit environment setup error were resolved before the
successful fresh scans; neither is reported as a completed security scan.

## Database migration status

**PASS repository render; NOT LIVE-VERIFIED deployment.**
Repository head is `b11d62a4c901`, parent `957d795d2816`. It contains exactly:

- `documents(user_id, created_at DESC, id DESC)`
- `chats(user_id, is_archived ASC, is_pinned DESC, created_at DESC, id DESC)`
- `messages(chat_id, created_at ASC, id ASC)`
- `document_chunks(document_id, id)`
- `chat_documents(document_id, chat_id)`

Upgrade/downgrade use concurrent DDL inside Alembic autocommit blocks; the migration
environment uses `transaction_per_migration=True`. Actual chat ordering is
compatible with the approved index. Concurrent builds are non-atomic and require
partial/invalid-index inspection and recovery on interruption; reduced write
blocking does not mean zero locks or zero resource cost.

`message_documents(document_id, message_id)` stays deferred. HNSW is unchanged.
The three historical migrations match local `main`. The older dimension migration
clears embeddings when traversed: do not blindly apply the migration chain or infer
embedding completeness from document status. No new migration was required by
`b0c8744` or this audit. **No migration, live Alembic check, live database plan/query,
embedding recovery, production PostgreSQL/Redis connection, real provider call or
live load test was executed.** Actual deployed revision is unknown.

## Push, merge and unrestricted public release

**Push to GitHub: reasonable for review and hosted CI.** The reviewed branch has
no confirmed remaining blocker, local gates pass, and targeted credential checks
found no matches. Review normal repository secret policy and any automatic
deployment trigger before pushing. This audit did not push or query hosted status.

**Merge into `main`: reasonable as a reviewed hardening change, conditional on
hosted CI passing and maintainer review.** The previous three code blockers no
longer justify holding a source-only merge. If merging `main` automatically deploys
or opens unrestricted public access, keep that release gate closed until the
applicable acceptance work below is complete. A clean local branch/test result
is not evidence that hosted CI or production has passed.

Before unrestricted public production:

1. Remediate/verify legacy image origins where applicable and review frontend/API
   routing, secure cookies/OAuth, secrets, TLS and edge controls.
2. Obtain hosted CI and Docker runtime evidence, including the deployment's
   Python/Node versions, worker setup, mounts and environment validation.
3. Review backups/recovery, execute the approved migrations only in separately
   authorized environments, verify index validity/data completeness, and capture
   query plans and a realistic DB connection budget.
4. Complete isolated multi-process/Redis/worker failure testing, browser E2E,
   Datalab page-contract fixtures and HNSW recall validation.
5. Use the guarded load tooling on a separately approved isolated stack to measure
   capacity and dependency pressure. Start with synthetic/mocked providers;
   real-provider spending needs explicit authorization and a budget.
6. Resolve or explicitly accept the summary-assistant session/history limitation
   against expected traffic, and assign owners for logging/alerts, backups/restore,
   recovery, quotas/billing and the other warnings.

## Safe CV / GitHub README claims

Supported wording:

> Built a Next.js/FastAPI document assistant with PostgreSQL/pgvector RAG,
> owner-scoped private image delivery, distributed resource admission,
> checkpointed document processing, summary concurrency controls, keyset
> pagination, stale-response-safe frontend streaming, explicit provider-stream
> cleanup, safe structured logging and dependency-scanning CI configuration.
> Passed 358 backend, 42 frontend and 27 load-tooling tests locally, plus
> TypeScript, production build and security audit gates; prepared guarded k6
> scenarios for staging validation.

Use the date when claiming clean dependency audits. Qualify performance and
production-hardening claims as repository implementation/offline validation with
staging acceptance pending. Current embeddings are Voyage, not Sentence
Transformers. "CI configured" is supported; "hosted CI passed" is not yet supported
by this audit. "Previous three audit blockers fixed and regression-tested" is
supported. The 19 lint warnings should not be described as a warning-free build.

Do not claim DDoS-proof, bug-free, fully production-proven, proven 500-user capacity,
measured throughput/latency gains, certified HNSW recall, verified Datalab page
mapping, guaranteed instant provider cancellation/no post-cancel billing,
exactly-once jobs, fully revoked legacy public images, complete prompt-injection
protection, universal bounded histories, or a vulnerability-free application.
No staging results were invented. Only this audit report remains uncommitted.
