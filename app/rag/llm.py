from __future__ import annotations

import asyncio
import os
from functools import lru_cache

from fastapi import HTTPException, status
from google import genai
from google.genai.errors import ClientError

from app.config import Settings, get_settings
from app.rag.retrieve import RetrievedChunk


@lru_cache
def _client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def _ensure_key(settings: Settings) -> str:
    key = settings.effective_gemini_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY is not set")
    os.environ.setdefault("GEMINI_API_KEY", key)
    return key


def _build_prompt(question: str, chunks: list[RetrievedChunk]) -> str:
    # Build context blocks — only include the SOAP field label and text.
    # Never include sessionId, patientId, therapistId, or any other identifier
    # so the LLM cannot accidentally leak identity in its response.
    blocks = []
    for i, c in enumerate(chunks, start=1):
        blocks.append(f"[Case {i}] ({c.field})\n{c.text}")
    context = "\n\n---\n\n".join(blocks) if blocks else "(no documented cases)"
    return f"""You are a clinical recommendation assistant integrated into a therapy practice management platform. Therapists ask you questions about how to handle clinical situations, and you recommend approaches based solely on documented case patterns from SOAP notes recorded on this platform.

IDENTITY RULE (highest priority — never break this):
- The case excerpts below come from real clinical records. They contain NO names, IDs, or identifiers — do NOT invent or infer any. Never mention a patient name, therapist name, session ID, or any personal identifier in your response. If you somehow see one in the excerpts, ignore it completely.

RESPONSE FORMAT RULE:
- Always frame your answer as a recommendation. Begin with a phrase such as:
  "My recommendation for [condition/situation] is..." or "Based on documented cases, I recommend..."
- Do not say "the notes say" or "according to the records". Speak as a recommender, not a document reader.

A) Greetings and light openers:
- Short messages like hi, hello, hey, good morning, thanks, ok, bye — reply briefly and warmly (one or two sentences), then invite the therapist to ask about a clinical situation. Do NOT refuse these.

B) Off-topic requests — refuse:
- Questions unrelated to therapy, mental health, or clinical practice (e.g. politics, economy, news, sports, personal matters, coding, weather) must be refused.
- Reply with: "I can only assist with clinical recommendations based on documented therapy cases. Please ask about a patient condition, treatment approach, or therapeutic technique."
- Do NOT answer the off-topic subject, even partially.

C) Clinical questions — recommend from documented cases:
- Use ONLY the case excerpts below. Do not draw on general medical or psychological knowledge outside what the excerpts support.
- If the excerpts contain relevant documented experience, give a clear recommendation framed as described above.
- If the excerpts do not contain enough relevant information to support a recommendation, reply with: "I don't have enough documented case experience with this specific situation to make a recommendation."
- Do NOT invent treatment advice not supported by the excerpts.

Documented case excerpts (anonymised):
{context}

Therapist's question:
{question}

Your recommendation:"""


async def generate_chat_reply(
    question: str,
    chunks: list[RetrievedChunk],
    *,
    settings: Settings | None = None,
) -> str:
    s = settings or get_settings()
    key = _ensure_key(s)
    client = _client(key)
    prompt = _build_prompt(question, chunks)

    def _call_sync() -> str:
        resp = client.models.generate_content(
            model=s.gemini_chat_model,
            contents=prompt,
        )
        text = getattr(resp, "text", None)
        if text:
            return text
        cands = getattr(resp, "candidates", None) or []
        if cands:
            parts = getattr(cands[0].content, "parts", None) or []
            if parts and getattr(parts[0], "text", None):
                return parts[0].text
        return "Unable to generate a response."

    try:
        return await asyncio.to_thread(_call_sync)
    except ClientError as e:
        code = getattr(e, "status_code", None)
        if code == 429:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Gemini rate limit or quota exceeded for model `{s.gemini_chat_model}` "
                    "(generateContent). Wait a minute and retry, set `GEMINI_CHAT_MODEL` to another "
                    "model (e.g. `gemini-2.5-flash`), or check usage: "
                    "https://ai.google.dev/gemini-api/docs/rate-limits"
                ),
            ) from e
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gemini API error ({code}): {e!s}",
        ) from e
