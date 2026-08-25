"""Meeting QuickChat transcript helpers (ephemeral context).

Transcript text is loaded from the meeting store on every LLM context
build and injected after the fixed system prompt. It is never written
to the session message table.

Source priority matches GET /meetings/{id}/transcript (UI):
  1. sentences.json (post-normalize; what the user sees in sections)
  2. transcript.json via meeting.transcript_path
"""

from __future__ import annotations

import logging
from typing import Any

from src.meeting.store import get_sentences, get_transcript

logger = logging.getLogger(__name__)

MEETING_SESSION_PREFIX = "meeting_"

# Injected as the transcript slot when no sentences/transcript body exists.
# Keeps layout stable (fixed system → transcript-or-notice → dialogue) and
# tells the model not to invent a meeting summary.
MEETING_TRANSCRIPT_UNAVAILABLE = (
    "MEETING TRANSCRIPT STATUS: unavailable.\n"
    "No transcript or sentence content is available for this meeting yet "
    "(transcription may still be in progress, or this meeting has no audio "
    "transcript).\n"
    "Tell the user clearly that you do not have the meeting record yet. "
    "Do not invent topics, decisions, action items, speaker quotes, "
    "placeholder names like [project], or citation markers like [ref:N]."
)


def format_segments_for_chat(segments: list[dict[str, Any]]) -> str:
    """Format segment dicts as numbered lines for the LLM system message.

    Each segment may use keys: text / original_text, speaker_id / speaker.
    """
    lines: list[str] = []
    auto_n = 0
    for seg in segments:
        text = (seg.get("text") or seg.get("original_text") or "").strip()
        if not text:
            continue
        spk = seg.get("speaker_id") or seg.get("speaker") or "unknown"
        raw_n = seg.get("ref_n")
        if raw_n is None:
            auto_n += 1
            n = auto_n
        else:
            n = int(raw_n)
        # [ref:N] matches MEETING_CHAT_SYSTEM_PROMPT so the model copies
        # that form instead of bare [N] from a "[1] speaker:" header.
        lines.append(f"[ref:{n}] {spk}: {text}")
    return "\n".join(lines)


def format_transcript_for_chat(transcript: Any) -> str:
    """Format a TranscriptionResult (or similar) for the LLM system message."""
    segments = getattr(transcript, "segments", None) or []
    as_dicts: list[dict[str, Any]] = []
    for seg in segments:
        if isinstance(seg, dict):
            as_dicts.append(seg)
        else:
            as_dicts.append({
                "text": getattr(seg, "text", "") or "",
                "speaker_id": getattr(seg, "speaker_id", None),
            })
    return format_segments_for_chat(as_dicts)


def load_meeting_transcript_text(meeting_id: str) -> str | None:
    """Load meeting body text for chat — same sources as the Meeting UI.

    Prefers sentences.json (section pipeline) so chat matches what the user
    sees even if transcript_path is missing. Falls back to transcript.json.
    """
    try:
        sentences = get_sentences(meeting_id)
        if sentences:
            text = format_segments_for_chat([
                {
                    "text": s.get("original_text") or s.get("text") or "",
                    "speaker_id": s.get("speaker") or s.get("speaker_id"),
                }
                for s in sentences
            ])
            if text.strip():
                return text

        transcript = get_transcript(meeting_id)
        if transcript and transcript.segments:
            text = format_transcript_for_chat(transcript)
            if text.strip():
                return text
    except Exception:
        logger.exception("Failed to load transcript for meeting %s", meeting_id)
        return None
    return None


def load_general_summary_text(meeting_id: str) -> str:
    """General tab markdown for meeting chat, or empty if missing."""
    try:
        from src.meeting.store import get_section_md

        text = get_section_md(meeting_id, "tab_general")
    except Exception:
        logger.exception("Failed to load General summary for meeting %s", meeting_id)
        return ""
    return (text or "").strip()


def speaker_display_map(meeting_id: str) -> dict[str, str]:
    """Same name map the Meeting UI uses (people bindings + stored labels)."""
    try:
        from src.meeting.store import get_meeting

        meeting = get_meeting(meeting_id)
    except Exception:
        return {}
    if meeting is None:
        return {}
    keep = meeting.speaker_names if isinstance(meeting.speaker_names, dict) else None
    people = meeting.speaker_people if isinstance(meeting.speaker_people, dict) else None
    try:
        from src.speakers.service import rebuild_speaker_names

        names = rebuild_speaker_names(people, keep=keep)
    except Exception:
        names = dict(keep or {})
    return {str(k): str(v).strip() for k, v in (names or {}).items() if str(v).strip()}


def apply_speaker_display_names(text: str, names: dict[str, str]) -> str:
    """Replace [spk:ID] with mapped display names (chat display only)."""
    out = text or ""
    for spk_id, name in sorted(names.items(), key=lambda kv: len(kv[0]), reverse=True):
        out = out.replace(f"[spk:{spk_id}]", name)
    return out


def format_speaker_mapping(meeting_id: str) -> str:
    """Speaker id → display name lines for ephemeral meeting chat context."""
    names = speaker_display_map(meeting_id)
    if not names:
        return "No speaker names configured for this meeting."
    lines = ["Current speaker mapping:"]
    for spk_id, name in names.items():
        lines.append(f"- {spk_id}: {name}")
    return "\n".join(lines)


def build_meeting_ephemeral_context(meeting_id: str) -> str:
    """Speakers + General summary. Not persisted; rebuilt every turn."""
    names = speaker_display_map(meeting_id)
    if names:
        parts = [
            "Current speaker mapping:\n"
            + "\n".join(f"- {spk_id}: {name}" for spk_id, name in names.items())
        ]
    else:
        parts = ["No speaker names configured for this meeting."]
    summary = load_general_summary_text(meeting_id)
    if summary:
        parts.append(
            "Meeting outline (General summary):\n\n"
            + apply_speaker_display_names(summary, names)
        )
    return "\n\n".join(parts)


def meeting_transcript_context_message(meeting_id: str) -> str:
    """Ephemeral system content for the transcript slot (always non-empty).

    Returns the formatted live transcript when available; otherwise the
    explicit unavailable notice so the model does not hallucinate a summary.
    """
    text = load_meeting_transcript_text(meeting_id)
    if text and text.strip():
        return text
    return MEETING_TRANSCRIPT_UNAVAILABLE
