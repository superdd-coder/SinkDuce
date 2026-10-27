"""MCP Hot Words Library management tools.

5 atomic tools wrapping ``src.hot_words`` (transcription hot-words libraries):

- :func:`list_hot_words_libraries` — list libraries (lightweight, no words)
- :func:`get_hot_words_library` — full library (with words array)
- :func:`create_hot_words_library` — create empty library
- :func:`update_hot_words_library` — update name / description / words
- :func:`delete_hot_words_library` — delete a library

Hot words are used to bias transcription accuracy (e.g. project-specific
jargon, people's names). Libraries can be attached to a meeting via
``update_meeting(hot_words_library_id=...)``.
"""

from __future__ import annotations

from mcp.server.fastmcp import Context

import logging
from typing import Any

from src.identity.acl import DOMAIN_HOT_WORDS
from src.hub.adapters import envelope, total_of
from src.hub.routing import body_without, hub_routed
from src.mcp.common import domain_gated, err, ok, run_sync, to_json
from src.hub.confirm import confirm_destructive

logger = logging.getLogger(__name__)


# ── list_hot_words_libraries ───────────────────────────────────


@domain_gated(DOMAIN_HOT_WORDS)
@hub_routed(
    method="GET",
    path="/hot-words",
    shape=envelope("libraries", extra=total_of("libraries")),
    forwards=(),
)
async def list_hot_words_libraries(ctx: Context | None = None) -> str:
    """List all hot-words libraries (sorted by mtime descending).

    Returns lightweight metadata only (``id``, ``name``, ``description``,
    ``word_count``, timestamps). Use :func:`get_hot_words_library` to fetch
    the actual word list.
    """
    def _run() -> dict[str, Any]:
        from src.hot_words import store as hstore

        libs = hstore.list_libraries()
        pins = hstore.get_pinned_library_ids()
        items = [
            hstore.library_summary(lib, pinned_ids=pins)
            for lib in libs
        ]
        return ok(libraries=items, total=len(items))

    return to_json(await run_sync(_run))


# ── get_hot_words_library ──────────────────────────────────────


@domain_gated(DOMAIN_HOT_WORDS)
@hub_routed(
    method="GET",
    path="/hot-words/{library_id}",
    # REST 是超集（多 is_default / is_pinned / is_system / word_count），保留
    forwards=("library_id",),
)
async def get_hot_words_library(library_id: str, ctx: Context | None = None) -> str:
    """Get a full hot-words library including its words.

    Each word entry has ``text``, ``weight`` (1-10), and ``lang`` (e.g.
    ``"zh"``, ``"en"``).
    """
    def _run() -> dict[str, Any]:
        from src.hot_words import store as hstore

        lib = hstore.get_library(library_id)
        if lib is None:
            return err(f"Hot-words library '{library_id}' not found")
        return lib.model_dump()

    return to_json(await run_sync(_run))


# ── create_hot_words_library ───────────────────────────────────


@domain_gated(DOMAIN_HOT_WORDS)
@hub_routed(
    method="POST",
    path="/hot-words",
    # REST 的 _public_lib = model_dump + library_summary —— 工具本地回的
    # lib.model_dump() 的超集，透传（良性多字段，批次 1 同判例）。
    # 空 name：REST 400 "Name is required" → 结构化 error，与本地 err 同语义。
    body=dict,
    forwards=("name", "description"),
)
async def create_hot_words_library(
    name: str,
    description: str = "",
    ctx: Context | None = None,
) -> str:
    """Create a new empty hot-words library.

    Use :func:`update_hot_words_library` next to add words.
    """
    def _run() -> dict[str, Any]:
        from src.hot_words import store as hstore

        final_name = name.strip()
        if not final_name:
            return err("Name is required")
        lib = hstore.create_library(name=final_name, description=description)
        return lib.model_dump()

    return to_json(await run_sync(_run))


# ── update_hot_words_library ───────────────────────────────────


def _hw_update_refuse(args: dict[str, Any]) -> str | None:
    """复刻本地实现的两道输入校验（REST 都不做，会静默成功/500）。"""
    if args.get("name") is None and args.get("description") is None and args.get("words") is None:
        # REST 的 PUT 收到空 body → store.update_library() 空更新 → **成功返回**。
        # 本地是明确报错。静默成功就是「看起来对、其实没改」的形态。
        return "Provide at least one of 'name', 'description', or 'words'"
    words = args.get("words")
    if isinstance(words, list):
        for i, w in enumerate(words):
            if not isinstance(w, dict):
                return f"words[{i}] must be a dict, got {type(w).__name__}"
            if "text" not in w or not isinstance(w["text"], str):
                return f"words[{i}].text is required and must be a string"
    return None


@domain_gated(DOMAIN_HOT_WORDS)
@hub_routed(
    method="PUT",
    path="/hot-words/{library_id}",
    # body 必须剔除 library_id —— REST 是 store.update_library(id, **body) 解包，
    # body 里再带一次就是 "got multiple values" → 500（真机实测）。
    body=body_without("library_id"),
    refuse=_hw_update_refuse,
    # REST 回 _public_lib（超集），404/403 由 REST 报 → 透传。
    forwards=("library_id", "name", "description", "words"),
)
async def update_hot_words_library(
    library_id: str,
    name: str | None = None,
    description: str | None = None,
    words: list[dict[str, Any]] | None = None,
    ctx: Context | None = None,
) -> str:
    """Update a hot-words library.

    All fields are optional — pass only the ones you want to change.

    Args:
        library_id: Target library.
        name: New library name.
        description: New description.
        words: Replace the entire word list. Each entry must be a dict with
            ``text`` (str), ``weight`` (int 1-10), and ``lang`` (str).
            Pass ``[]`` to clear all words.

    Example word entry::

        {"text": "Project X", "weight": 6, "lang": "en"}
    """
    def _run() -> dict[str, Any]:
        from src.hot_words import store as hstore

        kwargs: dict[str, Any] = {}
        if name is not None:
            kwargs["name"] = name
        if description is not None:
            kwargs["description"] = description
        if words is not None:
            # Validate each word entry shape
            for i, w in enumerate(words):
                if not isinstance(w, dict):
                    return err(
                        f"words[{i}] must be a dict, got {type(w).__name__}"
                    )
                if "text" not in w or not isinstance(w["text"], str):
                    return err(f"words[{i}].text is required and must be a string")
                # weight/lang defaults are filled by the model
            kwargs["words"] = words

        if not kwargs:
            return err("Provide at least one of 'name', 'description', or 'words'")

        try:
            lib = hstore.update_library(library_id, **kwargs)
        except PermissionError as exc:
            return err(str(exc))
        except FileNotFoundError:
            return err(f"Hot-words library '{library_id}' not found")

        return lib.model_dump()

    return to_json(await run_sync(_run))


# ── delete_hot_words_library ───────────────────────────────────


@domain_gated(DOMAIN_HOT_WORDS)
@confirm_destructive()
@hub_routed(
    method="DELETE",
    path="/hot-words/{library_id}",
    # REST 成功回 {"message":"Hot words library deleted"}；工具契约多一个 id。
    shape=lambda payload, a: (
        payload
        if not isinstance(payload, dict)
        else {
            "message": f"Hot-words library '{a.get('library_id')}' deleted",
            "id": a.get("library_id"),
        }
    ),
    forwards=("library_id",),
)
async def delete_hot_words_library(library_id: str, ctx: Context | None = None) -> str:
    """Delete a hot-words library permanently.

    Meetings that referenced this library will have their
    ``hot_words_library_id`` field set to a stale value — those meetings
    will continue to work, just without hot-word biasing.
    """
    def _run() -> dict[str, Any]:
        from src.hot_words import store as hstore

        try:
            deleted = hstore.delete_library(library_id)
        except PermissionError as exc:
            return err(str(exc))
        if not deleted:
            return err(f"Hot-words library '{library_id}' not found")
        return ok(message=f"Hot-words library '{library_id}' deleted", id=library_id)

    return to_json(await run_sync(_run))


__all__ = [
    "list_hot_words_libraries",
    "get_hot_words_library",
    "create_hot_words_library",
    "update_hot_words_library",
    "delete_hot_words_library",
]