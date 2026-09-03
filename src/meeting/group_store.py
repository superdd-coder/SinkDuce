"""JSON persistence for Meeting Groups (membership only; chat is sessions)."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.config import DATA_DIR

from .models import MeetingGroup, MeetingGroupMember

logger = logging.getLogger("meeting.group_store")
GROUPS_DIR = DATA_DIR / "meeting_groups"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _group_path(group_id: str) -> Path:
    from src.paths import assert_resource_id, confine

    assert_resource_id(group_id, name="group_id")
    GROUPS_DIR.mkdir(parents=True, exist_ok=True)
    return confine(GROUPS_DIR / f"{group_id}.json", GROUPS_DIR)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _to_dict(group: MeetingGroup) -> dict:
    data = group.model_dump()
    data["created_at"] = group.created_at.isoformat()
    data["updated_at"] = group.updated_at.isoformat()
    data["last_chat_at"] = group.last_chat_at.isoformat()
    return data


def _from_dict(data: dict) -> MeetingGroup:
    payload = dict(data)
    for key in ("created_at", "updated_at", "last_chat_at"):
        if key in payload:
            payload[key] = _parse_dt(payload[key])
    return MeetingGroup(**payload)


def _default_title(meeting_title: str, n: int) -> str:
    base = (meeting_title or "").strip() or "Untitled"
    return f"{base} 等 {n} 场"


def _save(group: MeetingGroup) -> MeetingGroup:
    group.updated_at = _now()
    _write_json(_group_path(group.id), _to_dict(group))
    return group


def create_group(
    *,
    title: str,
    meeting_id: str,
    meeting_title: str = "",
) -> MeetingGroup:
    mid = (meeting_id or "").strip()
    if not mid:
        raise ValueError("meeting_id is required")
    now = _now()
    name = (title or "").strip() or _default_title(meeting_title, 1)
    group = MeetingGroup(
        id=uuid.uuid4().hex,
        title=name,
        members=[MeetingGroupMember(meeting_id=mid, n=1)],
        created_at=now,
        updated_at=now,
        last_chat_at=now,
    )
    _write_json(_group_path(group.id), _to_dict(group))
    logger.info("Created meeting group id=%s title=%r", group.id, group.title)
    return group


def get_group(group_id: str) -> MeetingGroup | None:
    data = _read_json(_group_path(group_id))
    if data is None:
        return None
    return _from_dict(data)


def list_groups() -> list[MeetingGroup]:
    GROUPS_DIR.mkdir(parents=True, exist_ok=True)
    out: list[MeetingGroup] = []
    for path in GROUPS_DIR.glob("*.json"):
        data = _read_json(path)
        if not data:
            continue
        try:
            out.append(_from_dict(data))
        except Exception:
            logger.warning("skip bad group file %s", path, exc_info=True)
    out.sort(key=lambda g: g.last_chat_at or g.updated_at, reverse=True)
    return out


def groups_for_meeting(meeting_id: str) -> list[MeetingGroup]:
    mid = (meeting_id or "").strip()
    return [g for g in list_groups() if any(m.meeting_id == mid for m in g.members)]


def add_member(group_id: str, meeting_id: str) -> MeetingGroup:
    group = get_group(group_id)
    if group is None:
        raise FileNotFoundError(group_id)
    mid = (meeting_id or "").strip()
    if not mid:
        raise ValueError("meeting_id is required")
    if any(m.meeting_id == mid for m in group.members):
        return group
    next_n = max((m.n for m in group.members), default=0) + 1
    group.members.append(MeetingGroupMember(meeting_id=mid, n=next_n))
    return _save(group)


def remove_member(group_id: str, meeting_id: str) -> MeetingGroup:
    group = get_group(group_id)
    if group is None:
        raise FileNotFoundError(group_id)
    mid = (meeting_id or "").strip()
    group.members = [m for m in group.members if m.meeting_id != mid]
    return _save(group)


def drop_meeting_from_all_groups(meeting_id: str) -> None:
    mid = (meeting_id or "").strip()
    if not mid:
        return
    for group in list_groups():
        if any(m.meeting_id == mid for m in group.members):
            remove_member(group.id, mid)


def touch_chat(group_id: str) -> MeetingGroup:
    group = get_group(group_id)
    if group is None:
        raise FileNotFoundError(group_id)
    group.last_chat_at = _now()
    return _save(group)


def rename_group(group_id: str, title: str) -> MeetingGroup:
    group = get_group(group_id)
    if group is None:
        raise FileNotFoundError(group_id)
    name = (title or "").strip()
    if not name:
        raise ValueError("title is required")
    group.title = name
    return _save(group)


def set_group_archived(group_id: str, archived: bool) -> MeetingGroup:
    group = get_group(group_id)
    if group is None:
        raise FileNotFoundError(group_id)
    group.archived = bool(archived)
    return _save(group)


def delete_group(group_id: str) -> bool:
    path = _group_path(group_id)
    if not path.exists():
        return False
    path.unlink()
    return True
