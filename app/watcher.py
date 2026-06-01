"""
Real-time change stream watcher.

Watches patientrecords, treatmentplans, and assessments for inserts/updates.
Automatically chunks + embeds new documents into soap_chunks.
Resume tokens are persisted in _watcher_state so the watcher picks up where
it left off after a server restart.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from app.config import Settings, get_settings
from app.db import get_database
from app.rag.chunking import (
    SoapChunkDraft,
    chunk_assessment_document,
    chunk_source_document,
    chunk_treatment_plan_document,
)
from app.rag.embeddings import embed_document_text

logger = logging.getLogger(__name__)

_tasks: list[asyncio.Task] = []


def _chunk_key(session_id: str, field: str, text: str) -> str:
    h = hashlib.sha256(text.encode()).hexdigest()[:32]
    return f"{session_id}|{field}|{h}"


async def _upsert_chunks(
    drafts: list[SoapChunkDraft],
    dst: Any,
    settings: Settings,
) -> int:
    upserted = 0
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
    return upserted


async def _watch_collection(
    collection_name: str,
    chunker: Callable[[dict[str, Any]], list[SoapChunkDraft]],
    state_col: Any,
    dst: Any,
    settings: Settings,
) -> None:
    db = get_database()
    src = db[collection_name]

    # Load persisted resume token so we don't re-process old documents after restart
    state_doc = await state_col.find_one({"_id": collection_name})
    resume_token = state_doc.get("resumeToken") if state_doc else None

    logger.info(
        "watcher starting collection=%s resume=%s",
        collection_name,
        "yes" if resume_token else "no",
    )

    pipeline = [{"$match": {"operationType": {"$in": ["insert", "update", "replace"]}}}]

    while True:
        try:
            kwargs: dict[str, Any] = {}
            if resume_token:
                kwargs["resume_after"] = resume_token

            async with src.watch(pipeline, full_document="updateLookup", **kwargs) as stream:
                async for change in stream:
                    op = change.get("operationType")
                    doc = change.get("fullDocument")
                    if not doc:
                        continue

                    # patientrecords may not have sessionId — fall back to _id
                    if "_id" in doc and "sessionId" not in doc and "session_id" not in doc:
                        doc = {**doc, "sessionId": str(doc["_id"])}

                    drafts = chunker(doc)
                    if drafts:
                        count = await _upsert_chunks(drafts, dst, settings)
                        logger.info(
                            "watcher collection=%s op=%s chunks_upserted=%d",
                            collection_name,
                            op,
                            count,
                        )

                    # Persist resume token after each event
                    resume_token = stream.resume_token
                    await state_col.update_one(
                        {"_id": collection_name},
                        {
                            "$set": {
                                "resumeToken": resume_token,
                                "updatedAt": datetime.now(timezone.utc),
                            }
                        },
                        upsert=True,
                    )

        except asyncio.CancelledError:
            logger.info("watcher stopped collection=%s", collection_name)
            return
        except Exception as exc:
            logger.warning(
                "watcher error collection=%s error=%s — retrying in 5s",
                collection_name,
                exc,
            )
            await asyncio.sleep(5)


async def start_watchers() -> None:
    settings = get_settings()
    db = get_database()
    dst = db[settings.soap_chunks_collection]
    state_col = db["_watcher_state"]

    collection_map: list[tuple[str | None, Callable]] = [
        (settings.soap_source_collection, chunk_source_document),
        (settings.treatment_plans_collection, chunk_treatment_plan_document),
        (settings.assessments_collection, chunk_assessment_document),
    ]

    for col_name, chunker in collection_map:
        if not col_name:
            continue
        task = asyncio.create_task(
            _watch_collection(col_name, chunker, state_col, dst, settings),
            name=f"watcher:{col_name}",
        )
        _tasks.append(task)
        logger.info("watcher registered collection=%s", col_name)


async def stop_watchers() -> None:
    for task in _tasks:
        task.cancel()
    if _tasks:
        await asyncio.gather(*_tasks, return_exceptions=True)
        _tasks.clear()
    logger.info("watcher all stopped")
