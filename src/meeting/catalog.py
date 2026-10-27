"""Visible meeting catalog + lookup targeting for Chat / QC / Group / MCP."""

from __future__ import annotations

from typing import Any


def is_archived(meeting: Any) -> bool:
    """Strict archived check — `is True` so mock/test doubles never match."""
    return getattr(meeting, "archived", False) is True


def _all_collections_archived(meeting: Any) -> bool:
    """True iff the meeting is allocated and every target collection is archived.

    Multi-collection meetings stay visible while any member collection is
    active; deleted collections don't count as archived. Pure function of the
    current archive state — unarchiving restores visibility automatically.
    """
    cols = [str(c).strip() for c in (getattr(meeting, "allocated_collections", None) or []) if str(c).strip()]
    if not cols:
        return False
    try:
        from src.collections.store import archived_collection_ids

        return set(cols) <= archived_collection_ids()
    except Exception:
        return False


def ingested_meeting_ids(collection_id: str) -> list[str]:
    from src.meeting.store import list_meetings

    col = (collection_id or "").strip()
    if not col:
        return []
    out: list[str] = []
    for m in list_meetings():
        if is_archived(m):
            continue
        cols = list(getattr(m, "allocated_collections", None) or [])
        if col in cols:
            out.append(m.id)
    return out


def ingested_meeting_ids_many(collection_ids: list[str]) -> list[str]:
    wanted = {str(c).strip() for c in collection_ids if str(c).strip()}
    if not wanted:
        return []
    from src.meeting.store import list_meetings

    out: list[str] = []
    for m in list_meetings():
        if is_archived(m):
            continue
        cols = list(getattr(m, "allocated_collections", None) or [])
        if wanted.intersection(str(c) for c in cols):
            out.append(m.id)
    return out


def visible_meeting_ids(
    *,
    collection: str | None = None,
    collections: list[str] | None = None,
    group_id: str | None = None,
    meeting_id: str | None = None,
) -> list[str]:
    """Meetings this surface may list or search (archived ones are excluded).

    **RBAC 前置闸（2026-09-16）**：下面的"surface 可见性"是**会议↔库关联**的既有
    概念（某库能看到哪些已入库会议），与身份权限是两回事。这里先过身份：没有
    会议域 read 档的 actor（如 visitor）一律拿不到任何会议 id —— 于是所有会议工具
    （catalog / lookup_transcript / read_summary）自动为空，无需逐个改。
    """
    from src.identity import get_actor, may_see_everything
    from src.identity import acl as _acl

    try:
        _actor = get_actor()
        if not may_see_everything(_actor) and not _acl.domain_allows(
            _acl.DOMAIN_MEETING, "read", _actor.role_id
        ):
            return []
    except Exception:
        import logging as _logging

        _logging.getLogger("meeting.catalog").exception(
            "RBAC gate failed in visible_meeting_ids — returning empty"
        )
        return []

    from src.meeting.store import get_meeting, list_meetings

    mid = (meeting_id or "").strip()
    if mid:
        meeting = get_meeting(mid)
        if meeting is None or is_archived(meeting):
            return []
        return [mid]
    gid = (group_id or "").strip()
    if gid:
        from src.meeting.group_store import get_group

        group = get_group(gid)
        if group is None:
            return []
        out = []
        for m in group.members:
            meeting = get_meeting(m.meeting_id)
            if meeting is not None and not is_archived(meeting):
                out.append(m.meeting_id)
        return out
    selected = [str(c).strip() for c in (collections or []) if str(c).strip()]
    one = (collection or "").strip()
    if one:
        if selected and one not in selected:
            return []
        return ingested_meeting_ids(one)
    if selected:
        return ingested_meeting_ids_many(selected)
    return [
        m.id for m in list_meetings()
        if not is_archived(m) and not _all_collections_archived(m)
    ]


def select_lookup_targets(
    visible: list[str],
    requested: list[str] | None,
) -> tuple[list[str] | None, str | None]:
    vis = [str(x).strip() for x in visible if str(x).strip()]
    vis_set = set(vis)
    req = [str(x).strip() for x in (requested or []) if str(x).strip()]
    if not req:
        return list(vis), None
    unknown = [x for x in req if x not in vis_set]
    if unknown:
        return None, "meeting_id not visible: " + ", ".join(unknown)
    return req, None


def _summary_head(meeting_id: str, limit: int = 200) -> str:
    """First paragraph of the General summary (routing aid, not evidence)."""
    from src.chatbox.meeting_context import load_general_summary_text

    text = load_general_summary_text(meeting_id)
    if not text:
        return ""
    head = text.strip().split("\n", 1)[0].strip()
    if len(head) > limit:
        head = head[: limit - 1].rstrip() + "…"
    return head


def catalog_rows(
    visible_ids: list[str],
    *,
    group_n: dict[str, int] | None = None,
    include_summary_head: bool = False,
) -> list[dict[str, Any]]:
    from src.meeting.store import get_meeting

    rows: list[dict[str, Any]] = []
    nmap = group_n or {}
    for mid in visible_ids:
        m = get_meeting(mid)
        title = (m.title if m else None) or mid
        date = ""
        if m is not None and m.created_at is not None:
            date = m.created_at.date().isoformat()
        ready = bool(m and (m.transcript_index_status or "") == "ready")
        row: dict[str, Any] = {
            "meeting_id": mid,
            "title": title,
            "date": date,
            "index_ready": ready,
        }
        if include_summary_head:
            head = _summary_head(mid)
            if head:
                row["summary_head"] = head
        if mid in nmap:
            row["n"] = nmap[mid]
        rows.append(row)
    return rows


def group_n_map(group_id: str | None) -> dict[str, int]:
    gid = (group_id or "").strip()
    if not gid:
        return {}
    from src.meeting.group_store import get_group

    group = get_group(gid)
    if group is None:
        return {}
    return {m.meeting_id: m.n for m in group.members}


def catalog_tool_json(
    *,
    collection: str | None = None,
    collections: list[str] | None = None,
    group_id: str | None = None,
) -> str:
    import json

    ids = visible_meeting_ids(
        collection=collection, collections=collections, group_id=group_id
    )
    rows = catalog_rows(
        ids,
        group_n=group_n_map(group_id) or None,
        include_summary_head=True,
    )
    return json.dumps({"meetings": rows, "count": len(rows)}, ensure_ascii=False)


def lookup_tool_json(
    query: str,
    *,
    meeting_ids: list[str] | None = None,
    speaker_scope: str = "auto",
    user_question: str = "",
    collection: str | None = None,
    collections: list[str] | None = None,
    group_id: str | None = None,
    meeting_id: str | None = None,
    seen_pack_keys: set[str] | None = None,
) -> str:
    raw, _found, _hits = lookup_tool_json_and_keys(
        query,
        meeting_ids=meeting_ids,
        speaker_scope=speaker_scope,
        user_question=user_question,
        collection=collection,
        collections=collections,
        group_id=group_id,
        meeting_id=meeting_id,
        seen_pack_keys=seen_pack_keys,
    )
    return raw


def lookup_tool_json_and_keys(
    query: str,
    *,
    meeting_ids: list[str] | None = None,
    speaker_scope: str = "auto",
    user_question: str = "",
    collection: str | None = None,
    collections: list[str] | None = None,
    group_id: str | None = None,
    meeting_id: str | None = None,
    seen_pack_keys: set[str] | None = None,
    prior_hits: list[dict] | None = None,
) -> tuple[str, set[str], list[dict]]:
    import json

    from src.meeting.transcript_index import execute_meetings_lookup_json_and_keys

    visible = visible_meeting_ids(
        collection=collection,
        collections=collections,
        group_id=group_id,
        meeting_id=meeting_id,
    )
    req = [str(x).strip() for x in (meeting_ids or []) if str(x).strip()]
    target, err = select_lookup_targets(visible, req or None)
    if err:
        return (
            json.dumps(
                {"error": err, "context": "", "hit_count": 0},
                ensure_ascii=False,
            ),
            set(),
            [],
        )
    nmap = group_n_map(group_id)
    return execute_meetings_lookup_json_and_keys(
        target or [],
        query,
        speaker_scope=speaker_scope,
        user_question=user_question,
        group_n=nmap or None,
        group_id=(group_id or "").strip() or None,
        seen_pack_keys=seen_pack_keys,
        prior_hits=prior_hits,
    )


def read_meeting_summary_json(
    meeting_id: str,
    *,
    collection: str | None = None,
    collections: list[str] | None = None,
    group_id: str | None = None,
) -> str:
    """General summary for one visible meeting."""
    import json

    mid = (meeting_id or "").strip()
    if not mid:
        return json.dumps({"error": "meeting_id is required"})
    visible = visible_meeting_ids(
        collection=collection, collections=collections, group_id=group_id
    )
    if mid not in set(visible):
        return json.dumps({"error": f"meeting_id not visible: {mid}"})
    from src.chatbox.meeting_context import (
        apply_speaker_display_names,
        load_general_summary_text,
        speaker_display_map,
    )

    names = speaker_display_map(mid)
    text = apply_speaker_display_names(load_general_summary_text(mid), names)
    row: dict[str, Any] = {"meeting_id": mid, "summary": text or ""}
    nmap = group_n_map(group_id)
    if mid in nmap:
        row["n"] = nmap[mid]
    if not text:
        row["message"] = "No General summary is available for this meeting yet."
    return json.dumps(row, ensure_ascii=False)


def lookup_hits_to_chat_sources(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """One Sources row per meeting (Chat / Collection Quick Chat)."""
    titles = payload.get("meeting_titles") if isinstance(payload, dict) else None
    if not isinstance(titles, dict):
        titles = {}
    seen: list[str] = []
    raw = payload.get("meetings_searched") if isinstance(payload, dict) else None
    if isinstance(raw, list):
        for item in raw:
            mid = str(item or "").strip()
            if mid and mid not in seen:
                seen.append(mid)
    out: list[dict[str, Any]] = []
    for mid in seen:
        label = str(titles.get(mid) or mid)
        out.append(
            {
                "text": label,
                "score": 1.0,
                "metadata": {
                    "id": mid,
                    "source_type": "meeting",
                    "meeting_id": mid,
                    "source_label": label,
                    "source": f"__meeting__:{mid}",
                },
            }
        )
    return out
