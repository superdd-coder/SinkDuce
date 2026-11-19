"""Apply current source permissions to persisted conversation turns before reuse."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace

from src.identity import get_actor, may_see_everything
from src.identity import visibility


_MEETING_TOOLS = {"list_meeting_catalog", "lookup_meeting_transcript", "read_meeting_summary"}


def _json_content(text):
    if isinstance(text, str) and text.lstrip().startswith(("{", "[")):
        try:
            return json.loads(text)
        except (ValueError, RecursionError):
            pass
    return None


@dataclass
class ChatSources:
    collections: set[str] = field(default_factory=set)
    domains: set[str] = field(default_factory=set)

    def include(self, value) -> None:
        """Collect structured identifiers from nested tool results, citations and saved metadata."""
        pending = [value]
        while pending:
            obj = pending.pop()
            if isinstance(obj, list):
                pending.extend(obj)
            elif isinstance(obj, dict):
                for key, child in obj.items():
                    if key in {"collection", "collection_id"} and isinstance(child, str) and child:
                        self.collections.add(child)
                    elif key in {"collections", "target_collections", "source_collections"} and isinstance(child, list):
                        self.collections.update(item for item in child if isinstance(item, str) and item)
                    elif key == "source_domains" and isinstance(child, list):
                        self.domains.update(item for item in child if isinstance(item, str) and item)
                    elif key in {"meeting_id", "meetings_searched", "meeting_ids"} and child:
                        self.domains.add("meeting")
                    elif isinstance(child, (dict, list)):
                        pending.append(child)

    def include_message(self, message) -> None:
        meta = message.metadata if isinstance(message.metadata, dict) else {}
        self.include([message.sources, meta])
        calls = meta.get("tool_calls")
        if isinstance(calls, list):
            for call in calls:
                fn = call.get("function") if isinstance(call, dict) else None
                if isinstance(fn, dict):
                    self.include(_json_content(fn.get("arguments")))
        if message.role == "tool":
            self.include(_json_content(message.content))

    def include_tool(self, name, args, content, sources, selected) -> dict:
        result = _json_content(content) if isinstance(content, str) else content
        used = ChatSources()
        used.include([sources, result])
        if name in _MEETING_TOOLS and isinstance(result, dict) and not result.get("error"):
            used.domains.add("meeting")
        # A model-supplied meeting ID alone does not mean we read that meeting.
        arguments = ChatSources()
        arguments.include(args)
        used.collections.update(arguments.collections)
        if name == "list_collections" and isinstance(result, list):
            used.collections.update(row["id"] for row in result if isinstance(row, dict)
                                    and isinstance(row.get("id"), str) and row["id"])
        if name in {"search_knowledge_base", "lookup_collection"} and not used.collections:
            used.collections.update(selected or [])
        self.collections.update(used.collections)
        self.domains.update(used.domains)
        return used.metadata()

    def metadata(self) -> dict:
        result = {}
        if self.collections:
            result["source_collections"] = sorted(self.collections)
        if self.domains:
            result["source_domains"] = sorted(self.domains)
        return result

    def require_current(self) -> None:
        actor = get_actor()
        if actor.kind == "key":
            from src.pairing.service import device_actor_is_active
            if not device_actor_is_active(actor):
                raise PermissionError("Device authorization expired")
        if may_see_everything(actor):
            return
        if self.collections:
            visible = visibility.visible_collection_ids(actor)
            if visible is not None and not self.collections <= visible:
                raise PermissionError("A conversation source is no longer available")
        if any(not visibility.can_access_domain(actor, domain) for domain in self.domains):
            raise PermissionError("A conversation source is no longer available")


def authorized_history(messages):
    """A hidden tool result and the answer derived from it stay out of the same turn."""
    from src.chatbox.qa_evidence import history_permitted

    actor = get_actor()
    if may_see_everything(actor) or not messages:
        return messages
    visible = visibility.visible_collection_ids(actor)
    domain_access = {}
    result = []
    turn = []
    permitted = True

    def flush():
        for message in turn:
            if permitted:
                result.append(message)
            elif message.role == "assistant" and message.content:
                result.append(replace(message, content="Source unavailable", sources=None,
                                      metadata={"source_unavailable": True}))
        turn.clear()

    for message in messages:
        if message.role == "user":
            flush()
            permitted = True
            result.append(message)
            continue
        meta = message.metadata if isinstance(message.metadata, dict) else {}
        calls = meta.get("tool_calls")
        if permitted:
            sources = ChatSources()
            sources.include_message(message)
            for domain in sources.domains - domain_access.keys():
                domain_access[domain] = visibility.can_access_domain(actor, domain)
            permitted = ((visible is None or sources.collections <= visible)
                         and all(domain_access[d] for d in sources.domains) and history_permitted(meta))
        turn.append(message)
        if message.role == "assistant" and message.content and not isinstance(calls, list):
            flush()
            permitted = True
    flush()
    return result
