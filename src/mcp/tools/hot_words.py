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

import logging
from typing import Any

from src.mcp.common import err, ok, run_sync, to_json

logger = logging.getLogger(__name__)


# ── list_hot_words_libraries ───────────────────────────────────


async def list_hot_words_libraries() -> str:
    """List all hot-words libraries (sorted by mtime descending).

    Returns lightweight metadata only (``id``, ``name``, ``description``,
    ``word_count``, timestamps). Use :func:`get_hot_words_library` to fetch
    the actual word list.
    """
    def _run() -> dict[str, Any]:
        from src.hot_words import store as hstore

        libs = hstore.list_libraries()
        items = [
            {
                "id": lib.id,
                "name": lib.name,
                "description": lib.description,
                "word_count": len(lib.words),
                "created_at": lib.created_at,
                "updated_at": lib.updated_at,
            }
            for lib in libs
        ]
        return ok(libraries=items, total=len(items))

    return to_json(await run_sync(_run))


# ── get_hot_words_library ──────────────────────────────────────


async def get_hot_words_library(library_id: str) -> str:
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


async def create_hot_words_library(
    name: str,
    description: str = "",
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


async def update_hot_words_library(
    library_id: str,
    name: str | None = None,
    description: str | None = None,
    words: list[dict[str, Any]] | None = None,
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
        except FileNotFoundError:
            return err(f"Hot-words library '{library_id}' not found")

        return lib.model_dump()

    return to_json(await run_sync(_run))


# ── delete_hot_words_library ───────────────────────────────────


async def delete_hot_words_library(library_id: str) -> str:
    """Delete a hot-words library permanently.

    Meetings that referenced this library will have their
    ``hot_words_library_id`` field set to a stale value — those meetings
    will continue to work, just without hot-word biasing.
    """
    def _run() -> dict[str, Any]:
        from src.hot_words import store as hstore

        deleted = hstore.delete_library(library_id)
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