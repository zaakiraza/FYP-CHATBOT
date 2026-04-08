from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from motor.motor_asyncio import AsyncIOMotorCollection


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


@dataclass
class RetrievedChunk:
    session_id: str
    patient_id: str | None
    field: str
    text: str
    score: float
    session_date: Any | None
    source_therapist_id: str | None = None


async def retrieve_top_k(
    collection: AsyncIOMotorCollection,
    *,
    query_embedding: list[float],
    top_k: int,
    max_fetch: int,
    therapist_id_filter: str | None = None,
    patient_id: str | None = None,
    session_id: str | None = None,
) -> list[RetrievedChunk]:
    """
    Rank chunks by cosine similarity to the query embedding.
    If therapist_id_filter is set, only that therapist's chunks; otherwise all chunks in the collection.
    """
    filt: dict[str, Any] = {"embedding": {"$exists": True}}
    if therapist_id_filter:
        filt["therapistId"] = therapist_id_filter
    if patient_id:
        filt["patientId"] = patient_id
    if session_id:
        filt["sessionId"] = session_id

    cursor = collection.find(filt).limit(max_fetch)
    scored: list[RetrievedChunk] = []
    async for doc in cursor:
        emb = doc.get("embedding")
        if not isinstance(emb, list) or len(emb) != len(query_embedding):
            continue
        score = cosine_similarity(query_embedding, [float(x) for x in emb])
        raw_pid = doc.get("patientId")
        patient_id_val = str(raw_pid) if raw_pid is not None else None
        raw_tid = doc.get("therapistId")
        source_tid = str(raw_tid).strip() if raw_tid is not None and str(raw_tid).strip() else None
        scored.append(
            RetrievedChunk(
                session_id=str(doc.get("sessionId") or ""),
                patient_id=patient_id_val,
                field=str(doc.get("field") or ""),
                text=str(doc.get("text") or ""),
                score=score,
                session_date=doc.get("sessionDate"),
                source_therapist_id=source_tid,
            )
        )

    scored.sort(key=lambda x: x.score, reverse=True)
    return scored[:top_k]
