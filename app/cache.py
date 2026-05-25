"""
Redis cache helpers.

Two cache layers:
  - Embedding cache  (key: emb:<sha256(text)>)  TTL: EMBEDDING_CACHE_TTL (default 24h)
    Gemini embeddings are deterministic for identical text, so we never need to re-call
    the embedding API for the same string.

  - Response cache   (key: resp:<sha256(message)>) TTL: RESPONSE_CACHE_TTL (default 1h)
    Full LLM replies are cached so repeated clinical questions skip embedding + retrieval
    + generation entirely.

Graceful degradation: if REDIS_URL is blank or Redis is unreachable, every cache
operation is silently skipped and the app continues without caching.
"""
from __future__ import annotations

import hashlib
import json
import logging

import redis.asyncio as aioredis

from app.config import get_settings

logger = logging.getLogger(__name__)

_client: aioredis.Redis | None = None
_client_disabled: bool = False  # set to True after a connection failure to stop retrying


def _get_client() -> aioredis.Redis | None:
    global _client, _client_disabled
    if _client_disabled:
        return None
    if _client is not None:
        return _client
    settings = get_settings()
    url = (settings.redis_url or "").strip()
    if not url:
        _client_disabled = True
        return None
    try:
        _client = aioredis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        logger.info("Redis cache enabled — %s", url.split("@")[-1])  # hide credentials if any
    except Exception as exc:
        logger.warning("Redis init failed (%s) — caching disabled", exc)
        _client_disabled = True
    return _client


async def close_redis() -> None:
    global _client, _client_disabled
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
        _client = None
    _client_disabled = False


async def ping_redis() -> bool:
    """Test the Redis connection. Called at startup to surface config errors early."""
    client = _get_client()
    if client is None:
        return False
    try:
        await client.ping()
        logger.info("Redis ping OK")
        return True
    except Exception as exc:
        logger.warning("Redis ping FAILED (%s) — caching will be disabled", exc)
        return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _key(prefix: str, text: str) -> str:
    """Stable cache key: prefix + SHA-256 of normalised text."""
    digest = hashlib.sha256(text.strip().lower().encode()).hexdigest()
    return f"{prefix}:{digest}"


async def _get(key: str) -> str | None:
    client = _get_client()
    if client is None:
        return None
    try:
        return await client.get(key)
    except Exception as exc:
        logger.warning("Redis GET failed (%s)", exc)
        return None


async def _set(key: str, value: str, ttl: int) -> None:
    client = _get_client()
    if client is None:
        return
    try:
        await client.set(key, value, ex=ttl)
    except Exception as exc:
        logger.warning("Redis SET failed (%s)", exc)


# ---------------------------------------------------------------------------
# Embedding cache
# ---------------------------------------------------------------------------

async def get_cached_embedding(text: str) -> list[float] | None:
    raw = await _get(_key("emb", text))
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def set_cached_embedding(text: str, embedding: list[float]) -> None:
    settings = get_settings()
    await _set(_key("emb", text), json.dumps(embedding), settings.embedding_cache_ttl)


# ---------------------------------------------------------------------------
# Response cache
# ---------------------------------------------------------------------------

async def get_cached_response(message: str) -> str | None:
    return await _get(_key("resp", message))


async def set_cached_response(message: str, reply: str) -> None:
    settings = get_settings()
    await _set(_key("resp", message), reply, settings.response_cache_ttl)
