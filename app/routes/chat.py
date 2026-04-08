import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.auth import TherapistContext, get_current_therapist
from app.config import Settings, get_settings
from app.db import get_database
from app.limiter import CHAT_RATE_LIMIT, limiter
from app.rag.embeddings import embed_text
from app.rag.llm import generate_chat_reply
from app.rag.retrieve import RetrievedChunk, retrieve_top_k

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["therapist-chat"])


class ChatRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "What do the SOAP notes say about OCD, anxiety, or homework?",
                "patient_id": None,
                "session_id": None,
            }
        }
    )

    message: str = Field(
        ...,
        min_length=1,
        max_length=8000,
        description="Question for the assistant. Used to embed and retrieve the most relevant SOAP note chunks (semantic / topic match).",
        examples=["What themes appear in the notes related to intrusive thoughts?"],
    )
    patient_id: str | None = Field(
        default=None,
        description="Optional. If set, only chunks with this `patientId` in MongoDB are searched.",
    )
    session_id: str | None = Field(
        default=None,
        description="Optional. If set, only chunks with this `sessionId` are searched.",
    )


class ChatResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "reply": "The notes indicate…",
            }
        }
    )

    reply: str = Field(description="Answer grounded in retrieved SOAP excerpts (Gemini).")


_CHAT_OPENAPI_EXAMPLES = {
    "topic_search": {
        "summary": "Typical — message only",
        "description": "Semantic search over indexed `soap_chunks`. Leave `patient_id` and `session_id` null unless you need to narrow results.",
        "value": {
            "message": "What do the notes say about OCD or exposure homework?",
        },
    },
    "with_patient_session": {
        "summary": "Optional filters",
        "description": "Use MongoDB string ids matching your `patientId` / `sessionId` fields on chunks.",
        "value": {
            "message": "What was the plan for the last session?",
            "patient_id": "507f1f77bcf86cd799439011",
            "session_id": "507f191e810c19729de860ea:n0",
        },
    },
}


async def _run_chat(
    body: ChatRequest,
    ctx: TherapistContext,
    settings: Settings,
) -> ChatResponse:
    db = get_database()
    coll = db[settings.soap_chunks_collection]

    q_emb = await embed_text(body.message, settings=settings, for_document=False)

    scope_filter = ctx.therapist_id if settings.rag_therapist_scoped else None

    chunks = await retrieve_top_k(
        coll,
        query_embedding=q_emb,
        top_k=settings.rag_top_k,
        max_fetch=settings.rag_max_chunks_fetch,
        therapist_id_filter=scope_filter,
        patient_id=body.patient_id,
        session_id=body.session_id,
    )

    logger.info(
        "chat_request chunks=%s rag_therapist_scoped=%s caller=%s",
        len(chunks),
        settings.rag_therapist_scoped,
        (ctx.therapist_id[:12] + "…") if len(ctx.therapist_id) > 12 else ctx.therapist_id,
    )

    if not chunks:
        reply = (
            "No matching SOAP note excerpts were found for this question. "
            "Ensure notes are indexed (run the backfill script) or try rephrasing."
        )
        return ChatResponse(reply=reply)

    reply = await generate_chat_reply(body.message, chunks, settings=settings)
    return ChatResponse(reply=reply)


@router.post(
    "/therapist-chat",
    response_model=ChatResponse,
    summary="Chat (SOAP RAG)",
    response_description="Assistant reply (grounded in retrieved SOAP chunks).",
    description=(
        "**Authentication:** `Authorization: Bearer <JWT>` unless the server has `SKIP_THERAPIST_JWT=true`. "
        "Send a natural-language **message**; optional `patient_id` / `session_id` narrow MongoDB filters. "
        "Retrieval is semantic (topic match to your question). If `RAG_THERAPIST_SCOPED=false` (default), "
        "search is across all indexed chunks, not only the caller’s notes."
    ),
)
@limiter.limit(CHAT_RATE_LIMIT)
async def therapist_chat(
    request: Request,
    body: Annotated[ChatRequest, Body(..., openapi_examples=_CHAT_OPENAPI_EXAMPLES)],
    ctx: TherapistContext = Depends(get_current_therapist),
    settings: Settings = Depends(get_settings),
) -> ChatResponse:
    return await _run_chat(body, ctx, settings)


@router.post(
    "/v1/chat",
    response_model=ChatResponse,
    summary="Chat (SOAP RAG) — v1 alias",
    response_description="Same as `/api/therapist-chat`.",
    description="Alias path; same request/response as **POST /api/therapist-chat**.",
)
@limiter.limit(CHAT_RATE_LIMIT)
async def therapist_chat_v1(
    request: Request,
    body: Annotated[ChatRequest, Body(..., openapi_examples=_CHAT_OPENAPI_EXAMPLES)],
    ctx: TherapistContext = Depends(get_current_therapist),
    settings: Settings = Depends(get_settings),
) -> ChatResponse:
    return await _run_chat(body, ctx, settings)
