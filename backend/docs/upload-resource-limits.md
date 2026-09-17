# Batch 9: upload and parser limits

The backend is authoritative. `app/services/resource_limits.py:upload_limits()`
loads positive integer settings once per process. Restart API and worker processes
after changing settings, and keep their configuration identical. Defaults are in
`.env.example`. No database migration or existing-document rewrite is required.

## Product policy and ingress

Supported extensions are PDF, DOCX, XLSX and TXT (case insensitive). The default
logical file limit is **50 MB**, using the existing binary convention: 52,428,800
bytes (50 MiB). Exactly that many bytes is allowed. PDFs allow **500 total pages**.
Authenticated `GET /documents/upload-policy` returns only supported extensions,
maximum file bytes and maximum PDF pages. Frontend guidance and picker filters
read this endpoint; extension, empty-file and size validation run before POST.
The frontend refreshes the policy before each upload. A missing policy produces a
safe retry message; the UI asks for a refresh if initial guidance cannot load.
PDF pages and all parser limits are enforced independently by the backend.

`UploadBodyLimitMiddleware` bounds actual ASGI request bytes **before they reach
the multipart parser** on POST `/documents` (including its trailing-slash form).
The default request cap is **51 MiB (53,477,376 bytes)**: 50 MiB file plus 1 MiB
multipart overhead. An excessive Content-Length is rejected before body reads;
missing or understated Content-Length cannot bypass the running byte counter.
The route accepts one file and no extra form fields. Partial multipart spools are
closed on rejection; Starlette 1.6.0 is explicitly pinned for its stream-error
cleanup behavior. The route checks actual file length, content, and copied bytes
again. Heavier preflight runs in the thread pool. Other endpoints retain their
existing request behavior.

**Production ingress must also enforce this limit before the request fully reaches
FastAPI.** Configure every applicable CDN/WAF/load balancer/reverse proxy to accept
at most 53,477,376 request bytes for uploads, with request/header/read timeouts and
appropriate traffic limits. For an existing Nginx upload location, the corresponding
directive is `client_max_body_size 51m;`. Match any custom application settings;
a proxy cap of exactly 50 MiB would reject valid files near the file boundary due
to multipart overhead. This repository does not provision that edge configuration.
Do not expose an unrestricted origin that bypasses it.

The application counter protects multipart consumption and downstream parsing and
paid work. It does not bound bytes already buffered by the transport/proxy, sockets,
slow clients, or distributed traffic. It does **not** replace CDN/WAF/DDoS controls.
Starlette's multipart `max_part_size` alone is not a file-body limit; the separate
ASGI counter is essential. See the [Starlette request documentation](https://starlette.dev/requests/).

## Processing policy

| Setting | Default | Enforcement |
| --- | ---: | --- |
| `MAX_UPLOAD_SIZE_MB` | 50 | Original file bytes at upload and parser entry |
| `MAX_MULTIPART_OVERHEAD_BYTES` | 1,048,576 | Added to file limit for entire multipart request |
| `MAX_PDF_PAGES` | 500 | Page-tree count and actual pages before classification or paid extraction |
| `DATALAB_BATCH_SIZE` | 20 | Pages allowed in one conversion batch |
| `MAX_DATALAB_PAGES_PER_DOCUMENT` | 40 | Unique advanced pages selected in one document processing attempt |
| `DATALAB_PARALLEL_BATCHES` | 2 | Concurrent advanced conversion batches |
| `MAX_DATALAB_RESPONSE_BYTES` | 33,554,432 | Streamed response bytes before JSON decoding |
| `MAX_OFFICE_ZIP_ENTRIES` | 1,000 | EOCD count plus actual central-directory scan before ZipInfo allocation |
| `MAX_OFFICE_EXPANDED_BYTES` | 209,715,200 | Sum of declared expanded entry sizes |
| `MAX_OFFICE_ENTRY_BYTES` | 33,554,432 | Each expanded ZIP entry |
| `MAX_OFFICE_COMPRESSION_RATIO` | 200 | Expanded/compressed bytes for every entry |
| `MAX_OFFICE_XML_NODES` | 2,000,000 | Nodes across streamed Office XML |
| `MAX_OFFICE_XML_DEPTH` | 64 | Office XML nesting and advanced-output traversal depth |
| `MAX_XLSX_WORKSHEETS` | 20 | Workbook sheet entries and worksheet XML files |
| `MAX_XLSX_ROWS_PER_SHEET` | 20,000 | Declared dimensions, coordinates and actual rows |
| `MAX_XLSX_TOTAL_ROWS` | 50,000 | Total row extent across worksheets |
| `MAX_XLSX_COLUMNS` | 256 | Maximum column coordinate per worksheet |
| `MAX_XLSX_CELLS` | 500,000 | Actual cells and sum of rectangular worksheet extents |
| `MAX_DOCX_PARAGRAPHS` | 20,000 | Paragraph nodes across Word XML parts |
| `MAX_DOCX_TABLES` | 1,000 | Table nodes across Word XML parts |
| `MAX_EXTRACTED_CHARACTERS` | 2,000,000 | Extracted text, plus a separate generated-chunk text total |
| `MAX_EXTRACTED_TEXT_BYTES` | 8,388,608 | Corresponding UTF-8 byte totals |
| `MAX_EXTRACTED_BLOCKS` | 10,000 | Extracted blocks before embedding |
| `MAX_DOCUMENT_CHUNKS` | 10,000 | Incremental chunk creation, before appending an excessive chunk |
| `MAX_PDF_PAGE_CONTENT_BYTES` | 8,388,608 | Decoded page streams before text extraction |
| `MAX_PDF_CONTENT_BYTES` | 67,108,864 | Decoded page-stream total before text extraction |

The historical `MAX_DATALAB_PAGES` setting remains a batch-size alias when
`DATALAB_BATCH_SIZE` is absent. It is not the total-page allowance.
The old `MAX_DATALAB_RATIO` warning does not enforce admission and is superseded
by the explicit total-page setting.

Complex PDF pages are selected in ascending page order. Only the first 40 are
eligible for advanced processing. Later complex pages use standard extraction
when they contain nonempty extractable text. If any overflow page needs OCR and
has no usable text, the whole attempt fails with a split-file message **before any
Datalab request**. The low-level conversion entry requires the same finite page
admission object across batches; oversized, repeated or unselected ranges are
rejected before submission. Known failures stop further batch admission; already
in-flight remote calls cannot be cancelled or refunded. Returned content and
filename-based image fallback text are checked before image uploads and embeddings.

This is a per-attempt page-selection limit, not durable billing accounting.
Existing bounded transport retries or a Batch 6 retry after a transient failure
can resubmit admitted pages. Once chunks are checkpointed, retries validate that
checkpoint and resume missing embeddings without repeating extraction. No pages
outside the selected allowance are submitted by an attempt.

DOCX/XLSX preflight examines ZIP metadata before decompression, then streams XML
without extracting archive paths. It rejects duplicate names, traversal, absolute
or drive paths, backslashes, nulls, symlinks, encryption, DTDs and external entities.
The policy intentionally rejects ZIP64/special central-directory layouts and
malformed packages; 50 MiB normal Office files do not require these layouts.
Word extraction retains its existing paragraph behavior; it adds no table feature.
Excel keeps formula and cached-value views in read-only mode, validates actual
coordinates even when dimensions lie, and resets dimensions before iteration.
Sparse sheets are charged for their rectangular extent to bound empty-cell work.
Styled/empty rows, chart sheets and auxiliary XML can therefore consume limits.

TXT uses incremental UTF-8 decoding, including BOM support, and rejects invalid
tails or nulls. Multibyte characters spanning sample/read boundaries are valid.
Text chunks retain their existing 300-word / 50-word-overlap behavior. Overlap
counts toward generated-chunk text totals, so a document can exceed that budget
before reaching the raw-text limit. Budgets reject the document instead of silently
truncating it. Retained checkpoints are checked with aggregate count/character/byte
queries and original-file preflight before paid embeddings.

## Errors, cleanup and earlier batches

Resource failures use code-selected safe messages (400 for invalid files/forms,
413 for resource limits). Parser/provider details never become these public
messages. The frontend shows resource and quota messages; unknown errors or edge
HTML get a safe fallback. Processing failures use Batch 4's safe diagnostics and
are permanent under Batch 6, including when a resource error wraps a timeout.
Real transient provider/network failures keep Batch 6's existing bounded retries.

Batch 8 remains unchanged: 5 upload starts/hour/user, 100 retained documents/user,
1 GiB tracked original bytes/user, and one active processing job/user. Invalid
upload attempts can still consume a start in the rate window. Preflight rejection
creates no document row or tracked original, releases the quota lock, closes
multipart files, and never dispatches work. A write-time rejection removes the
partial original. Processing rejection releases the processing permit and does not
create chunks or invoke embeddings beyond the content budget.

An accepted document that subsequently fails processing remains a visible failed
row with its original file, consistent with existing retry/delete behavior. It
continues to count toward retained-document/storage quotas until the user deletes
it. This is intentional accounting, not an orphaned reservation. A later rejection
cannot undo provider work completed earlier in the attempt.

## Validation and practical limits

Run the focused database-free suites with the backend virtual environment:

```text
python -B tests/test_upload_resource_limits.py
python -B tests/test_resource_admission.py
python -B tests/test_document_processing_reliability.py
python -B tests/test_public_generation_errors.py
python -B tests/test_database_safety.py
```

The fixtures use tiny PDFs/ZIPs/workbooks and mocked providers, sessions and
admission. The reliability harness stubs the new checkpoint-resource boundary
because its paths and storage are synthetic; Batch 9 directly tests that boundary.
The existing live upload test expectations also reflect the safe error-code body;
do not run those without Batch 1's explicitly isolated test-database setup.

Frontend checks, using installed dependencies only:

```text
node --test tests/upload-policy.test.mjs
node node_modules/typescript/bin/tsc --noEmit --incremental false
npm run build
npm run lint
```

ESLint's pre-Batch-9 baseline is 4 errors / 20 warnings; this batch must not add
findings. No production database/Redis, migrations, paid providers, giant fixtures
or load tests are needed for these checks.

These limits bound document scale and downstream work, not hard process RSS or
CPU time. Python/native parser allocations may occur before a decoded-size check;
in particular pypdf must decode page streams before their size can be measured.
See [pypdf's extraction memory guidance](https://github.com/py-pdf/pypdf/blob/main/docs/user/extract-text.md).
Use deployment worker memory/CPU/time limits alongside ingress controls. Encrypted
PDFs and some unusual but legitimate complex, highly compressed, sparse or heavily
formatted documents may require splitting/re-exporting. Standard fallback does
not guarantee OCR-quality transcription for every overflow page.
