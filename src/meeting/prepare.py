"""Pre-meeting brief: structured assembly + one-shot LLM synthesis.

Assembly (zero LLM): the group's last-meeting summary, open todos from the
group's meetings, attendee profiles and metadata. Synthesis: exactly one LLM
call over that compact context. Degradation is decided here, not in the
prompt: absent blocks are simply not rendered (no pre-selected attendees
means no attendee block; a missing last-meeting summary degrades to a
one-line note; no open todos means no todo block).
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone

from src.meeting.models import MeetingBrief
from src.prompts import MEETING_PREP_BRIEF_PROMPT

logger = logging.getLogger("meeting.prepare")

TODO_CAP = 5
SUMMARY_CLIP = 4000
_SPK_MARKER_RE = re.compile(r"\[spk:([A-Za-z0-9_\-]+)\]")

_BRIEF_GENERATING: set[str] = set()
_generating_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_llm():
    from src.meeting.service import _resolve_meeting_llm

    return _resolve_meeting_llm()


# ── todo adapter (cross-collection, source-meeting scoped) ─────────────


def _open_todos_for_meetings(meeting_ids: set[str]) -> list[dict]:
    """Open todos whose source meeting is in the set, across all collections."""
    if not meeting_ids:
        return []
    try:
        from src.collections.store import list_collections_meta
        from src.file_mgmt.todos import list_todos
    except Exception:
        logger.warning("todo module unavailable — brief todo block skipped", exc_info=True)
        return []
    rows: list[dict] = []
    for meta in list_collections_meta():
        try:
            todos = list_todos(meta.id, done=False)
        except Exception:
            continue
        for todo in todos:
            source = getattr(todo, "source_meeting_id", None)
            if not source or source not in meeting_ids:
                continue
            rows.append(
                {
                    "title": todo.title,
                    "assignee_person_id": getattr(todo, "assignee_person_id", None),
                    "source_meeting_id": source,
                    "created_at": todo.created_at,
                }
            )
    return rows


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def _age_days(created_at) -> int | None:
    stamp = _parse_ts(created_at)
    if stamp is None:
        return None
    return max(0, (datetime.now(timezone.utc) - stamp).days)


def _date_str(value: datetime) -> str:
    return value.strftime("%Y-%m-%d")


# ── context assembly (pure, no LLM) ────────────────────────────────────


def _latest_binding_meeting(person_id: str, meeting_ids: set[str] | None) -> dict | None:
    """Newest meeting (from the given subset, or any) where person has a binding."""
    from src.meeting.store import list_meetings

    best = None
    for meeting in list_meetings():
        if meeting_ids is not None and meeting.id not in meeting_ids:
            continue
        if person_id not in (meeting.speaker_people or {}).values():
            continue
        if best is None or meeting.created_at > best.created_at:
            best = meeting
    if best is None:
        return None
    return {"id": best.id, "title": best.title, "date": _date_str(best.created_at)}


def _resolve_spk_markers(text: str, meeting) -> str:
    """Replace [spk:N] markers with speaker names (summaries keep them raw).

    Slot ids differ by provider ("0", "1"… from DashScope realtime, "spk0"…
    elsewhere), so both key forms are tried.
    """
    try:
        from src.speakers.service import rebuild_speaker_names

        names = rebuild_speaker_names(meeting.speaker_people, keep=meeting.speaker_names) or {}
    except Exception:
        names = meeting.speaker_names or {}
    if not names:
        return text

    def _sub(match):
        slot = match.group(1)
        return names.get(slot) or names.get(f"spk{slot}") or match.group(0)

    return _SPK_MARKER_RE.sub(_sub, text or "")


def _meeting_summary_text(meeting) -> str:
    """General summary text: the Meeting.summary field when set, otherwise the
    general tab's section md (that is where the summarize pipeline writes it).
    Speaker markers are resolved to names for prompt cleanliness."""
    text = (getattr(meeting, "summary", None) or "").strip()
    if not text:
        from src.meeting.store import get_section_md

        tab_id = "tab_general"
        for tab in meeting.tabs or []:
            if isinstance(tab, dict) and tab.get("type") == "general" and tab.get("tab_id"):
                tab_id = str(tab["tab_id"])
                break
        try:
            text = (get_section_md(meeting.id, tab_id) or "").strip()
        except Exception:
            logger.debug("general tab md unavailable for %s", meeting.id, exc_info=True)
            text = ""
    return _resolve_spk_markers(text, meeting) if text else text


def build_brief_context(meeting_id: str) -> dict:
    """All structured inputs the brief synthesis consumes."""
    from src.meeting.group_store import groups_for_meeting
    from src.meeting.store import get_meeting, get_notes, list_meetings
    from src.speakers.profile import get_profile
    from src.speakers.store import get_person

    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")

    groups = groups_for_meeting(meeting_id)
    group = max(groups, key=lambda g: g.updated_at) if groups else None
    group_meeting_ids = (
        {m.meeting_id for m in group.members} - {meeting_id} if group else set()
    )

    last_meeting = None
    if group_meeting_ids:
        candidates = []
        for mid in group_meeting_ids:
            row = get_meeting(mid)
            if row is not None:
                candidates.append(row)
        if candidates:
            latest = max(candidates, key=lambda m: m.created_at)
            summary = _meeting_summary_text(latest)
            last_meeting = {
                "id": latest.id,
                "title": latest.title,
                "date": _date_str(latest.created_at),
                "gap_days": max(
                    0, (datetime.now(timezone.utc) - latest.created_at).days
                ),
                "has_summary": bool(summary),
                "summary_clip": summary[:SUMMARY_CLIP],
            }

    todo_rows = _open_todos_for_meetings(group_meeting_ids) if group_meeting_ids else []
    todo_rows.sort(key=lambda r: _parse_ts(r.get("created_at")) or datetime.max.replace(tzinfo=timezone.utc))
    titles_by_id = {m.id: m.title for m in list_meetings()}
    open_todos = []
    for row in todo_rows[:TODO_CAP]:
        open_todos.append(
            {
                "title": row["title"],
                "assignee_person_id": row.get("assignee_person_id"),
                "age_days": _age_days(row.get("created_at")),
                "source_meeting_title": titles_by_id.get(row.get("source_meeting_id"), ""),
            }
        )

    persons = []
    for pid in list(meeting.expected_people or []):
        person = get_person(pid)
        if person is None:
            continue
        profile = get_profile(pid)
        persons.append(
            {
                "person_id": pid,
                "name": person.display_name or "Unnamed",
                "last_together": _latest_binding_meeting(
                    pid, group_meeting_ids or None
                ),
                "open_todo_count": sum(
                    1 for r in todo_rows if r.get("assignee_person_id") == pid
                ),
                "profile_text": (profile.text if profile else "") or "",
            }
        )

    notes = get_notes(meeting_id) or ""
    return {
        "group": {"id": group.id, "title": group.title} if group else None,
        "last_meeting": last_meeting,
        "open_todos": open_todos,
        "persons": persons,
        "agenda": {"title": meeting.title or "", "text": notes.strip()},
    }


# ── prompt blocks ──────────────────────────────────────────────────────


def _agenda_block(agenda: dict) -> str:
    title = agenda.get("title", "")
    text = agenda.get("text", "")
    if not title and not text:
        return ""
    parts = []
    if title:
        parts.append(f"- meeting title: {title}")
    if text:
        parts.append(f"- agenda:\n{text[:2000]}")
    return "\n".join(parts)


def _recap_block(last_meeting: dict | None) -> str:
    if last_meeting is None:
        return ""
    head = (
        f'"{last_meeting["title"]}" on {last_meeting["date"]} '
        f'({last_meeting["gap_days"]} days ago)'
    )
    if not last_meeting["has_summary"]:
        return f"Last meeting (recap source):\n{head}\nSummary: not yet generated"
    return f"Last meeting (recap source):\n{head}\nSummary:\n{last_meeting['summary_clip']}"


def _todos_block(open_todos: list[dict]) -> str:
    if not open_todos:
        return ""
    lines = ["Open follow-ups (todos):"]
    for row in open_todos:
        age = f'{row["age_days"]} days' if row["age_days"] is not None else "unknown age"
        source = row.get("source_meeting_title") or ""
        lines.append(f'- {row["title"]} — open for {age} (from "{source}")')
    return "\n".join(lines)


def _persons_block(persons: list[dict]) -> str:
    if not persons:
        return ""
    chunks = []
    for p in persons:
        lines = [f"### {p['name']}"]
        if p.get("last_together"):
            lt = p["last_together"]
            lines.append(f'- last seen together: "{lt["title"]}" on {lt["date"]}')
        if p.get("open_todo_count"):
            lines.append(f'- open follow-ups owned: {p["open_todo_count"]}')
        profile = (p.get("profile_text") or "").strip()
        lines.append(f"- profile: {profile if profile else '(no profile yet)'}")
        chunks.append("\n".join(lines))
    return "\n".join(chunks)


def _build_prompt(ctx: dict, locale: str) -> str:
    return MEETING_PREP_BRIEF_PROMPT.format(
        locale=locale,
        agenda_block=_agenda_block(ctx["agenda"]),
        recap_block=_recap_block(ctx["last_meeting"]),
        todos_block=_todos_block(ctx["open_todos"]),
        persons_block=_persons_block(ctx["persons"]),
    )


# ── generation ─────────────────────────────────────────────────────────


def generate_brief(meeting_id: str, *, locale: str = "zh-CN", llm=None) -> dict:
    """Refresh dirty profiles, synthesize once, persist the brief on the meeting."""
    from src.meeting.store import get_meeting, update_meeting
    from src.speakers.profile import regenerate_profile
    from src.speakers.store import get_person

    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")
    person_ids = list(meeting.expected_people or [])

    # Lazy profile refresh: cached when clean (zero LLM), distilled when dirty.
    # Concurrent — one slow provider must not serialize everyone.
    from concurrent.futures import ThreadPoolExecutor

    valid_ids = [pid for pid in person_ids if get_person(pid) is not None]
    if valid_ids:
        with ThreadPoolExecutor(max_workers=min(4, len(valid_ids))) as pool:
            futures = [
                pool.submit(regenerate_profile, pid, llm=llm, locale=locale)
                for pid in valid_ids
            ]
            for pid, fut in zip(valid_ids, futures):
                try:
                    fut.result()
                except Exception:
                    logger.warning("profile refresh failed person=%s", pid, exc_info=True)

    ctx = build_brief_context(meeting_id)
    engine = llm if llm is not None else _resolve_llm()
    try:
        try:
            markdown = (engine.generate(_build_prompt(ctx, locale), thinking=False) or "").strip()
        except TypeError:
            # Custom/injected engines without the thinking kwarg.
            markdown = (engine.generate(_build_prompt(ctx, locale)) or "").strip()
    except Exception as exc:
        _save_error(meeting_id, str(exc))
        raise

    brief = MeetingBrief(
        state="ready",
        markdown=markdown,
        generated_at=_now(),
        group_id=ctx["group"]["id"] if ctx["group"] else None,
        group_title=ctx["group"]["title"] if ctx["group"] else "",
        person_ids=[p["person_id"] for p in ctx["persons"]],
        locale=locale,
    )
    update_meeting(meeting_id, brief=brief)
    return brief_state(meeting_id)


def _save_error(meeting_id: str, message: str) -> None:
    from src.meeting.store import get_meeting, update_meeting

    meeting = get_meeting(meeting_id)
    if meeting is None:
        return
    previous = meeting.brief
    brief = MeetingBrief(
        state="error",
        markdown=previous.markdown if previous else "",
        error=(message or "")[:300],
        generated_at=previous.generated_at if previous else "",
        group_id=previous.group_id if previous else None,
        group_title=previous.group_title if previous else "",
        person_ids=previous.person_ids if previous else [],
    )
    try:
        update_meeting(meeting_id, brief=brief)
    except Exception:
        logger.warning("failed to persist brief error state", exc_info=True)


# ── state + background kick ────────────────────────────────────────────


def brief_state(meeting_id: str) -> dict:
    from src.meeting.store import get_meeting

    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")
    with _generating_lock:
        generating = meeting_id in _BRIEF_GENERATING
    brief = meeting.brief
    if brief is None:
        return {
            "state": "generating" if generating else "none",
            "markdown": "",
            "error": None,
            "generated_at": "",
            "group_id": None,
            "group_title": "",
            "person_ids": [],
            "locale": "zh-CN",
        }
    data = brief.model_dump()
    if generating:
        data["state"] = "generating"
    return data


def start_brief_generate(meeting_id: str, *, locale: str = "zh-CN") -> dict:
    """Kick background brief generation; idempotent while running."""
    from src.meeting.store import get_meeting

    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")

    with _generating_lock:
        already = meeting_id in _BRIEF_GENERATING
        if not already:
            _BRIEF_GENERATING.add(meeting_id)

    def _run() -> None:
        try:
            generate_brief(meeting_id, locale=locale)
        except Exception as exc:
            logger.warning("brief generation failed meeting=%s", meeting_id, exc_info=True)
            _save_error(meeting_id, str(exc))
        finally:
            with _generating_lock:
                _BRIEF_GENERATING.discard(meeting_id)

    if not already:
        threading.Thread(
            target=_run,
            name=f"brief-gen-{meeting_id[:8]}",
            daemon=True,
        ).start()
    return brief_state(meeting_id)
