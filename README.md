AI Document Assistant

AI Document Assistant is a full-stack application for working with your own documents. Upload a PDF, Word file, spreadsheet, or text file, attach documents to a chat, and ask questions in English or Arabic. The assistant retrieves relevant content before answering, streams its responses, and can generate saved summaries or detailed page-by-page walkthroughs.

The project focuses on three practical problems: extracting useful information from complex PDFs without sending every page to a paid conversion service, retrieving the right context across multiple documents, and keeping long-running processing jobs recoverable when services fail or restart.

Features

Document chat: Ask questions across one or more attached documents, with streaming responses, follow-up context, and optional general knowledge.

Document search: PostgreSQL and pgvector retrieve relevant chunks; lexical matching helps re-rank vector candidates. Questions about an exact page use page-based retrieval.

PDF extraction: Simple pages use pypdf. Complex pages can use Datalab for tables, figures, equations, and scanned content, within a configurable conversion budget.

Visual content: Extracted PDF images and charts can appear alongside answers. Tables and equations are rendered with Markdown and KaTeX.

Summaries: Generate versioned summaries or detailed page-by-page walkthroughs, including specific page ranges.

Document management: Upload PDF, DOCX, XLSX, and TXT files; track processing progress, retry failures, and organize chats with pinning and archiving.

Background jobs: Run processing through FastAPI BackgroundTasks or Celery with Redis. Checkpoints reduce repeated extraction and embedding work.

User isolation: Google sign-in, authenticated document access, upload validation, rate limits, and ownership checks.

PDF support is richer than Office-file support: DOCX extraction reads paragraphs, not embedded images or tables. Answer text may mention sources, but those references are not currently verified citations. See Current limitations.

Technology stack

Area

Technology

Frontend

Next.js 16, React 19, TypeScript, Tailwind CSS

Backend

Python 3.11, FastAPI, SQLAlchemy 2, Alembic

Database

PostgreSQL, pgvector, HNSW cosine index

AI

DeepSeek for generation, Voyage AI voyage-4-lite for 512-dimensional embeddings

Document processing

pypdf, Datalab, python-docx, openpyxl

Background work

FastAPI BackgroundTasks or Celery, Redis

Storage

Cloudinary or a local persistent volume

Infrastructure and quality

Docker, pytest, GitHub Actions

Dependency versions are maintained in backend/requirements.txt and frontend/package.json.

Architecture

The backend owns database access, provider calls, and document storage. The browser uses authenticated API routes and does not receive private provider credentials or direct storage paths. Redis handles rate limiting in both processing modes and also acts as the task broker when Celery is enabled.

From upload to searchable document

Text is split into 300-word chunks with a 50-word overlap; structured PDF elements retain their own content types and page locations. Processing saves the extracted chunks before embedding them. If embedding generation fails, a retry can resume from that checkpoint instead of repeating successful paid extraction.

For PDFs, the extractor classifies pages individually. Straightforward text pages use pypdf, while complex layouts can be converted with Datalab. The default advanced-extraction budget is 40 pages per document. Pages beyond the cap may use a text fallback, but pages with no extractable text cannot be silently skipped.

How answers are generated

Search is limited to the user's ready documents attached to the chat. Vector candidates are re-ranked with a small keyword-match bonus, confidence thresholds, and per-document limits. Nearby-page chunks can be included to preserve context across page boundaries. DeepSeek receives the selected content as untrusted document data, along with a short chat history, and the answer is streamed as newline-delimited JSON.

This is vector retrieval with lexical re-ranking, not a separate BM25 or full-text search index. For the exact retrieval rules, PDF classification thresholds, streaming events, database model, and security controls, see the technical reference.

Getting started

Requirements

Python 3.11 and Node.js 22

PostgreSQL with pgvector

Redis, including for local development

Google OAuth client, DeepSeek, Voyage AI, and Datalab API credentials

Cloudinary credentials, unless using local storage

Backend

git clone https://github.com/HishamFekri/ai-document-assistant.git
cd ai-document-assistant/backend
python -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env

Configure backend/.env with your PostgreSQL URL, Google OAuth settings, JWT_SECRET_KEY, and the DeepSeek, Datalab, and Voyage keys. VOYAGE_API_KEY is required but is currently absent from .env.example. Set STORAGE_BACKEND=local if you do not want Cloudinary. Register http://localhost:8000/auth/google/callback with your Google OAuth client.

With a dedicated application database created and pgvector available:

python -m alembic upgrade head
uvicorn main:app --reload

The backend runs at http://localhost:8000, with OpenAPI documentation at /docs. Run migrations only against the intended development database.

Frontend

In a second terminal:

cd ai-document-assistant/frontend
npm ci

Create frontend/.env.local:

NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_GOOGLE_CLIENT_ID=<your-google-oauth-client-id>

Then run npm run dev and open http://localhost:3000. Keep localhost consistent across both services and the Google callback, or the host-only session cookie may not work.

For optional Celery processing, set TASK_QUEUE=celery and run celery -A app.worker.celery_app worker --loglevel=info from backend/ with the same environment as the API.

Docker and self-hosting

The repository includes backend and frontend Dockerfiles, but does not include a Compose file. A deployment operator must provide PostgreSQL/pgvector, Redis, HTTPS reverse proxy, persistent storage, and backups.

For local file storage, configure STORAGE_BACKEND=local and mount the uploads volume. The backend expects a .storage-sentinel file on the mounted volume to protect against accidentally writing uploads to an unmounted disk. The backend image runs Alembic migrations at startup, so review schema changes before updating containers.

Backend self-hosting and persistent storage

Frontend Docker image and reverse proxy

Deployment and rollback tooling

The API exposes /health for liveness and /ready for dependency readiness. Back up the database and uploaded files together, since database records reference assets stored outside PostgreSQL.

API overview

Area

Main operations

Authentication

Google sign-in, current user, logout

Documents

Upload, list, status, retry, delete, access extracted assets

Chats

Create, rename, attach documents, pin, archive, delete

Questions

Search, ask, stream answers through POST /chats/{id}/ask/stream

Summaries

Generate, stream, list versions, select, cancel, delete

Operations

GET /health, GET /ready

The FastAPI OpenAPI endpoints at /docs and /redoc provide the complete request and response schemas. Streaming answers use NDJSON events, including token, done, and error.

Testing

Backend integration tests use a dedicated, disposable PostgreSQL database. Before running pytest, explicitly set TEST_DATABASE_URL in the shell with a loopback host and a dedicated test role. Never point it at development or production data. The test harness validates naming and connection restrictions, creates a unique per-run database, and only drops a database that run created.

cd backend
python -B tests/test_database_safety.py
python -m pytest --collect-only -q
python -m pytest -v

The last two commands require a safely configured TEST_DATABASE_URL; the first does not. Read the full test database safeguards before running database tests.

Frontend checks:

cd frontend
node --test tests/*.test.mjs
npx --no-install tsc --noEmit
npm run lint -- --max-warnings 19
npm run build

GitHub Actions checks backend tests and migrations, frontend quality, dependencies, and container builds. See CI and observability notes.

Current limitations

Source mentions generated in answer prose are not verified citations to retrieved chunks.

Complex PDF conversion has a page budget; DOCX tables and images are not extracted.

Lexical matching only re-ranks vector candidates, so an exact term outside those candidates can be missed.

The in-process task mode cannot preserve an active job through a restart, although a recovery scanner can retry stale work. Celery has different worker-crash limits.

The example backend environment file needs corrections for the Voyage key, batch setting name, and DeepSeek default.

These are current implementation limits, not planned or completed features.

License

No license file is currently included in the repository. No permission to use, modify, or redistribute the code is granted by default.
