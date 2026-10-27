"""Canonical successful Note writes attached to a single assistant turn."""

from __future__ import annotations

import json


def merge_note_changes(changes: list[dict], incoming: list[dict]) -> None:
    """Preserve first-write order while keeping the latest title per Note."""
    for note in incoming:
        key = (note["collection"], note["id"])
        for index, previous in enumerate(changes):
            if (previous["collection"], previous["id"]) == key:
                changes[index] = note
                break
        else:
            changes.append(note)


def note_references_from_history(messages: list, collections: list[str] | None) -> list[dict]:
    """Read canonical references for tool selection without changing reply text."""
    references: list[dict] = []
    for message in messages:
        if message.role != "assistant":
            continue
        changes = (message.metadata or {}).get("note_changes")
        if not isinstance(changes, list):
            continue
        for note in changes:
            if not isinstance(note, dict) or any(
                not isinstance(note.get(k), str) or not note[k] for k in ("id", "collection")
            ):
                continue
            if collections and note["collection"] not in collections:
                continue
            merge_note_changes(references, [{
                "id": note["id"], "collection": note["collection"],
                "title": str(note.get("title") or note["id"]),
            }])
    return references


def note_changes_from_result(tool: str, content: str | list) -> list[dict]:
    """Use full tool results, never the truncated preview or model prose."""
    if tool not in {"create_note", "update_note"} or not isinstance(content, str):
        return []
    try:
        payload = json.loads(content)
    except (ValueError, TypeError):
        return []
    if not isinstance(payload, dict) or "error" in payload:
        return []
    if any(not isinstance(payload.get(k), str) or not payload[k] for k in ("id", "collection")):
        return []
    return [{"id": payload["id"], "collection": payload["collection"], "title": str(payload.get("title") or payload["id"])}]
