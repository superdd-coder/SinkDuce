"""Extract structured todo candidates from Section Summary Markdown.

Design evolution (2026-08-12 → single-shot LLM):
  1. Build the same snapshot used for section library ingest
     (resolve speaker display names, strip stt_ref tags).
  2. One LLM call → short title, body/description, priority, ddl.
  3. Deterministic bullet parse remains as offline / LLM-failure fallback.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_PRIORITY_RE = re.compile(
    r"\s*\[priority:\s*(high|medium|low)\s*\]\s*$",
    re.IGNORECASE,
)
_SPK_RE = re.compile(r"^\[spk:([^\]]+)\]\s*", re.IGNORECASE)
_BULLET_RE = re.compile(r"^[-*+]\s+(.+)$")
_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
# Explicit title — detail separator (em dash / en dash / " | " / " - ")
_TITLE_DETAIL_RE = re.compile(
    r"\s+(?:—|–|\|)\s+|\s+-\s+",
)
# Soft caps for checklist title (detail goes to body/description).
# Slightly roomy so "Name: verb …" fits without chopping the person.
_TITLE_MAX_WORDS = 12
_TITLE_MAX_CHARS = 72

_STT_BRACKET_RE = re.compile(
    r"\[(?:ref:)?\s*(?:stt_\d+(?:\s*[-–]\s*\d+)?"
    r"(?:\s*,\s*stt_\d+(?:\s*[-–]\s*\d+)?)*)\s*\]",
)
_STT_BARE_RE = re.compile(r"\bstt_\d{4}\b")


def candidate_id_for(
    title: str,
    assignee_label: str | None = None,
    priority: str | None = None,
) -> str:
    """Stable id from normalized title + assignee + priority."""
    key = "|".join(
        [
            (title or "").strip().lower(),
            (assignee_label or "").strip().lower(),
            (priority or "").strip().lower(),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def prepare_section_todo_snapshot(
    md: str,
    speaker_names: dict[str, str] | None = None,
) -> str:
    """Same text transform as section library ingest (allocate snapshot).

    - Replace ``[spk:ID]`` / ``Speaker ID`` with display names
    - Strip ``[stt_XXXX]`` / bare ``stt_XXXX`` ref tags
    - Collapse excess blank lines
    """
    content = (md or "").replace("\r\n", "\n")
    names = speaker_names or {}
    for spk_id, name in names.items():
        if not name:
            continue
        content = content.replace(f"[spk:{spk_id}]", str(name))
        content = re.sub(
            rf"\bSpeaker {re.escape(str(spk_id))}\b", str(name), content
        )
    content = _STT_BRACKET_RE.sub("", content)
    content = _STT_BARE_RE.sub("", content)
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip()


def parse_section_todo_candidates(
    md: str,
    *,
    speaker_names: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Parse ``## Todo`` bullets into candidate dicts (no LLM).

    Prefer :func:`extract_todo_candidates` which uses LLM when available.
    This remains the offline / failure fallback.

    Returns list of::

        {
          candidate_id, title, body, assignee_label, priority,
          ddl, raw_line
        }
    """
    # Keep [spk:ID] for assignee parse; only strip stt noise.
    # (LLM path uses prepare_section_todo_snapshot instead.)
    content = (md or "").replace("\r\n", "\n")
    content = _STT_BRACKET_RE.sub("", content)
    content = _STT_BARE_RE.sub("", content)
    if not content.strip():
        return []

    names = speaker_names or {}
    lines = content.split("\n")
    in_todo = False
    bullets: list[str] = []

    for line in lines:
        h2 = _H2_RE.match(line.strip())
        if h2:
            heading = h2.group(1).strip().lower()
            if heading in {
                "todo",
                "todos",
                "to-do",
                "to-dos",
                "to do",
                "action items",
                "action item",
                "待办",
                "待办事项",
                "行动项",
            } or heading.startswith("todo"):
                in_todo = True
                continue
            if in_todo:
                break
            continue
        if not in_todo:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        m = _BULLET_RE.match(stripped)
        if m:
            bullets.append(m.group(1).strip())

    out: list[dict[str, Any]] = []
    for raw in bullets:
        item = _parse_bullet(raw, names)
        if item:
            out.append(item)
    return out


def extract_todo_candidates(
    md: str,
    *,
    speaker_names: dict[str, str] | None = None,
    meeting_created_at: datetime | str | None = None,
    llm: Any | None = None,
    use_llm: bool = True,
) -> list[dict[str, Any]]:
    """Primary extract: ingest snapshot → one LLM call (title/body/priority/ddl).

    Falls back to deterministic ``## Todo`` parse when *use_llm* is False,
    *llm* is missing, or the LLM call fails / returns empty.
    """
    snapshot = prepare_section_todo_snapshot(md, speaker_names)
    if not snapshot:
        return []

    if use_llm and llm is not None:
        try:
            items = extract_todo_candidates_llm(
                snapshot,
                meeting_created_at=meeting_created_at,
                llm=llm,
            )
            if items:
                return items
            logger.info("LLM todo extract returned empty; falling back to parse")
        except Exception:
            logger.warning(
                "LLM todo extract failed; falling back to parse",
                exc_info=True,
            )

    return parse_section_todo_candidates(
        snapshot, speaker_names=speaker_names
    )


def extract_todo_candidates_llm(
    section_snapshot: str,
    *,
    meeting_created_at: datetime | str | None,
    llm: Any,
) -> list[dict[str, Any]]:
    """Single LLM call → structured candidates. Raises on hard failures."""
    from src.prompts import (
        MEETING_TODO_EXTRACT_SYSTEM_PROMPT,
        MEETING_TODO_EXTRACT_USER_PROMPT,
    )

    if isinstance(meeting_created_at, datetime):
        anchor = meeting_created_at.isoformat()
    else:
        anchor = (meeting_created_at or "").strip() or "unknown"

    user = MEETING_TODO_EXTRACT_USER_PROMPT.format(
        meeting_created_at=anchor,
        section_snapshot=(section_snapshot or "")[:14000],
    )

    # Structured JSON extract only — never use meeting_thinking / default
    # thinking. thinking=None leaves provider defaults on (e.g. DeepSeek),
    # which can burn 30–60s and starve concurrent enrich calls on the same
    # model endpoint. Cap tokens so a hung think loop cannot run unbounded.
    gen_kwargs = dict(
        response_format={"type": "json_object"},
        max_tokens=2048,
        thinking=False,
    )
    if hasattr(llm, "generate"):
        raw = llm.generate(
            user,
            system=MEETING_TODO_EXTRACT_SYSTEM_PROMPT,
            **gen_kwargs,
        )
    elif hasattr(llm, "chat"):
        raw = llm.chat(
            [
                {"role": "system", "content": MEETING_TODO_EXTRACT_SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            **gen_kwargs,
        )
    else:
        raise TypeError("llm has neither generate nor chat")

    text = raw if isinstance(raw, str) else str(raw)
    data = _extract_json(text)
    rows = data if isinstance(data, list) else (data.get("items") or [])
    if not isinstance(rows, list):
        return []

    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        # Soft-cap title even if model is verbose
        title, extra = _soft_cap_title(title)
        body = row.get("body")
        if body is not None:
            body = str(body).strip() or None
        if extra:
            body = f"{extra} {body}".strip() if body else extra

        priority = row.get("priority")
        if priority is not None:
            p = str(priority).strip().lower()
            priority = p if p in ("high", "medium", "low") else None
        else:
            priority = None

        ddl = row.get("ddl")
        if ddl is None or (isinstance(ddl, str) and not str(ddl).strip()):
            ddl = None
        else:
            ddl = str(ddl).strip()

        assignee_label = row.get("assignee_label")
        if assignee_label is not None:
            assignee_label = str(assignee_label).strip() or None

        # Never drop a known person name: if model put it only in
        # assignee_label, fold into title (or body if title already long).
        title, body = _ensure_name_visible(title, body, assignee_label)

        cid = candidate_id_for(title, assignee_label, priority)
        out.append(
            {
                "candidate_id": cid,
                "title": title,
                "body": body,
                "assignee_label": assignee_label,
                "priority": priority,
                "ddl": ddl,
                "raw_line": title,
            }
        )
    return out


def _name_in_text(name: str, *parts: str | None) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return True
    blob = " ".join(p for p in parts if p).lower()
    return n in blob


def _ensure_name_visible(
    title: str,
    body: str | None,
    assignee_label: str | None,
) -> tuple[str, str | None]:
    """Keep assignee display name in title/body so UI does not lose it."""
    name = (assignee_label or "").strip()
    if not name:
        return title, body
    if _name_in_text(name, title, body):
        return title, body

    # Prefer short "Name: title" when it still fits soft title length.
    prefixed = f"{name}: {title}".strip()
    if (
        len(prefixed) <= _TITLE_MAX_CHARS + 24
        and len(prefixed.split()) <= _TITLE_MAX_WORDS + 4
    ):
        return prefixed, body

    note = f"Owner: {name}"
    if body:
        return title, f"{note}. {body}".strip()
    return title, note


def _split_title_and_detail(text: str) -> tuple[str, str | None]:
    """Return (title, detail) using explicit separator or length soft-cap."""
    s = (text or "").strip()
    if not s:
        return "", None

    m = _TITLE_DETAIL_RE.search(s)
    if m:
        title = s[: m.start()].strip()
        detail = s[m.end() :].strip() or None
        if title:
            title, extra = _soft_cap_title(title)
            if extra:
                detail = f"{extra} {detail}".strip() if detail else extra
            return title, detail

    return _soft_cap_title(s)


def _soft_cap_title(text: str) -> tuple[str, str | None]:
    """If *text* is long, cut a short title and put the rest in detail."""
    s = (text or "").strip()
    if not s:
        return "", None

    words = s.split()
    for sep in ("; ", ". ", "：", ": "):
        if sep not in s:
            continue
        head, tail = s.split(sep, 1)
        head = head.strip()
        tail = tail.strip()
        if not head or not tail:
            continue
        # "Alice: prepare budget" — short head is a person label, keep full
        # title so names are not stripped into body-only.
        if sep in (": ", "：") and len(head.split()) <= 3 and len(head) <= 32:
            continue
        if (
            len(head) <= _TITLE_MAX_CHARS
            and len(head.split()) <= _TITLE_MAX_WORDS
        ):
            return head, tail or None

    if len(words) <= _TITLE_MAX_WORDS and len(s) <= _TITLE_MAX_CHARS:
        return s, None

    cut_n = min(_TITLE_MAX_WORDS, len(words))
    title_words = words[:cut_n]
    title = " ".join(title_words)
    while len(title) > _TITLE_MAX_CHARS and len(title_words) > 3:
        title_words = title_words[:-1]
        title = " ".join(title_words)
    rest = " ".join(words[len(title_words) :]).strip()
    if not rest:
        return title, None
    return title, rest


def _parse_bullet(raw: str, speaker_names: dict[str, str]) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None

    priority: str | None = None
    pm = _PRIORITY_RE.search(text)
    if pm:
        priority = pm.group(1).lower()
        text = text[: pm.start()].rstrip()

    assignee_label: str | None = None
    sm = _SPK_RE.match(text)
    if sm:
        spk_id = sm.group(1).strip()
        text = text[sm.end() :].strip()
        if text.lower().startswith("to "):
            text = text[3:].strip()
        assignee_label = speaker_names.get(spk_id) or speaker_names.get(
            str(spk_id)
        )
        if not assignee_label:
            assignee_label = f"[spk:{spk_id}]"
    else:
        to_split = re.split(r"\s+to\s+", text, maxsplit=1)
        if len(to_split) == 2 and 1 <= len(to_split[0].split()) <= 4:
            maybe_name = to_split[0].strip()
            if maybe_name and not maybe_name[0].isdigit():
                assignee_label = maybe_name
                text = to_split[1].strip()

    text = text.strip()
    if not text:
        return None
    if text.lower().startswith("to "):
        text = text[3:].strip()
    if not text:
        return None

    title, detail = _split_title_and_detail(text)
    title = (title or "").strip()
    if not title:
        return None

    body = (detail or "").strip() or None

    cid = candidate_id_for(title, assignee_label, priority)
    return {
        "candidate_id": cid,
        "title": title,
        "body": body,
        "assignee_label": assignee_label,
        "priority": priority,
        "ddl": None,
        "raw_line": raw.strip(),
    }


def enrich_candidates_ddl(
    candidates: list[dict[str, Any]],
    *,
    full_section_md: str,
    meeting_created_at: datetime | str | None,
    llm: Any | None = None,
) -> list[dict[str, Any]]:
    """Deprecated: DDL is folded into :func:`extract_todo_candidates_llm`.

    Kept for callers that still pass pre-parsed lists — no-op when ddl
    already set; otherwise leaves ddl unchanged (prefer full re-extract).
    """
    del full_section_md, meeting_created_at, llm  # unused
    return candidates


def _extract_json(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if m:
            return json.loads(m.group(1).strip())
        for i, ch in enumerate(text):
            if ch in "{[":
                return json.loads(text[i:])
        raise
