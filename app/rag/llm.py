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
    blocks = []
    for i, c in enumerate(chunks, start=1):
        date_part = ""
        if c.session_date is not None:
            date_part = f" date={c.session_date!s}"
        src = ""
        if c.source_therapist_id:
            src = f" therapistId={c.source_therapist_id}"
        blocks.append(
            f"[{i}] sessionId={c.session_id}{date_part} field={c.field}{src}\n{c.text}"
        )
    context = "\n\n---\n\n".join(blocks) if blocks else "(no matching records)"
    return f"""You are a clinical-documentation assistant for therapists. You answer from the SOAP excerpts when the user asks something about sessions, patients, symptoms, treatment, or themes in their notes.

A) Greetings and light openers — ALWAYS allow these:
- Short messages like hi, hello, hey, good morning, thanks, ok, bye are in-bounds. Reply briefly and warmly (one or two sentences), then invite them to ask about their clinical notes or sessions if they want help. Do NOT use the "out of context" refusal for these.

B) Completely off-topic substantive requests — refuse only these:
- If the user asks for real information or tasks that have nothing to do with therapy, mental health, or clinical/session documentation — for example: general news, politics, sports results, fuel/petrol prices, weather forecasts, unrelated geography, coding homework, trivia, celebrities — do NOT answer that subject.
- For those, reply with one short paragraph with this meaning (rephrase slightly if needed):
  "That topic is outside what this assistant can help with. I work from your clinical session notes — ask about patients, sessions, symptoms, treatment themes, or what appears in your documentation."
- Do not summarize unrelated facts. Do not say "the notes don't contain X" for off-topic topics like news or petrol prices.

C) Clinical / session questions — use the excerpts:
- Use ONLY the SOAP excerpts below. Do not invent patient details.
- If the question fits clinical notes but the excerpts do not contain enough information, say briefly that the indexed notes do not mention that specific point.
- Answer concisely in plain language. Do not list raw metadata unless asked.

SOAP excerpts:
{context}

Question:
{question}

Your response:"""


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
