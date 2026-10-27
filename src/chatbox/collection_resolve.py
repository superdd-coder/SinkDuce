"""Chat-side collection name→ID resolution for write tools (create_note).

Users say display names (often with typos or as prefixes), never IDs. The
agent passes the user's wording straight through; this module maps it to a
collection ID. On failure the error payload carries the full directory so
the next model round can self-correct without a ``list_collections`` call.

Match tiers (first hit wins):
1. ``id``    — exact collection ID
2. ``exact`` — case-insensitive full display-name match
3. ``prefix``— unique case-insensitive prefix of a display name
4. ``fuzzy`` — difflib close match (typo repair); callers must treat this
   as a guess and route it through the HITL confirm card
"""

from __future__ import annotations

import difflib
import logging
from typing import Any

logger = logging.getLogger(__name__)

_FUZZY_CUTOFF = 0.6


def build_collection_directory() -> list[dict[str, str]]:
    """All user-facing collections **visible to the current actor**.

    **可见性（2026-09-17）**：这个目录会**原样交给模型**——解析失败时整份目录作为
    错误载荷回传（"pick from the directory below"），所以它是最直接的枚举出口之一。
    用户第三次报"隐藏库还能在 Chat 里找出来"，根因就在这里：目录没过滤，模型拿到的
    是**全部库的 id + 显示名**，甚至能直接 resolve 到隐藏库。

    fail-closed：可见性判定失败返回**空目录**，绝不回落"不过滤"。
    """
    from src.collections.store import collection_display_name
    from src.meeting.transcript_index import INTERNAL_QDRANT_COLLECTIONS
    from src.services import services

    try:
        from src.identity import get_actor
        from src.identity import visibility as _vis

        visible = _vis.visible_collection_ids(get_actor())
    except Exception:
        logger.exception("collection directory visibility gate failed — empty directory")
        return []

    out: list[dict[str, str]] = []
    for cid in services.db.list_collections():
        if cid in INTERNAL_QDRANT_COLLECTIONS:
            continue
        if visible is not None and cid not in visible:
            continue
        out.append({"id": cid, "name": collection_display_name(cid)})
    return out


def _directory_payload(directory: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{"id": c["id"], "name": c["name"]} for c in directory]


def resolve_collection(raw: str) -> dict[str, Any]:
    """Resolve a user-said collection name (or ID) against the directory.

    Returns ``{"collection", "collection_name", "match"}`` on success or
    ``{"error", "collections"}`` (full directory attached) on failure.
    """
    query = str(raw or "").strip()
    directory = build_collection_directory()

    if not query:
        return {
            "error": (
                "No target collection given. Ask the user which collection "
                "to write into, or pass the collection name they used."
            ),
            "collections": _directory_payload(directory),
        }

    lowered = query.casefold()
    by_id = {c["id"]: c for c in directory}

    # 1. Exact ID (IDs are canonical — check before name tiers).
    if query in by_id:
        entry = by_id[query]
        return {
            "collection": entry["id"],
            "collection_name": entry["name"],
            "match": "id",
        }

    # 2. Exact display name (case-insensitive).
    name_hits = [c for c in directory if c["name"].casefold() == lowered]
    if len(name_hits) == 1:
        return {
            "collection": name_hits[0]["id"],
            "collection_name": name_hits[0]["name"],
            "match": "exact",
        }
    if len(name_hits) > 1:
        return {
            "error": (
                f"Collection name '{query}' matches {len(name_hits)} "
                "collections. Ask the user which one they mean."
            ),
            "collections": _directory_payload(name_hits),
        }

    # 3. Unique display-name prefix.
    prefix_hits = [c for c in directory if c["name"].casefold().startswith(lowered)]
    if len(prefix_hits) == 1:
        return {
            "collection": prefix_hits[0]["id"],
            "collection_name": prefix_hits[0]["name"],
            "match": "prefix",
        }
    if len(prefix_hits) > 1:
        names = ", ".join(sorted(c["name"] for c in prefix_hits))
        return {
            "error": (
                f"Collection name '{query}' is a prefix of several "
                f"collections ({names}). Ask the user which one they mean."
            ),
            "collections": _directory_payload(prefix_hits),
        }

    # 4. Fuzzy repair for typos (HITL confirm is mandatory downstream).
    names = [c["name"] for c in directory if c["name"]]
    close = difflib.get_close_matches(query, names, n=2, cutoff=_FUZZY_CUTOFF)
    if close:
        best = close[0]
        tied = [c for c in directory if c["name"] == best]
        if len(tied) == 1:
            logger.info(
                "Collection resolve: fuzzy %r → %r", query, tied[0]["name"]
            )
            return {
                "collection": tied[0]["id"],
                "collection_name": tied[0]["name"],
                "match": "fuzzy",
            }

    return {
        "error": (
            f"No collection matches '{query}'. Resolve conversational "
            "references from chat history to an actual collection name, "
            "pick from the directory below, or ask the user."
        ),
        "collections": _directory_payload(directory),
    }
