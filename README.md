# AI Document Assistant

AI Document Assistant is a full-stack web application for uploading documents, asking questions about their content, generating summaries, and searching through them using semantic search and RAG.

The project uses a Next.js frontend with a FastAPI backend, PostgreSQL, pgvector, and DeepSeek.

## Features

- Google authentication
- PDF, DOCX, XLSX, and TXT uploads
- Chat with one or multiple documents
- Semantic search with pgvector
- Retrieval-Augmented Generation (RAG)
- Streaming AI responses
- Document summaries
- Exact page questions
- Image, table, equation, and chart support
- Hybrid PDF parsing with PyPDF and Datalab
- Pinned and archived chats
- Celery + Redis background processing
- Alembic database migrations
- Automated backend tests
- User-specific document and chat access

## Tech Stack

**Frontend**
- Next.js
- React
- TypeScript

**Backend**
- FastAPI
- Python
- SQLAlchemy

**Database**
- PostgreSQL
- pgvector
- HNSW vector indexing

**AI**
- DeepSeek
- Sentence Transformers
- RAG

**Document Processing**
- PyPDF
- Datalab
- python-docx
- openpyxl

**Other**
- Celery
- Redis
- Alembic
- Pytest

## How It Works

When a document is uploaded:

```text
Upload
  ↓
Content extraction
  ↓
Chunking
  ↓
Embedding generation
  ↓
PostgreSQL + pgvector
```

When the user asks a question:

```text
Question
  ↓
Query embedding
  ↓
Vector search
  ↓
Relevant document chunks
  ↓
RAG context
  ↓
LLM response
```

The search system uses cosine similarity together with lexical matching and retrieval limits to select relevant chunks.

## PDF Processing

PDFs use a hybrid parsing approach.

Regular text pages are processed locally with PyPDF. More complex pages can be processed with Datalab to extract structured content such as:

- Images
- Charts
- Tables
- Equations
- Diagrams

Extracted images can also be linked back to their document page and displayed with relevant answers.

## Project Structure

```text
ai-document-assistant/
├── backend/
│   ├── app/
│   │   ├── database/
│   │   ├── routes/
│   │   ├── schemas/
│   │   └── services/
│   ├── migrations/
│   ├── tests/
│   └── main.py
│
├── frontend/
│   ├── public/
│   └── src/
│
└── README.md
```

## Backend Setup

```bash
cd backend
python -m venv venv
pip install -r requirements-dev.txt
python -m alembic upgrade head
uvicorn main:app --reload
```

Create a `.env` file using `.env.example` as a reference.

## Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

The frontend runs locally on:

```text
http://localhost:3000
```

The backend runs on:

```text
http://localhost:8000
```

## Background Processing

Document processing can run locally with FastAPI BackgroundTasks or through Celery and Redis.

For Celery:

```env
TASK_QUEUE=celery
```

Run the worker with:

```bash
celery -A app.worker.celery_app worker --loglevel=info
```

## Testing

Backend tests require an explicit **process environment variable**
`TEST_DATABASE_URL`. There is no fallback to `DATABASE_URL`, and a test URL in
`.env` is not automatically selected. Use a disposable local PostgreSQL server
with pgvector and a dedicated test role. No Docker setup is required by this
test harness.

The URL must use `postgresql` or `postgresql+psycopg`, include a username and a
loopback host (`localhost`, `127.0.0.1`, or `::1`), and contain no query options.
Its database name is a **base name**, not a database that will be reset: use
`ai_document_assistant_test`, for example. Names must start with a lowercase
letter, contain only lowercase letters/digits/underscores, be at most 26
characters, and contain a separate `test` segment. Development/staging/production
name segments and either application database name from the environment or
`backend/.env` are rejected. Unset `PGHOSTADDR`, `PGSERVICE`, `PGSERVICEFILE`, and
`PGOPTIONS`; they can override connection routing/settings.

For example, set `TEST_DATABASE_URL` in your shell using your local test-role
credentials and the shape
`postgresql+psycopg://test_role:<password>@127.0.0.1:5432/ai_document_assistant_test`.
Do not commit credentials or use application/production administrator credentials.

Then run from the repository root:

```bash
cd backend
python -m pytest --collect-only -q
python -m pytest -v
```

Missing or unsafe configuration fails before test-module collection. Importing
`tests/conftest.py` never contacts PostgreSQL. Collection redirects only the
pytest process's `DATABASE_URL` to a unique per-run name; it does not create,
migrate, truncate, or drop a database. The original value is restored when
pytest exits normally. Start pytest in a fresh process, before importing the app.

Actual test execution creates `<base>_run_<uuid>` in a session fixture and runs
the existing Alembic migrations there. The role needs `CREATEDB`, access to the
local `postgres` maintenance database, and permission to enable pgvector in its
new database. The base database is never modified. Tests retain their existing
commit semantics and per-test truncation/identity reset. Teardown drops only a
database successfully created by that run, without terminating connections or
using `FORCE`. If creation collides, the existing database is left untouched.
An interrupted process or busy database may leave a run database behind; inspect
it manually rather than automatically removing databases by prefix.

Independent test processes (including separate pytest workers) get separate
database names. This isolates database state only, not external AI services or
upload files. Unique databases fit the existing separate request sessions and
commits better than wrapping one fixture session in a rollback transaction.

Guard regression checks require no database and can be run directly from `backend/`:

```bash
python -B tests/test_database_safety.py
```

Separately, check application database migrations (this uses `DATABASE_URL`):

```bash
python -m alembic check
```

## Security

The backend includes authentication and ownership checks so users can only access their own chats, documents, and document assets.

Uploads are validated before processing, sensitive values are stored in environment variables, and document content is treated as untrusted input when building LLM prompts.

## Author
