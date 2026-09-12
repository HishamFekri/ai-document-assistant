# Batch 11: database scalability and staging validation

Batch 12 (`fd1147a`) is the correctness baseline. This batch changes database
access and list delivery, not retrieval quality, HNSW settings, provider policy,
or the intentional ownership of advisory-lock/admission connections.

## API and frontend behavior

List defaults are **50**, maximum **100**, centralized in
`app/services/pagination.py`. Requests outside 1–100 receive 422. Responses remain
JSON arrays. `X-Next-Cursor` contains a continuation token when another page
exists; its absence means the list is exhausted. CORS exposes this header.
Send it as `?limit=50&cursor=<URL-encoded token>` on the same endpoint/filter.
Tokens are signed using the existing JWT secret with a pagination-specific
prefix and bound to the authenticated owner, resource and filter values.
Malformed, tampered or mismatched tokens receive 400. Secret rotation requires
starting a fresh first page. Tokens are authenticated, not encrypted.

| Resource | SQL ordering and continuation | Frontend access |
| --- | --- | --- |
| Documents | `created_at DESC, id DESC`, scoped by owner | Dashboard: Load more documents |
| Chats | `is_archived ASC, is_pinned DESC, created_at DESC, id DESC`, scoped by owner, existing message-existence filter retained | Sidebar: Load more chats |
| Messages | Read newest window using `created_at DESC, id DESC`, scoped to an owned chat; reverse each returned window for chronological presentation | Chat: Load older messages; prepend with scroll position preserved |
| Summary history | `is_selected DESC, created_at DESC, id DESC`, scoped to owned document/chat and optional mode | API continuation; current UI uses selected-summary endpoint |
| Assets | `id ASC`, scoped to owned document and optional asset type | API continuation; current UI requests assets by ID |

Chat/message legacy offsets remain accepted, but cannot be combined with a
cursor. Their windows follow the ordering above; clients should prefer keysets.
The first message page is now the latest 50, not an unbounded entire history.
Frontend merges deduplicate IDs, keep older loaded messages on refresh, and use
the same timestamp/ID ordering. Dashboard's five-chat preview remains a preview;
the workspace sidebar provides complete chat navigation.

Keysets avoid growing offsets and resolve timestamp ties. They are not a database
snapshot across requests. Pin/archive/summary-selection changes can move records
across a cursor boundary; refresh the list after those mutations. Concurrent
deletion removes rows normally. Older unchanged rows remain reachable by following
continuations until the header disappears. Do not interpret a loaded-page count
as an exact total; dashboard shows `+` while more documents remain.

## Query and transaction changes

* Chat and message list serializers select attachments once per page, replacing
  lazy relationship reads per row. Offline SQL counts are two SELECTs for chats
  and three for messages, including message ownership validation.
* Queued-message discovery filters waiting user messages in SQL and scans by
  `(chat_id, created_at, id)` in batches of 100. A single status projection reads
  attachment readiness per batch. Processing retains per-message conditional
  claims and writes; those necessary operations are not represented as a constant
  query count. Readiness values survive commits without lazy relationship reads.
* Internal chunk/asset scans use ascending keysets of at most 100 rows per query.
  Existing safe document/type filters precede the SQL limit. Exact-page retrieval
  excludes explicit `page_mapping_status = 'unknown'` in SQL; legacy page
  normalization, formula/equation compatibility, deduplication and final matching
  limits remain in the Batch 12 logic. No speculative casts of legacy metadata
  or early raw-row limit replace that logic.
* Summary character budgets can stop before fetching remaining batches. Full
  transcription still visits all relevant pages. Representative sampling still
  materializes eligible rows to preserve its evenly distributed selection, and
  companion image logic still checks global duplicate counts. SQL fetch sizes
  are bounded; these two algorithms are not constant-memory operations.
* `DocumentChunk.embedding` is deferred. Metadata/content reads omit Vector(512)
  payloads. Explicit vector distance expressions and Batch 12 validation queries
  continue to access the column in SQL. Accessing the attribute explicitly still
  loads it while attached to a session; callers must not depend on deferred lazy
  loads after detaching an object.
* Ordinary read transactions roll back before query embedding, intent detection,
  answer generation, streaming answer generation and title generation waits.
  Prompt inputs are materialized first; intent detection receives ID/name
  snapshots. The outer streaming request transaction also ends before returning
  the response. The read-release helper refuses to discard pending ORM changes.
* Batch 6 processing and Batch 7 summary provider boundaries remain in place.
  Datalab retry rewind, Cloudinary handling and the dedicated Batch 6/7/8 lock or
  permit connections retain their existing ownership/lifetime. A rollback of a
  Session bound to a dedicated Connection does not release that Connection.

## Pool deployment budget and statement timeouts

`app/database/pool_config.py` configures each **application process**:

| Environment variable | Development default | Accepted values |
| --- | --- | --- |
| `DB_POOL_SIZE` | 5 | 1–5 |
| `DB_MAX_OVERFLOW` | 10 | 0–10 |
| `DB_POOL_TIMEOUT` | 30 seconds | 1–300 seconds |
| `DB_POOL_RECYCLE` | -1 (disabled) | -1 or 1–86400 seconds |
| `DB_POOL_PRE_PING` | true | true / false |

Capacity is not increased. Unlimited overflow and zero/unbounded pool size are
rejected. Pre-ping now checks connection liveness at checkout; it does not recover
an interrupted transaction. Recycle is connection age at checkout, not a deadline
for an active operation. Pool timeout controls waiting for a pool slot, not SQL
execution or provider duration.

Production must explicitly choose process counts and per-process settings within
the actual PostgreSQL connection budget:

```
API processes × (API pool_size + API max_overflow)
+ worker processes × (worker pool_size + worker max_overflow)
+ administrative / migration / monitoring / other-client / failover headroom
<= usable PostgreSQL connection budget
```

For illustration, `(4 API + 2 worker processes) × (5 + 10) = 90` application
connections is a **theoretical capacity ceiling, not a recommended PostgreSQL
budget**. Check the deployed process topology and server limits before choosing
values. Include rolling-deployment overlap if old and new processes coexist.
API and worker deployments can set different values within the validated bounds.

Ordinary queries and retained locks/permits consume slots from their process's
pool; do not count the same physical connection twice. Batch 6 processing holds
a claim connection, with its processing permit sharing that connection. Chat and
search permits can coexist with a separate ordinary query connection. Summary
admission and summary claims can hold separate connections, alongside transient
request reads. Background titles can also use a query connection. Budget active
operations accordingly: lowering a pool until all slots are held by permits can
starve the queries those operations need. Shorter ordinary transactions reduce
unnecessary occupancy but do not remove these retained connections.

No application statement timeout is introduced. The repository did not configure
one; actual server/role defaults may differ. Staging must measure ordinary lists,
vector retrieval and internal scans before choosing a production application-role
timeout. Keep a separately configured migration connection/role so index builds
do not inherit an aggressive application timeout. Use transaction-local timeouts
for staging probes and confirm reset at transaction end. A global arbitrary
deadline would risk aborting valid vector queries or concurrent index builds.

## One new index migration; execution requires a separate deployment

Revision `b11d62a4c901`, parent `957d795d2816`, adds exactly these non-unique B-tree
indexes. Historical revisions are unchanged. ORM metadata matches the new indexes.

| Index | Columns | Existing overlap and expected benefit |
| --- | --- | --- |
| `ix_documents_user_created_id` | `user_id, created_at DESC, id DESC` | ID PK alone cannot serve owner/date pages; supports filtering and ordering |
| `ix_chats_user_archive_pin_created_id` | `user_id, is_archived ASC, is_pinned DESC, created_at DESC, id DESC` | ID PK lacks owner/group ordering; exact production list ORDER BY matches this index |
| `ix_messages_chat_created_id` | `chat_id, created_at ASC, id ASC` | ID PK lacks chat prefix; supports chat existence/history and backward scans for newest-first pages |
| `ix_document_chunks_document_id_id` | `document_id, id` | ID PK and global HNSW serve different access patterns; supports document-scoped chunk scans |
| `ix_chat_documents_document_chat` | `document_id, chat_id` | Existing PK is `(chat_id, document_id)`; reverse direction supports document-to-chat discovery/deletion |

All five add storage, WAL, build I/O and write maintenance. Pin/archive updates
also update the chat ordering index. No existing index is dropped. Existing asset
and summary indexes remain; their suitability must be measured before proposing
anything additional. `message_documents(document_id, message_id)` remains
**deferred** until staging plans or later load tests justify its cost. HNSW and
Batch 12 vector validation helpers are unchanged.

Both upgrade and downgrade use PostgreSQL concurrent DDL inside Alembic's
`autocommit_block()`. `migrations/env.py` now uses `transaction_per_migration=True`
in online and offline configuration. Alembic manages the autocommit isolation
required outside a normal transaction block. Its migration engine retains
`NullPool`; application pool settings are not applied to it. Deploy with the
normal Alembic environment, not a wrapper that encloses the entire SQL stream in
`BEGIN`/`COMMIT`. Generated offline SQL must preserve its transaction boundaries.

Concurrent builds allow ordinary writes, unlike normal CREATE INDEX, which would
block table writes for the build. Concurrent does not mean zero impact: expect
extra scans, I/O, snapshot waits and contention with maintenance/DDL. Schedule
within staging-measured I/O and connection headroom. Use a single migration
runner, and avoid concurrent builds/maintenance on the same table. Downgrade
drops one index at a time concurrently, in reverse order, with `IF EXISTS` for
safe retry of partial removal. There is no CASCADE and no constraint removal.

The revision is deliberately **non-atomic**. Autocommit commits any preceding
transaction before entering the block. A failure can leave earlier valid indexes
and an invalid interrupted index while the Alembic revision remains at the
parent. Upgrade does not use `IF NOT EXISTS`: that could silently accept a wrong
or invalid index and falsely report completion.

Operator procedure for a separately approved staging/deployment window:

1. Verify the target database identity, current Alembic revision, server version,
   free disk and connection headroom; inspect for names left by earlier attempts.
2. Run only the reviewed revision through the normal migration runner. This
   document does not authorize or perform that execution.
3. On interruption, inspect the migration revision and all five named indexes
   using `pg_index.indisvalid`, `indisready`, `pg_get_indexdef(indexrelid)` and table
   identity. Do not blindly rerun, stamp success, or assume an existing name is
   the correct index.
4. If still at the parent, the simple recovery is a reviewed removal of only the
   indexes verified to belong to this attempt, one at a time with DROP INDEX
   CONCURRENTLY outside a transaction, followed by retry of this revision. Name
   collisions or unexpected definitions require investigation before removal.
   If revision state differs, reconcile it explicitly before retrying.
5. Confirm all five definitions are exact and valid/ready, and the revision is
   advanced. For downgrade, confirm all five are absent and the parent revision
   is recorded; partial downgrade can be retried because drops use IF EXISTS.

See the [Alembic autocommit contract](https://alembic.sqlalchemy.org/en/latest/api/runtime.html#alembic.runtime.migration.MigrationContext.autocommit_block),
[PostgreSQL CREATE INDEX](https://www.postgresql.org/docs/current/sql-createindex.html),
[DROP INDEX](https://www.postgresql.org/docs/current/sql-dropindex.html), and
[SQLAlchemy engine configuration](https://docs.sqlalchemy.org/en/20/core/engines.html).

## Staging EXPLAIN (ANALYZE, BUFFERS) runbook — not executed

Use a separately authorized disposable staging PostgreSQL database with
representative, non-production-sensitive data and the deployed PostgreSQL/pgvector
versions. Do not fall back from an absent TEST_DATABASE_URL to DATABASE_URL.
EXPLAIN ANALYZE executes the SELECT; run these only against confirmed staging.
This is individual query-plan validation, not a load test.

Capture plans before and after the reviewed index deployment, with comparable
data, statistics and session settings. Confirm normal statistics maintenance;
label cold/warm-cache runs instead of flushing shared production caches. Choose
small, medium and high-cardinality staging owners/chats/documents, including tied
timestamps and both pin/archive groups. Resolve placeholders below from that
synthetic fixture. Set a locally approved statement/lock timeout for the probe
session and use a READ ONLY transaction for SELECT probes; index deployment must
be a separate autocommit operation. Roll back the probe transaction afterward.

```sql
-- Replace :parameters through your staging SQL client's parameter binding.
EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
SELECT d.* FROM documents d
WHERE d.user_id = :owner
ORDER BY d.created_at DESC, d.id DESC LIMIT 51;

EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
SELECT d.* FROM documents d
WHERE d.user_id = :owner
  AND (d.created_at < :created OR (d.created_at = :created AND d.id < :id))
ORDER BY d.created_at DESC, d.id DESC LIMIT 51;

EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
SELECT c.* FROM chats c
WHERE c.user_id = :owner AND EXISTS (SELECT 1 FROM messages m WHERE m.chat_id = c.id)
ORDER BY c.is_archived ASC, c.is_pinned DESC, c.created_at DESC, c.id DESC
LIMIT 51;

-- The second chat page uses the application's boolean casts in its predicate,
-- while keeping the uncast ORDER BY compatible with the approved index.
EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
SELECT c.* FROM chats c
WHERE c.user_id = :owner AND EXISTS (SELECT 1 FROM messages m WHERE m.chat_id = c.id)
  AND (CAST(c.is_archived AS INTEGER) > :archived_int
    OR (c.is_archived = :archived AND CAST(c.is_pinned AS INTEGER) < :pinned_int)
    OR (c.is_archived = :archived AND c.is_pinned = :pinned AND c.created_at < :created)
    OR (c.is_archived = :archived AND c.is_pinned = :pinned AND c.created_at = :created AND c.id < :id))
ORDER BY c.is_archived ASC, c.is_pinned DESC, c.created_at DESC, c.id DESC
LIMIT 51;

EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
SELECT m.* FROM messages m
WHERE m.chat_id = :chat
  AND (m.created_at < :created OR (m.created_at = :created AND m.id < :id))
ORDER BY m.created_at DESC, m.id DESC LIMIT 51;

EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
SELECT dc.id, dc.document_id, dc.content, dc.content_type, dc.location, dc.metadata
FROM document_chunks dc
WHERE dc.document_id = :document AND dc.id > :last_id
ORDER BY dc.id ASC LIMIT 100;

EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
SELECT cd.chat_id FROM chat_documents cd WHERE cd.document_id = :document;

EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
SELECT s.* FROM document_summaries s
WHERE s.chat_id = :chat AND s.document_id = :document AND s.mode = :mode
ORDER BY s.is_selected DESC, s.created_at DESC, s.id DESC LIMIT 51;

EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
SELECT a.* FROM document_assets a
WHERE a.document_id = :document AND a.asset_type = :asset_type AND a.id > :last_id
ORDER BY a.id ASC LIMIT 51;
```

Also capture the actual SQL generated by the application for the select-in
attachment queries, queued-message discovery/status projection, exact-page
metadata exclusion, multi-document chunk continuation and vector distance queries.
Use the unchanged [Batch 12 runbook](rag-correctness-validation.md) for vector
correctness comparisons. Supply synthetic valid 512-dimensional query vectors
locally; do not call an embedding provider. Do not tune HNSW in this batch.

For every plan record: fixture size/cardinality, first vs deep page, returned IDs,
planning time, execution time, actual rows and loops at each node, rows removed
by filters, returned rows, shared buffer hits/reads, temporary reads/writes, sort
method/memory/disk spill, chosen index and unexpected sequential scans. Estimate
scan work from node rows multiplied by loops rather than returned rows alone.
Check that all continuations terminate without missing/duplicate IDs on a fixed
fixture and that the chat ORDER BY remains unchanged. Inspect whether the OR
cursor predicate scans many earlier entries on deep pages despite index ordering;
index availability does not guarantee an optimal plan.

Record connection checkout/occupancy during mocked slow provider waits and
retained claims/permits, pool wait/timeout behavior at the intended process count,
index size/build time, write overhead and interruption recovery in staging.
Separate later load testing is still required to establish throughput and a
production connection budget. No performance improvement percentage is claimed
from SQLite/mocked offline checks.

## Offline validation boundary

`tests/test_database_scalability.py` uses an explicit disposable in-memory SQLite
engine and synthetic rows for HTTP pagination, query counts and ORM lifetimes.
PostgreSQL vector expressions are compiled only; migration operations are mocked
or rendered to an in-memory SQL buffer without a database connection.
It does not prove PostgreSQL planner selection or concurrent DDL locking. Prior
Batch 1/5/6/7/8/12 standalone regression harnesses also isolate providers and
external services. The regular PostgreSQL fixture suite must not be run without
an explicitly configured isolated test database because it executes migrations.

This implementation session did not execute the migration, EXPLAIN ANALYZE,
production PostgreSQL/Redis, real providers or load tests. Deployment and the
staging measurements above remain separate work.

Validation completed for this working tree: Batch 11 **25** tests (including the
12 existing document/chat ownership cases on SQLite); Batch 1 **20**, Batch 4
**19**, Batch 5 **30**, Batch 6 **29**, Batch 7 **32**, Batch 8 **38**, Batch 10
**32**, and unchanged Batch 12 **28** tests passed. Frontend pagination/logout/
upload-policy tests: **18 passed**. TypeScript no-emit and production build passed;
the build reports the existing unset `metadataBase` warning. ESLint retains the
same **4 errors / 20 warnings**, with no new findings. Changed Python files passed
Python 3.11 grammar validation and compile checks under local Python 3.14.7;
execution on a Python 3.11 runtime and actual PostgreSQL remains a staging check.
