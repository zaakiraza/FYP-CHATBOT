from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from motor.motor_asyncio import AsyncIOMotorCollection

# Only fetch the fields the app actually uses — avoids transferring extra data from Atlas
_PROJECTION = {
    "embedding": 1,
    "field": 1,
    "text": 1,
    "sessionId": 1,
    "patientId": 1,
    "therapistId": 1,
    "sessionDate": 1,
    "_id": 0,
}


def _build_chunk(doc: dict[str, Any], score: float) -> "RetrievedChunk":
    raw_pid = doc.get("patientId")
    patient_id_val = str(raw_pid) if raw_pid is not None else None
    raw_tid = doc.get("therapistId")
    source_tid = str(raw_tid).strip() if raw_tid is not None and str(raw_tid).strip() else None
    return RetrievedChunk(
        session_id=str(doc.get("sessionId") or ""),
        patient_id=patient_id_val,
        field=str(doc.get("field") or ""),
        text=str(doc.get("text") or ""),
        score=score,
        session_date=doc.get("sessionDate"),
        source_therapist_id=source_tid,
    )


@dataclass
class RetrievedChunk:
    session_id: str
    patient_id: str | None
    field: str
    text: str
    score: float
    session_date: Any | None
    source_therapist_id: str | None = None


async def _retrieve_brute_force(
    collection: AsyncIOMotorCollection,
    *,
    query_embedding: list[float],
    top_k: int,
    max_fetch: int,
    min_score: float,
    therapist_id_filter: str | None = None,
    patient_id: str | None = None,
    session_id: str | None = None,
) -> list[RetrievedChunk]:
    """
    Fetch candidate docs from MongoDB (with field projection to minimise data transfer),
    then rank all embeddings in one numpy batch operation instead of a Python loop.
    Works on any MongoDB deployment — no vector index required.
    """
    filt: dict[str, Any] = {"embedding": {"$exists": True}}
    if therapist_id_filter:
        filt["therapistId"] = therapist_id_filter
    if patient_id:
        filt["patientId"] = patient_id
    if session_id:
        filt["sessionId"] = session_id

    # Collect docs and their embeddings in two parallel lists
    docs: list[dict[str, Any]] = []
    raw_embeddings: list[list[float]] = []
    dim = len(query_embedding)

    cursor = collection.find(filt, _PROJECTION).limit(max_fetch)
    async for doc in cursor:
        emb = doc.get("embedding")
        if not isinstance(emb, list) or len(emb) != dim:
            continue
        docs.append(doc)
        raw_embeddings.append(emb)

    if not docs:
        return []

    # Batch cosine similarity via numpy — orders of magnitude faster than a Python loop
    matrix = np.array(raw_embeddings, dtype=np.float32)   # (n, dim)
    q = np.array(query_embedding, dtype=np.float32)         # (dim,)

    dots = matrix @ q                                        # (n,)
    q_norm = float(np.linalg.norm(q))
    doc_norms = np.linalg.norm(matrix, axis=1)              # (n,)
    denom = doc_norms * q_norm
    denom = np.where(denom == 0.0, 1e-10, denom)
    scores = dots / denom                                    # (n,)

    # Filter by min_score threshold
    mask = scores >= min_score
    valid_indices = np.where(mask)[0]
    if len(valid_indices) == 0:
        return []

    # Sort valid results descending, take top_k
    valid_scores = scores[valid_indices]
    order = np.argsort(valid_scores)[::-1][:top_k]

    return [
        _build_chunk(docs[valid_indices[i]], float(valid_scores[i]))
        for i in order
    ]


async def _retrieve_atlas(
    collection: AsyncIOMotorCollection,
    *,
    query_embedding: list[float],
    top_k: int,
    max_fetch: int,
    min_score: float,
    index_name: str,
    therapist_id_filter: str | None = None,
    patient_id: str | None = None,
    session_id: str | None = None,
) -> list[RetrievedChunk]:
    """
    Uses MongoDB Atlas $vectorSearch aggregation.
    Requires a vector search index named `index_name` on the `embedding` field.
    Create it once via Atlas UI:
      { "fields": [{ "type": "vector", "path": "embedding", "numDimensions": <dim>, "similarity": "cosine" }] }
    Add filter fields (therapistId, patientId, sessionId) as "filter" type entries if needed.
    """
    pre_filter: dict[str, Any] = {}
    if therapist_id_filter:
        pre_filter["therapistId"] = {"$eq": therapist_id_filter}
    if patient_id:
        pre_filter["patientId"] = {"$eq": patient_id}
    if session_id:
        pre_filter["sessionId"] = {"$eq": session_id}

    vs_stage: dict[str, Any] = {
        "index": index_name,
        "path": "embedding",
        "queryVector": query_embedding,
        "numCandidates": max_fetch,
        "limit": top_k,
    }
    if pre_filter:
        vs_stage["filter"] = pre_filter

    pipeline: list[dict[str, Any]] = [
        {"$vectorSearch": vs_stage},
        {"$addFields": {"_vs_score": {"$meta": "vectorSearchScore"}}},
    ]

    results: list[RetrievedChunk] = []
    async for doc in collection.aggregate(pipeline):
        score = float(doc.get("_vs_score", 0.0))
        if score < min_score:
            continue
        results.append(_build_chunk(doc, score))
    return results


async def retrieve_top_k(
    collection: AsyncIOMotorCollection,
    *,
    query_embedding: list[float],
    top_k: int,
    max_fetch: int,
    min_score: float = 0.0,
    use_atlas_vector_search: bool = False,
    atlas_vector_index_name: str = "vector_index",
    therapist_id_filter: str | None = None,
    patient_id: str | None = None,
    session_id: str | None = None,
) -> list[RetrievedChunk]:
    """
    Retrieve the most semantically relevant chunks for a query embedding.

    - use_atlas_vector_search=True: uses MongoDB Atlas $vectorSearch (fast, ANN index required).
    - use_atlas_vector_search=False (default): fetches up to max_fetch docs and ranks in memory.
    - min_score: chunks with cosine similarity below this value are dropped before returning.
    """
    if use_atlas_vector_search:
        return await _retrieve_atlas(
            collection,
            query_embedding=query_embedding,
            top_k=top_k,
            max_fetch=max_fetch,
            min_score=min_score,
            index_name=atlas_vector_index_name,
            therapist_id_filter=therapist_id_filter,
            patient_id=patient_id,
            session_id=session_id,
        )
    return await _retrieve_brute_force(
        collection,
        query_embedding=query_embedding,
        top_k=top_k,
        max_fetch=max_fetch,
        min_score=min_score,
        therapist_id_filter=therapist_id_filter,
        patient_id=patient_id,
        session_id=session_id,
    )
