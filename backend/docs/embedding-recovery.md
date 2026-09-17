# Embedding verification and recovery

Migration `957d795d2816` cleared 384-dimensional embeddings before changing the
column to `vector(512)`. It did not update document status or regenerate vectors.
Leave that migration immutable. This command does not run Alembic or alter schema.

The current configuration is Voyage `voyage-4-lite`, 512 dimensions, float output,
with `input_type=document` for chunks and `query` for search. The application still
supports its existing `VOYAGE_MODEL` setting; this batch does not change it.

## Meaning of completeness

Ingestion embeds every nonblank chunk, including image descriptions, tables,
formulae and code. Maintenance therefore checks all nonblank content; whitespace
is identified with PostgreSQL's POSIX space character class. Search checks only
its requested content types (normally text/table/equation; image for visual
search). A usable vector is non-NULL, 512-dimensional and nonzero for cosine
similarity. pgvector itself rejects nonfinite stored elements. Counts are SQL
aggregates; inspection never transfers whole vectors to Python.

Reports distinguish `fully_embedded`, `partially_embedded`, `no_valid_embeddings`,
and `no_embeddable_chunks`. An empty document is not embedding-complete.
`semantic_ready` additionally requires the existing document status to be `ready`.
This is a maintenance report field, not a new stored status or public API field.

Newly ingested/repaired vectors carry `metadata.embedding_generation`: provider,
model, dimension, input type, output dtype and request format version. Other JSON
metadata is preserved. This is request provenance, not a provider revision pin.
Usable legacy vectors lacking this record, or carrying different generation
metadata, are preserved and counted as `unverified_generation_chunks`. Dimension
alone cannot establish model compatibility. Review those counts separately;
this tool does not perform a model migration or backfill guessed provenance.

## Manual procedure

No live recovery was run as part of implementing this batch. First validate on an
explicitly provisioned isolated PostgreSQL/pgvector database with synthetic chunks
and a mocked provider. The database must already have the `vector(512)` schema.
The CLI refuses a different schema; it will not repair migration history.

Run from `backend`. Supply `EMBEDDING_RECOVERY_DATABASE_URL` through your normal
secure environment configuration. It must explicitly name the database you intend
to inspect; there is no fallback to `DATABASE_URL` or to a database URL in `.env`.
Do not place credentials in command arguments or copy a production URL into tests.
After selecting this target, application configuration loading still reads `.env`
for settings such as `VOYAGE_MODEL` and (only needed for execution) `VOYAGE_API_KEY`.
Process environment settings take precedence. `--help` needs no configuration.

```text
python -B scripts/recover_embeddings.py --dry-run --document-id 123 --max-documents 1 --max-chunks 128 --batch-size 32
```

Dry-run is also the default if neither mode flag is given. It uses a PostgreSQL
read-only connection, reports counts and planned input characters, and makes no
provider calls, commits or data writes. IDs, counts and configuration are output
as JSON events; content, vectors, URLs, keys and provider error bodies are omitted.
The plan is printed/flushed before any provider request. Existing Voyage progress
and retry lines can also appear during execution.

Review the plan, database selection, model and budget before intentionally opting
in to paid work. Use the same model as the deployed query embedding configuration.
Pause processing/reprocessing/deletion of the selected documents during maintenance
to avoid racing changes and duplicate paid work. Recovery skips documents outside
the existing `ready`/`failed` states and never promotes failed documents to ready.

```text
python -B scripts/recover_embeddings.py --execute --expected-model voyage-4-lite --document-id 123 --max-documents 1 --max-chunks 128 --batch-size 32
```

`--execute` explicitly authorizes provider calls and database writes for this
invocation. `--expected-model` must match the loaded model before connecting.
There are no startup hooks, worker dispatches or hidden recovery calls.

Without `--document-id`, inspection walks at most `--max-documents` documents in
ID order, including healthy and empty documents. `--after-document-id` selects
the next page. Repeat the same page until its recoverable work is complete; only
then use the reported `next_after_document_id`. A cursor advances past remaining
work if you use it early. Repeated `--document-id` selects an explicit subset.
Requested IDs not inspected (missing, before the cursor, or beyond the document
limit) are reported, not assumed healthy. Counts apply only to the selected page,
not the entire database.

Defaults: 10 documents, 256 attempted chunks per invocation, 32 chunks per batch.
Allowed bounds: documents 1–100, chunks 1–10,000, batch size 1–128. Failed requests
consume the invocation's attempted-chunk budget. The existing bounded Voyage 429
retry policy still applies within a request. Character counts are workload
estimates, not token counts or dollar caps; input length and provider billing
determine actual cost. Concurrent edits can change the preview before execution.

Each successful batch validates response count, unique input indices, dimensions,
finite float32 values and nonzero vectors before one database commit. Updates keep
document/chunk IDs, content, locations, source/asset metadata, and associations.
No parsing, Datalab, Cloudinary, chunk deletion or chunk recreation is involved.
An atomic update rechecks that each vector still needs repair, its content is
unchanged and the document is eligible. Changed/deleted/already repaired rows are
counted as conflicts. Current JSON metadata is merged at update time. Non-object
metadata aborts that batch rather than discarding metadata.

Provider/validation/database failure stops the invocation, rolls back only the
current batch, and leaves earlier commits intact. Failed chunks remain eligible.
Rerunning the same selection resumes from the remaining missing/unusable vectors.
An interruption or ambiguous commit acknowledgement can make progress counters
uncertain; rerun dry-run to establish persisted state. A request that succeeded at
the provider but was not committed may be billed again on retry. Concurrent
maintenance can also duplicate calls even though conditional writes preserve
usable vectors. The command does not claim to serialize workers.

Final verification repeats SQL counts. Existing processing status is unchanged;
a legacy `ready` row with missing vectors reports `semantic_ready=false`. Ingestion
now verifies completeness before setting ready. The legacy duplicate-task guard
directs incomplete documents to explicit recovery instead of reparsing them.
Search logs incomplete counts internally, preserves NULL exclusion and valid
partial matches, and returns an empty result safely when no usable vectors exist.
Existing RAG fallbacks/public chat responses remain available; the public document
status alone continues to describe processing, not verified embedding provenance.

Exit codes: `0` all inspected documents are semantic-ready; `1` configuration,
provider, validation or persistence failure; `2` verification found incomplete,
empty, failed/skipped documents, an empty selection or uninspected requested IDs.
A successful bounded batch can exit `2` because more chunks remain. Exit `0` does
not certify the provenance of preserved legacy vectors; inspect the unverified
generation count too. Review the dry-run and final reports after each invocation.

Provider contracts: [Voyage embeddings](https://docs.voyageai.com/reference/embeddings-api)
and [pgvector cosine index behavior](https://github.com/pgvector/pgvector#troubleshooting).

## Database-free validation

```text
python -B tests/test_embedding_recovery.py
python -B tests/test_database_safety.py
```

The recovery suite blocks real engines and networking, suppresses `.env` loading,
and uses synthetic provider credentials. PostgreSQL execution of aggregate/update
SQL and end-to-end CLI recovery still require isolated live validation. Do not
weaken the integration suite's `TEST_DATABASE_URL` protections to run that check.
