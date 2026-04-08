# Therapist SOAP assistant (FastAPI + MongoDB + Gemini)

Python service for **therapist-only** Q&A grounded in **that therapist’s** SOAP notes.

## Setup

1. Python 3.10+
2. Copy `.env.example` to `.env` and set `MONGODB_URI`, `JWT_SECRET` (same as Express HS256), and `GEMINI_API_KEY` or `GOOGLE_API_KEY`.
3. Install and run:

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- API docs: `http://127.0.0.1:8000/docs`
- Health: `GET /health`
- Chat: `POST /api/therapist-chat` or `POST /api/v1/chat` with header `Authorization: Bearer <JWT>` and JSON `{"message":"..."}`.
- Full **API list, request/response flow (RAG), and tech stack**: [docs/API_AND_ARCHITECTURE.md](docs/API_AND_ARCHITECTURE.md)

### Testing without JWT (local only)

In `.env` set `SKIP_THERAPIST_JWT=true` and restart; optional `DEV_THERAPIST_ID` labels logs only. **Turn off before production.**

### Retrieval scope

- Default **`RAG_THERAPIST_SCOPED=false`**: search **all** SOAP chunks by semantic similarity to the question (e.g. OCD-related chunks for an OCD question).
- Set **`RAG_THERAPIST_SCOPED=true`** to limit search to the signed-in therapist’s notes only (stricter privacy).

## Indexing SOAP notes

Point `SOAP_SOURCE_COLLECTION` at your collection of full SOAP documents (see [SCHEMA.md](SCHEMA.md)), then:

```bash
python -m scripts.backfill_chunks
```

Use `--limit 10` for a small test run.

## Frontend demo

With the API running, open **`/chat`** on the same server (e.g. `http://127.0.0.1:8001/chat`) or [frontend/therapist-chat.html](frontend/therapist-chat.html); leave API base empty when served from the API. JWT optional if `SKIP_THERAPIST_JWT=true`.

## Logging

Chat requests log only a short therapist id prefix and chunk count—not the message body.
