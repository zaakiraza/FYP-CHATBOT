from __future__ import annotations

import asyncio
import os
from functools import lru_cache

from google import genai

from app.config import Settings, get_settings


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
    """Return embedding vector. Uses RETRIEVAL_QUERY vs RETRIEVAL_DOCUMENT when supported."""
    s = settings or get_settings()
    key = _ensure_key(s)
    client = _client(key)

    def _call() -> list[float]:
        try:
            from google.genai import types as genai_types

            task = "RETRIEVAL_DOCUMENT" if for_document else "RETRIEVAL_QUERY"
            cfg = genai_types.EmbedContentConfig(task_type=task)  # type: ignore[attr-defined]
            resp = client.models.embed_content(
                model=s.embedding_model,
                contents=text,
                config=cfg,
            )
        except Exception:
            resp = client.models.embed_content(
                model=s.embedding_model,
                contents=text,
            )
        return _first_embedding_values(resp)

    return await asyncio.to_thread(_call)


async def embed_document_text(text: str, settings: Settings | None = None) -> list[float]:
    return await embed_text(text, settings=settings, for_document=True)
