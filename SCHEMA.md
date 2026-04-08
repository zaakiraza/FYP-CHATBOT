# MongoDB schema contract (therapist chatbot)

SOAP chunks are stored in MongoDB; retrieval is **semantic** (query embedding vs chunk embeddings). By default (`RAG_THERAPIST_SCOPED=false`) the search is over **all** chunks in `soap_chunks` so answers follow the **topic of the question** (e.g. OCD-related notes for an OCD question), not the identity of who asked. Set `RAG_THERAPIST_SCOPED=true` to restrict retrieval to the authenticated therapist’s notes only (stronger privacy isolation).

**Privacy / consent:** Global search can surface excerpts from sessions recorded by **other** therapists. Use only if your product and legal basis allow that; otherwise keep `RAG_THERAPIST_SCOPED=true`.

## Collections

### `soap_chunks` (RAG index — primary read path for chat)

Created by the backfill script or your Express app when saving notes. One document per text chunk.

| Field | Type | Description |
|-------|------|-------------|
| `therapistId` | string | **Stored on each chunk** (who recorded the session). Used only when `RAG_THERAPIST_SCOPED=true`; otherwise retrieval is across all chunks. |
| `patientId` | string \| null | Optional; used for optional UI filtering. |
| `sessionId` | string | Session or appointment id for citations. |
| `field` | string | SOAP section: `subjective`, `objective`, `assessment`, `plan`, or `combined`. |
| `text` | string | Chunk text used for display and embedding. |
| `embedding` | float[] | Vector from the configured `EMBEDDING_MODEL` (e.g. `gemini-embedding-001`). Query and stored chunks must use the **same** model. |
| `sessionDate` | string \| datetime \| null | ISO date for citation display. |
| `createdAt` | datetime | Optional. |

**Atlas Vector Search:** Not required for this implementation. Retrieval uses **in-memory cosine similarity** over the candidate chunk set (optionally filtered by therapist). For large corpora, add Atlas Vector Search and switch the retrieval layer.

### Source notes (optional — backfill input)

If you store full SOAP notes in another collection (e.g. `soap_notes`, `sessions`), configure `SOAP_SOURCE_COLLECTION` and map fields in `app/config.py` / backfill script. Expected logical shape:

| Field | Type |
|-------|------|
| `therapistId` | string |
| `patientId` | string (optional) |
| `sessionId` or `_id` | string |
| `subjective`, `objective`, `assessment`, `plan` | string (optional each) |
| `createdAt` / `date` / `sessionDate` | optional |

The backfill script flattens non-empty SOAP fields into `soap_chunks` with embeddings.

**Therakonnect `patientrecords` (and similar):** Documents may use **`therapist`** and **`patient`** (ObjectIds) instead of `therapistId` / `patientId`, and store SOAP in a **`notes`** array. Each note can be an object with `subjective` / `objective` / `assessment` / `plan`, a nested `soap` object, or a single text field such as `content`, `text`, `body`, `note`, or `description`. String-only notes are chunked as `combined`. The chunker maps this shape automatically (`app/rag/chunking.py`).

## JWT (must align with Express)

- **Bearer token** in `Authorization` header.
- Default validation: **HS256** with `JWT_SECRET` (same secret as Express if it signs HS256 JWTs).
- Therapist id from claim: `sub`, or `therapistId`, or `userId` (configurable order in `app/auth.py`).

**Local testing:** If `SKIP_THERAPIST_JWT=true`, the API skips JWT (`DEV_THERAPIST_ID` optional; defaults to `anonymous` for logging). Retrieval does not use therapist id unless `RAG_THERAPIST_SCOPED=true`. Do **not** enable skip-JWT in production.

## Environment summary

See `.env.example` for `MONGODB_URI`, `JWT_SECRET`, `GOOGLE_API_KEY` / `GEMINI_API_KEY`, and collection overrides.
