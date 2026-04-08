"""
Read full SOAP documents from SOAP_SOURCE_COLLECTION, chunk + embed, upsert into SOAP_CHUNKS_COLLECTION.

Usage (from project root):
  set MONGODB_URI=...
  set SOAP_SOURCE_COLLECTION=soap_notes
  python -m scripts.backfill_chunks

Optional: limit number of source docs for testing:
  python -m scripts.backfill_chunks --limit 10
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running without installing the package
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.db import close_database, get_database
from app.rag.chunking import SoapChunkDraft, chunk_source_document
from app.rag.embeddings import embed_document_text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _chunk_key(session_id: str, field: str, text: str) -> str:
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
    return f"{session_id}|{field}|{h}"


async def run_backfill(*, limit: int | None) -> None:
    settings = get_settings()
    if not settings.soap_source_collection:
        raise SystemExit("Set SOAP_SOURCE_COLLECTION in .env (e.g. soap_notes)")

    db = get_database()
    src = db[settings.soap_source_collection]
    dst = db[settings.soap_chunks_collection]

    total = await src.count_documents({})
    logger.info(
        "Database=%s source=%s target=%s documents_in_source=%s",
        settings.mongodb_db_name,
        settings.soap_source_collection,
        settings.soap_chunks_collection,
        total,
    )
    if total == 0:
        names = await db.list_collection_names()
        logger.error(
            "Source collection is empty or wrong name. "
            "Fix SOAP_SOURCE_COLLECTION / MONGODB_DB_NAME in .env. "
            "Collections in this database: %s",
            ", ".join(sorted(names)) or "(none)",
        )
        return

    await dst.create_index("therapistId")
    await dst.create_index([("therapistId", 1), ("sessionId", 1)])
    await dst.create_index("chunkKey", unique=False)

    cursor = src.find({})
    if limit:
        cursor = cursor.limit(limit)

    processed = 0
    upserted = 0
    async for doc in cursor:
        processed += 1
        # Normalize _id to string for session id fallback
        if "_id" in doc and "sessionId" not in doc and "session_id" not in doc:
            doc = {**doc, "sessionId": str(doc["_id"])}
        drafts: list[SoapChunkDraft] = chunk_source_document(doc)
        for d in drafts:
            vec = await embed_document_text(d.text, settings=settings)
            chunk_key = _chunk_key(d.session_id, d.field, d.text)
            now = datetime.now(timezone.utc)
            payload: dict[str, Any] = {
                "chunkKey": chunk_key,
                "therapistId": d.therapist_id,
                "patientId": d.patient_id,
                "sessionId": d.session_id,
                "field": d.field,
                "text": d.text,
                "embedding": vec,
                "sessionDate": d.session_date,
                "updatedAt": now,
            }
            r = await dst.update_one(
                {"chunkKey": chunk_key},
                {"$set": payload, "$setOnInsert": {"createdAt": now}},
                upsert=True,
            )
            if r.upserted_id is not None or r.modified_count:
                upserted += 1
        if processed % 20 == 0:
            logger.info("Processed %s source documents…", processed)

    logger.info("Done. Source docs processed: %s, chunks upserted (new/updated rows): %s", processed, upserted)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None, help="Max source documents to process")
    args = p.parse_args()
    try:
        asyncio.run(run_backfill(limit=args.limit))
    finally:
        close_database()


if __name__ == "__main__":
    main()
