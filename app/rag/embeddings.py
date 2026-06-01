from __future__ import annotations

import asyncio
import logging
import os
from functools import lru_cache

from google import genai

from app.cache import get_cached_embedding, set_cached_embedding
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_RETRYABLE_CODES = {429, 503}


@lru_cache
def _client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def _ensure_key(settings: Settings) -> str:
    key = settings.effective_gemini_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY is not set")
    os.environ.setdefault("GEMINI_API_KEY", key)
    return key


def _first_embedding_values(resp: object) -> list[float]:
    embs = getattr(resp, "embeddings", None) or []
    if not embs:
        raise RuntimeError("Embedding returned no vectors")
    values = embs[0].values
    if values is None:
        raise RuntimeError("Embedding values missing")
    return list(values)


async def embed_text(
    text: str,
    *,
    settings: Settings | None = None,
    for_document: bool = False,
) -> list[float]:
    """Return embedding vector. Checks Redis cache first; falls back to Gemini API."""
    if not for_document:
        cached = await get_cached_embedding(text)
        if cached is not None:
            return cached

    s = settings or get_settings()
    keys = s.gemini_api_keys()
    if not keys:
        raise RuntimeError("No Gemini API key configured")

    last_exc: Exception | None = None

    for idx, key in enumerate(keys):
        client = _client(key)

        def _call(c: genai.Client = client) -> list[float]:
            try:
                from google.genai import types as genai_types
                task = "RETRIEVAL_DOCUMENT" if for_document else "RETRIEVAL_QUERY"
                cfg = genai_types.EmbedContentConfig(task_type=task)  # type: ignore[attr-defined]
                resp = c.models.embed_content(model=s.embedding_model, contents=text, config=cfg)
            except Exception:
                resp = c.models.embed_content(model=s.embedding_model, contents=text)
            return _first_embedding_values(resp)

        try:
            embedding = await asyncio.to_thread(_call)
            if not for_document:
                await set_cached_embedding(text, embedding)
            return embedding
        except Exception as e:
            code = getattr(e, "status_code", None)
            if code in _RETRYABLE_CODES and idx < len(keys) - 1:
                logger.warning("embeddings key_%d returned %s — switching to next key", idx + 1, code)
                last_exc = e
                continue
            raise

    raise last_exc  # type: ignore[misc]


async def embed_document_text(text: str, settings: Settings | None = None) -> list[float]:
    return await embed_text(text, settings=settings, for_document=True)
