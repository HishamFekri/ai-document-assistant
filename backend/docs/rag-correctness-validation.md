# Batch 12: retrieval and provider verification

No migration, historical rewrite, embedding-model change, or HNSW setting change
is included. Offline tests do not establish live provider numbering or ANN recall.

## Content and page conventions

New mathematical blocks/chunks use `equation`. Generic, explicit math, companion,
and representative retrieval accept `formula` and `equation`. Generic retrieval
also includes already-indexed code. Images retain the existing separate semantic
description path. Historical rows are not rewritten.

Normalized `metadata.page` and source `Page N` labels are one-based original
document pages. PyPDF enumerates original pages starting at 1. The outbound
Datalab `page_range` subtracts 1 from selected original pages.

Current Marker JSON block IDs use `/page/{page_id}/{block_type}/{block_id}` where
`page_id` is the zero-based original-document page. A live selected-page probe
for pages `0,26,29` returned those same IDs (not batch positions), matching the
provider's renderer and document-builder contract. The deterministic adapter
therefore maps a strict Marker block ID to `page_id + 1`, provided that page is
in the submitted batch. Nested blocks inherit a verified parent reference only
when their own ID is absent. Malformed or conflicting IDs remain unassigned.

Bare numeric page fields still require an explicit `DATALAB_PAGE_NUMBERING`
contract; they are not guessed by default. Missing metadata on a single-page
request can safely use that requested page. Raw values are retained as
`provider_page`, with `provider_block_id`, `page_numbering`,
`page_mapping_source`, and `page_mapping_status` provenance. Unresolved provider
labels cannot override that status. Non-PDF unpaginated transcription retains
virtual page 1 only when compatible with the selected scope.

Explicit legacy numeric-field adapter contracts:

| Setting | Reported value for original page 5 in selection `[2, 5, 9]` |
| --- | --- |
| `original_one_based` | 5 |
| `original_zero_based` | 4 |
| `batch_zero_based` | 1 |
| `batch_one_based` | 2 |

A sanitized provider-shape fixture covers first, middle, and last selected pages,
nested text, and an image block. Keep genuinely ambiguous formats unknown rather
than extending the Marker ID contract to unrelated numeric fields.

Older rows with `page_mapping_status = 'unknown'` are not rewritten by this
adapter. Reprocess their original documents through the current extraction
pipeline after a scoped backup and representative staging check.

## Prompt and authorization boundaries

Summary, page transcription, and transcription fallback now state that document
text, filenames, captions, and assets are untrusted data, including fake system
messages and delimiter-closing text. Source fields have explicit delimiters.
User preferences remain application inputs. Main chat keeps its existing rule.
The summary assistant and generation-request resolver consume user instructions,
not raw document context. Ownership, attached-document IDs, and allowed asset IDs
remain server-side decisions. Prompt tests inspect messages without calling a model;
they do not claim complete prompt-injection resistance.

## Vector baseline and isolated tests

The repository uses 512-dimensional vectors, cosine `<=>`, and global HNSW
`vector_cosine_ops`, with no repository override of HNSW build/search settings.
Search defaults: 8 anchors, 5x candidate multiplier (40 candidates), similarity
0.45, score-drop window 0.20, at most 3 anchors per location and 6 per document
in multi-document scope. RAG callers can request different limits. Companions
can add up to 10 chunks after anchor selection. Measure candidate retrieval
separately from lexical reranking, deduplication, diversification, and companions.

`vector_candidates(..., exact=True)` uses distance-plus-zero ordering to prevent
the bare distance ordering required for the HNSW index. It uses the same document,
compatible-type, nonblank-content and usable-vector filters as normal retrieval.
Confirm its actual plan on the deployed PostgreSQL version. No session settings
are changed by the application helper. Short candidate sets log returned versus
expected counts separately from Batch 5 embedding-incompleteness warnings.

The isolated `tests/test_vector_retrieval_postgres.py` fixture has 96 synthetic
vectors, multiple document owners/types, exact expected IDs, cosine queries,
candidate counts, top-8/top-40 overlap/recall, latency, versions and JSON plans.
It reports whether each ANN query actually used HNSW. A planner-selected exact
scan must not be described as measured ANN recall. It is a compact correctness
fixture, not a load test or representative quality benchmark.

It was **not executed**: no `TEST_DATABASE_URL` was configured. The existing
Batch 1 pytest fixture creates an isolated local run database and applies
migrations. Running it requires separate authorization for that lifecycle; do
not run it under the Batch 12 no-migrations restriction. Once authorized, use:

```powershell
# Set TEST_DATABASE_URL to a dedicated local test role/database base, following
# tests/conftest.py validation. Never use DATABASE_URL or a production target.
venv/Scripts/python.exe -B -m pytest -s tests/test_vector_retrieval_postgres.py
```

## Representative staging comparison procedure

Use a separately approved staging snapshot and read-only transaction. No schema
changes or embedding generation are necessary: supply previously captured query
vectors from the unchanged model. Keep vectors and source text out of public logs.
Record server version, pgvector extension version, index definition/options,
`hnsw.ef_search`, planner settings, and query plans before changing anything.

Choose existing documents with roughly 10–50, 100–500, and 1,000–10,000 chunks,
including sparse math/code, mixed images/text, incomplete vectors, and narrow
multi-document scopes. Select scopes representing roughly 0.1%, 1%, 10%, and
50% of the indexed population where the snapshot permits. Use a small fixed
query set with human-reviewed expected evidence, including math and exact-page
questions. This is bounded correctness sampling, not a load test.

For each vector and fixed candidate limit, compare:

1. Exact cosine retrieval with the intended document/type filters.
2. Unfiltered HNSW and its unfiltered exact baseline, over the approved snapshot.
3. HNSW with the intended document/type filters and the filtered exact baseline.

Use 8 and 40 as baseline top-k/candidate limits, plus actual caller-specific
limits. Record ordered IDs, eligible-row count, returned candidate count,
`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`, and elapsed time. Capture one initial
and several repeated measurements with identical queries/settings and report
median/range; do not compare a cold exact query only against warm ANN queries.

`compare_candidate_ids` reports overlap and recall@k as
`|ANN_top_k intersect exact_top_k| / |exact_top_k|`. Empty exact sets have undefined
recall (`null`), not a false success. Handle equal-distance ties consistently.
Compare unfiltered results only to an unfiltered baseline; filtering a global
top-k afterward is a separate experiment and is not equivalent to scoped search.
Check document/type scope and ownership before considering recall. Then compare
final RAG anchors, lexical ordering, companions, and source pages.

Agree on an acceptance threshold before measurement (for example recall@8 >=0.95
across the sample, no missing critical mathematical evidence, zero scope leaks,
and latency within the service budget). Report failures and per-query results;
an aggregate average must not hide a failed narrow scope.

## HNSW tuning and fallback decision

[pgvector's official documentation](https://github.com/pgvector/pgvector#filtering)
explains that approximate-index filtering can return fewer rows. Iterative scans
require pgvector **0.8.0 or later**; verify the deployed extension and PostgreSQL
support matrix before testing transaction-local `hnsw.iterative_scan = strict_order`.
Measure recall/latency and scan limits before proposing a configuration change.
Iterative scans were not enabled by Batch 12.

A small-scope exact fallback was evaluated and deferred: count shortfall is a
useful signal, but a full candidate count can still have poor recall, and a safe
exact-search workload cap requires staging latency evidence. Candidate-shortage
logging makes this failure observable without introducing an unmeasured automatic
query. Establish a bounded scope threshold using the procedure above before
adding a fallback. ANN correctness remains unverified until these measurements.

Identical query embeddings are reused only within one `prepare_answer_context`
call. Different contextual/original queries retain distinct embeddings. Empty or
unusable-vector scopes do not trigger paid embedding or automatic recovery.
