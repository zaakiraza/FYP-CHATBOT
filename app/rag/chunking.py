from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

SOAP_FIELDS = ("subjective", "objective", "assessment", "plan")

# Split very long fields into smaller chunks (characters, paragraph-aware).
MAX_CHUNK_CHARS = 1500


@dataclass
class SoapChunkDraft:
    therapist_id: str
    patient_id: str | None
    session_id: str
    field: str
    text: str
    session_date: Any | None


def _split_long_text(text: str, max_chars: int) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []
    parts: list[str] = []
    paragraphs = re.split(r"\n\s*\n", text)
    buf = ""
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if len(buf) + len(p) + 2 <= max_chars:
            buf = f"{buf}\n\n{p}" if buf else p
        else:
            if buf:
                parts.append(buf.strip())
            if len(p) <= max_chars:
                buf = p
            else:
                for i in range(0, len(p), max_chars):
                    parts.append(p[i : i + max_chars].strip())
                buf = ""
    if buf:
        parts.append(buf.strip())
    return [x for x in parts if x]


def _as_str_id(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    return s or None


def _drafts_for_soap_dict(
    *,
    therapist_id: str,
    patient_id: str | None,
    session_id: str,
    session_date: Any | None,
    soap_src: dict[str, Any],
) -> list[SoapChunkDraft]:
    out: list[SoapChunkDraft] = []
    for field in SOAP_FIELDS:
        raw = soap_src.get(field)
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        for piece in _split_long_text(text, MAX_CHUNK_CHARS):
            out.append(
                SoapChunkDraft(
                    therapist_id=therapist_id,
                    patient_id=patient_id,
                    session_id=session_id,
                    field=field,
                    text=piece,
                    session_date=session_date,
                )
            )
    return out


def _flatten_note_dict(note: dict[str, Any]) -> dict[str, Any]:
    """Unwrap common shapes: { soap: { S/O/A/P } } or top-level S/O/A/P."""
    if "soap" in note and isinstance(note["soap"], dict):
        base = {**note, **note["soap"]}
    else:
        base = dict(note)
    return base


def _drafts_from_notes_array(
    doc: dict[str, Any],
    *,
    therapist_id: str,
    patient_id: str | None,
    parent_session_id: str,
    session_date: Any | None,
    notes: list[Any],
) -> list[SoapChunkDraft]:
    out: list[SoapChunkDraft] = []
    for i, note in enumerate(notes):
        if isinstance(note, str):
            t = note.strip()
            if not t:
                continue
            sid = f"{parent_session_id}:n{i}"
            for piece in _split_long_text(t, MAX_CHUNK_CHARS):
                out.append(
                    SoapChunkDraft(
                        therapist_id=therapist_id,
                        patient_id=patient_id,
                        session_id=sid,
                        field="combined",
                        text=piece,
                        session_date=session_date,
                    )
                )
            continue
        if not isinstance(note, dict):
            continue
        n = _flatten_note_dict(note)
        note_id = _as_str_id(n.get("_id") or n.get("id"))
        sid = f"{parent_session_id}:n{i}" if not note_id else f"{parent_session_id}:{note_id}"

        chunk_part = _drafts_for_soap_dict(
            therapist_id=therapist_id,
            patient_id=patient_id,
            session_id=sid,
            session_date=session_date,
            soap_src=n,
        )
        if chunk_part:
            out.extend(chunk_part)
            continue

        # Fallback: one blob field often used in apps
        blob = (
            n.get("content")
            or n.get("text")
            or n.get("body")
            or n.get("note")
            or n.get("description")
        )
        if blob is not None:
            t = str(blob).strip()
            if t:
                for piece in _split_long_text(t, MAX_CHUNK_CHARS):
                    out.append(
                        SoapChunkDraft(
                            therapist_id=therapist_id,
                            patient_id=patient_id,
                            session_id=sid,
                            field="combined",
                            text=piece,
                            session_date=session_date,
                        )
                    )
    return out


def chunk_source_document(doc: dict[str, Any]) -> list[SoapChunkDraft]:
    """
    Turn a MongoDB document into chunk drafts (no embeddings).

    Supports:
    - Flat SOAP on the root: therapistId|therapist_id|therapist, patientId|patient_id|patient,
      sessionId|session_id|_id, subjective/objective/assessment/plan.
    - Therakonnect-style patientrecords: root therapist/patient ObjectIds, notes[] with SOAP or text blobs.
    """
    therapist_id = _as_str_id(
        doc.get("therapistId") or doc.get("therapist_id") or doc.get("therapist"),
    )
    if not therapist_id:
        return []

    patient_id = _as_str_id(
        doc.get("patientId") or doc.get("patient_id") or doc.get("patient"),
    )
    parent_sid = _as_str_id(doc.get("sessionId") or doc.get("session_id") or doc.get("_id"))
    if not parent_sid:
        return []

    session_date = (
        doc.get("sessionDate")
        or doc.get("session_date")
        or doc.get("createdAt")
        or doc.get("created_at")
        or doc.get("date")
    )

    # 1) SOAP fields on document root (classic flat schema)
    root_chunks = _drafts_for_soap_dict(
        therapist_id=therapist_id,
        patient_id=patient_id,
        session_id=parent_sid,
        session_date=session_date,
        soap_src=doc,
    )
    if root_chunks:
        return root_chunks

    # 2) notes[] — e.g. patientrecords from Mongoose
    notes = doc.get("notes")
    if isinstance(notes, list) and len(notes) > 0:
        return _drafts_from_notes_array(
            doc,
            therapist_id=therapist_id,
            patient_id=patient_id,
            parent_session_id=parent_sid,
            session_date=session_date,
            notes=notes,
        )

    return []
