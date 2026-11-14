# AI Document Assistant

Upload your own documents and ask questions about them. AI Document Assistant reads PDFs, Word files, spreadsheets, and text files, finds the passages that answer your question, and writes an answer from them, streamed as it's generated. It can also write saved summaries and page-by-page walkthroughs.

**Live: [ai.hishamjouda.com](https://ai.hishamjouda.com/)**

> The live site is the real application, not a sandbox. Signing in with Google creates a real account, and the documents you upload are stored on the server, along with your chats and summaries. To read and answer from them, document content is sent to third-party AI services: Datalab (for complex PDF pages), Voyage AI (for embeddings), and DeepSeek (for answers and summaries). Don't upload anything you aren't comfortable sharing with those services.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/landing-dark.webp">
  <img src="docs/screenshots/landing-light.webp" alt="AI Document Assistant landing page: “Your documents, Finally make sense.”" width="100%">
</picture>

## What it does

**Documents**

- Upload PDF, DOCX, XLSX, and TXT files, with progress tracking, retries, and limits on size and page count
- Complex PDF pages keep their tables, figures, equations, and scanned text, and extracted images can appear beside answers

**Conversations**

- Chat with one or more documents at once, with streaming answers and follow-up context
- Ask in English, Arabic, or another language; the answer comes back in the language of the question
- Ask about an exact page, such as "what does page 12 say about penalties?"
- Optionally let the assistant add general knowledge when the documents don't cover something
- Pin, archive, rename, and delete chats

**Answers and summaries**

- Tables and equations rendered with Markdown and KaTeX; page references in answers shown as compact chips
- Versioned summaries and page-by-page walkthroughs, including page ranges, with a follow-up conversation beside each summary

**Accounts**

- Google sign-in, with every document, chat, and summary tied to its owner

<table>
  <tr>
    <td width="66%">
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/chat-dark.webp">
        <img src="docs/screenshots/chat-light.webp" alt="A chat answering when a supplier agreement can be terminated, with a table, a formula, and page references">
      </picture>
    </td>
    <td width="34%">
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/chat-mobile-dark.webp">
        <img src="docs/screenshots/chat-mobile-light.webp" alt="The same answer on a phone">
      </picture>
    </td>
  </tr>
</table>

<sub>Screenshots of the app with a sample document.</sub>

## How it works

A language model only knows what it learned in training and what you put in front of it. It can't read a long PDF in one go, and asking it about a document from memory invites made-up answers. **Retrieval-augmented generation (RAG)** splits the job in two: first find the passages most likely to answer the question, then give only those passages to the model and ask it to answer from them.

**From upload to searchable document**

1. **Extract.** Each PDF page is classified. Plain text pages are read locally with pypdf; pages with tables, figures, equations, or scans go to Datalab, within a per-document page budget (40 pages by default). Word files contribute their paragraphs, spreadsheets their sheets.
2. **Chunk.** Text is split into 300-word chunks with a 50-word overlap. Structured PDF elements keep their own type and page number. The chunks are saved as a checkpoint before the next step.
3. **Embed.** Each chunk becomes a 512-dimensional Voyage AI embedding, a list of numbers that captures its meaning, stored in PostgreSQL with pgvector and an HNSW index.

**From question to answer**

1. **Retrieve.** The question is embedded the same way, and the closest chunks are found among the user's ready documents attached to the chat. Candidates get a small bonus for matching keywords, then pass a confidence threshold and per-document limits. Chunks from neighbouring pages can be added so an answer that crosses a page break stays intact. Questions about an exact page read that page directly.
2. **Generate.** DeepSeek receives the selected passages, marked as untrusted document data, with a short chat history, and the answer streams back to the browser as newline-delimited JSON.

## How it's built

| Area                | Stack                                                                 |
| ------------------- | --------------------------------------------------------------------- |
| Frontend            | Next.js 16, React 19, TypeScript, Tailwind CSS, react-markdown, KaTeX |
| Backend             | Python 3.11, FastAPI, SQLAlchemy 2, Alembic                           |
| Database            | PostgreSQL with pgvector (HNSW, cosine distance)                      |
| AI services         | DeepSeek for answers and summaries, Voyage AI for embeddings          |
| Document processing | pypdf, Datalab, python-docx, openpyxl                                 |
| Background work     | FastAPI background tasks, or Celery with Redis                        |
| Storage             | A local persistent volume, or Cloudinary                              |
| Quality             | pytest, Node test runner, TypeScript, ESLint, GitHub Actions, Docker  |

A few decisions worth knowing about:

- **Pay for extraction only where it helps.** Classifying pages first means most pages are read locally, and only the complex ones use the paid conversion service.
- **Never repeat paid work.** Chunks are checkpointed before embedding, so a failed embedding step resumes there instead of converting the PDF again. A recovery scanner retries work that stopped without finishing.
- **Documents are untrusted input.** Document text reaches the model inside clearly marked boundaries, with instructions to treat it as data and never as commands.
- **The backend owns every secret.** The browser talks only to the authenticated API; provider keys and storage paths never reach it, and extracted images are served through authenticated routes.
- **Ownership on every query.** Documents, chats, and summaries are always looked up together with the signed-in user. Sign-in uses Google OAuth with a state check, and the session is a signed token in an HttpOnly cookie, marked Secure in production. Sign-in and per-user requests are rate limited, and uploads are validated and limited by quota.

## Repository structure

```
frontend/              Next.js app: pages, chat and document components, Node tests
backend/
  main.py              FastAPI application
  app/routes/          HTTP routes: auth, documents, chats, summaries, assets
  app/services/        Processing, retrieval, generation, limits, storage
  app/database/        SQLAlchemy models and database setup
  app/worker.py        Celery worker
  migrations/          Alembic migrations
  tests/               Backend tests, including PostgreSQL integration suites
  docs/                Backend notes: self-hosting, reliability, sessions, CI
ops/deploy/            Pull-based deployment and rollback tooling
load-tests/            k6 load-test scenarios
.github/workflows/     CI
```

## Running it locally

### Requirements

- Python 3.11 and Node.js 22, the versions CI uses
- PostgreSQL with the pgvector extension (CI uses PostgreSQL 17 with pgvector 0.8.1), in a database created only for this app. The role you connect with must be able to run `CREATE EXTENSION vector`, which the first migration does
- Redis: rate limits have no in-memory fallback, so it's needed in every mode, and it's also the queue when Celery is on
- Your own Google OAuth client, and DeepSeek, Voyage AI, and Datalab API keys
- Cloudinary credentials only if you don't use local storage

### Backend

```bash
git clone https://github.com/HishamFekri/ai-document-assistant.git
cd ai-document-assistant/backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Fill in `backend/.env`. The file documents every setting; these are the ones you need first (placeholders, not real values):

```env
ENVIRONMENT=development
FRONTEND_URL=http://localhost:3000
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/ai_document_assistant

GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=replace-me
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback
JWT_SECRET_KEY=generate-a-long-random-secret

DEEPSEEK_API_KEY=replace-me
DATALAB_API_KEY=replace-me
VOYAGE_API_KEY=replace-me

STORAGE_BACKEND=local              # or leave it unset and set CLOUDINARY_URL
RESOURCE_REDIS_URL=redis://127.0.0.1:6379/2
```

Register `http://localhost:8000/auth/google/callback` as a redirect URI in your Google OAuth client. Then, from `backend/` (the `.env` file is read from the working directory) and against your development database:

```bash
python -m alembic upgrade head
uvicorn main:app --reload
```

The API runs at <http://localhost:8000>, with interactive documentation at `/docs`.

To process documents on a Celery worker instead of in the API process, set `TASK_QUEUE=celery` (with `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND`), then run from `backend/`, with the same environment:

```bash
celery -A app.worker.celery_app worker --loglevel=info
```

### Frontend

In a second terminal:

```bash
cd ai-document-assistant/frontend
npm ci
```

Create `frontend/.env.local`:

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
```

Run `npm run dev` and open <http://localhost:3000>. Use `localhost` consistently for both services and the Google callback, or the session cookie won't be sent. `NEXT_PUBLIC_*` values are compiled into the browser bundle, so they must never hold secrets.

## Tests and checks

Backend database tests need a **dedicated, disposable PostgreSQL database**. Set `TEST_DATABASE_URL` as an environment variable in your shell; it isn't read from `.env`, and there is no fallback to `DATABASE_URL`. The harness refuses an unsafe URL before collecting any test:

- a loopback host (`localhost`, `127.0.0.1` or `::1`), a username, and no query options;
- a database **base name** containing a separate `test` segment, such as `ai_document_assistant_test`. Each run creates its own `<base>_run_<uuid>` database, migrates it, and drops only that database afterwards; the base database is never touched;
- a role with `CREATEDB` and permission to enable pgvector in the databases it creates.

Never point it at real data.

```bash
cd backend
python -B tests/test_database_safety.py   # checks the safeguards; needs no database
python -m pytest -v                       # needs TEST_DATABASE_URL
```

Frontend:

```bash
cd frontend
node --test tests/*.test.mjs
npx --no-install tsc --noEmit
npm run lint -- --max-warnings 19
npm run build
```

GitHub Actions runs the backend tests (including PostgreSQL and pgvector integration suites with simulated AI providers), the frontend checks, dependency audits for Python and npm, a workflow lint, and both Docker builds, checking that each image runs as a non-root user. More detail is in [backend/docs/observability-ci.md](backend/docs/observability-ci.md).

## Deployment

The live app is self-hosted with Docker: the frontend and backend run as separate containers, with PostgreSQL and pgvector, Redis, and uploads on a persistent volume that must be mounted before the backend starts. The server checks GitHub for new commits on `main` itself and deploys only those whose CI passed; GitHub has no access to the server. The backend image runs Alembic migrations when it starts.

The repository has Dockerfiles for both services but no Compose file: running it yourself means providing PostgreSQL with pgvector, Redis, an HTTPS reverse proxy, persistent storage, and backups. See [backend/docs/self-hosting.md](backend/docs/self-hosting.md), [frontend/docs/self-hosting.md](frontend/docs/self-hosting.md), and [ops/deploy/README.md](ops/deploy/README.md). The API reports liveness at `/health` and readiness at `/ready`. Back up the database and the uploaded files together, since records refer to files stored outside PostgreSQL.

## Current limitations

- Page references in answers come from the model's text and aren't yet checked against the passages it was given.
- Keyword matching only re-ranks the vector candidates, so an exact term outside them can be missed.
- Complex PDF conversion has a per-document page budget, and Word files contribute their paragraphs only, without tables or images.
- In the in-process mode, a job interrupted by a restart is retried by the recovery scanner (for jobs that went stale within the last day) rather than continuing where it was. In Celery mode, a worker that dies mid-task isn't redelivered automatically.
- `.env.example` lists `EMBEDDING_BATCH_SIZE`, which the code doesn't read; the setting it reads is `VOYAGE_BATCH_SIZE`.

## API overview

FastAPI generates interactive documentation at `/docs` and `/redoc`. Every endpoint except sign-in, `/health`, and `/ready` requires authentication.

| Area              | Main operations                                                                |
| ----------------- | ------------------------------------------------------------------------------ |
| Auth              | Google sign-in, current user, logout                                           |
| Documents         | Upload policy, upload, list, status, retry, delete                             |
| Chats             | Create, list, rename, pin, archive, delete; attach and detach documents        |
| Answers           | Ask, ask with streaming (`POST /chats/{id}/ask/stream`, NDJSON events), search |
| Summaries         | Generate (with streaming), list versions, select, cancel, delete               |
| Summary assistant | Read, send, and clear the follow-up conversation for a document                |
| Assets            | Extracted images and their files, through authenticated routes                 |
| Operations        | `GET /health`, `GET /ready`                                                    |

Lists use keyset pagination, with the next cursor in the `X-Next-Cursor` response header.

## Further documentation

| Document                                                                                           | Covers                                      |
| -------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| [backend/docs/authentication-sessions.md](backend/docs/authentication-sessions.md)                 | OAuth flow, cookies, deployment layouts     |
| [backend/docs/upload-resource-limits.md](backend/docs/upload-resource-limits.md)                   | Upload and extraction limits                |
| [backend/docs/resource-admission.md](backend/docs/resource-admission.md)                           | Rate limits and concurrency permits         |
| [backend/docs/document-processing-reliability.md](backend/docs/document-processing-reliability.md) | Claims, checkpoints, retries, image uploads |
| [backend/docs/embedding-recovery.md](backend/docs/embedding-recovery.md)                           | Repairing incomplete embeddings             |
| [backend/docs/rag-correctness-validation.md](backend/docs/rag-correctness-validation.md)           | Page conventions, prompt boundaries, tuning |
| [backend/docs/summary-generation-concurrency.md](backend/docs/summary-generation-concurrency.md)   | Summary ownership and versioning            |
| [backend/docs/database-scalability.md](backend/docs/database-scalability.md)                       | Pagination, indexes, connection pool        |
| [backend/docs/observability-ci.md](backend/docs/observability-ci.md)                               | Logging, health checks, CI design           |
| [backend/docs/rollback-runbook.md](backend/docs/rollback-runbook.md)                               | Rolling back across migrations              |
| [frontend/docs/frontend-reliability.md](frontend/docs/frontend-reliability.md)                     | Request ownership and stream handling       |

## What's next

- **Verified citations** that link each page reference to the exact passage it came from
- **Richer Word support**, extracting tables and images from DOCX files
- **Full-text search** beside the vector search, so exact terms are found even when they aren't among the closest matches

## License

No license file is included. Without one, no permission to use, modify, or redistribute the code is granted.
