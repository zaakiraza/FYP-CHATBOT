import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.cache import get_cached_response, set_cached_response
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
                "message": "How should I approach a patient dealing with severe academic stress?",
            }
        }
    )

    message: str = Field(
        ...,
        min_length=1,
        max_length=8000,
        description="Clinical question for the recommendation assistant. The question is embedded and matched against documented SOAP case patterns.",
        examples=["What approaches have worked for patients with intrusive thoughts and OCD?"],
    )


class ChatResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "reply": "My recommendation for a patient dealing with academic stress is...",
            }
        }
    )

    reply: str = Field(description="Anonymised clinical recommendation grounded in documented SOAP case patterns.")


_CHAT_OPENAPI_EXAMPLES = {
    "stress": {
        "summary": "Stress / academic pressure",
        "description": "Ask for a recommendation on a clinical theme. The assistant searches all documented cases and responds anonymously.",
        "value": {
            "message": "How should I approach a patient dealing with severe stress due to academic pressure?",
        },
    },
    "ocd": {
        "summary": "OCD and intrusive thoughts",
        "value": {
            "message": "What techniques have been effective for patients with OCD and intrusive thoughts?",
        },
    },
    "greeting": {
        "summary": "Greeting",
        "value": {
            "message": "Hello",
        },
    },
}


async def _run_chat(
    body: ChatRequest,
    settings: Settings,
) -> ChatResponse:
    # --- Response cache: return immediately if this exact question was answered recently ---
    cached = await get_cached_response(body.message)
    if cached is not None:
        logger.info("chat_request cache=HIT")
        return ChatResponse(reply=cached)

    db = get_database()
    coll = db[settings.soap_chunks_collection]

    q_emb = await embed_text(body.message, settings=settings, for_document=False)

    chunks = await retrieve_top_k(
        coll,
        query_embedding=q_emb,
        top_k=settings.rag_top_k,
        max_fetch=settings.rag_max_chunks_fetch,
        min_score=settings.rag_min_score,
        use_atlas_vector_search=settings.use_atlas_vector_search,
        atlas_vector_index_name=settings.atlas_vector_index_name,
        therapist_id_filter=None,
    )

    logger.info("chat_request cache=MISS chunks=%s", len(chunks))

    if not chunks:
        reply = (
            "I don't have any documented case experience related to this situation. "
            "I can only make recommendations based on cases that have been recorded on this platform. "
            "Please try rephrasing your question or ask about a different clinical topic."
        )
        return ChatResponse(reply=reply)

    reply = await generate_chat_reply(body.message, chunks, settings=settings)
    # Store successful LLM reply in response cache
    await set_cached_response(body.message, reply)
    return ChatResponse(reply=reply)


@router.post(
    "/therapist-chat",
    response_model=ChatResponse,
    summary="Clinical Recommendation (SOAP RAG)",
    response_description="Anonymised clinical recommendation grounded in documented SOAP cases.",
    description=(
        "Send a natural-language clinical **message**. The assistant searches all documented SOAP case patterns "
        "and returns an anonymised recommendation. No patient or therapist identities are ever revealed."
    ),
)
@limiter.limit(CHAT_RATE_LIMIT)
async def therapist_chat(
    request: Request,
    body: Annotated[ChatRequest, Body(..., openapi_examples=_CHAT_OPENAPI_EXAMPLES)],
    settings: Settings = Depends(get_settings),
) -> ChatResponse:
    return await _run_chat(body, settings)
