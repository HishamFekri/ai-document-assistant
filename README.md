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

Run the backend tests:

```bash
python -m pytest -v
```

Check database migrations:

```bash
python -m alembic check
```

## Security

The backend includes authentication and ownership checks so users can only access their own chats, documents, and document assets.

Uploads are validated before processing, sensitive values are stored in environment variables, and document content is treated as untrusted input when building LLM prompts.

## Author
