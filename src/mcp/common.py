"""Shared utilities for MCP tools.

Every MCP tool has the same boilerplate:
- Defer-import ``src.services`` to avoid circular imports
- Wrap blocking I/O in a thread-pool executor
- JSON-serialize the result (with safe defaults for non-serializable types)

This module provides:
- :func:`run_sync` — async wrapper around blocking functions
- :func:`to_json` — safe JSON serialization
- :func:`ok` / :func:`err` — success / error response builders
- :func:`require_collection` — collection existence check
- :func:`require_task` — task existence check
- :func:`safe_filename` — path traversal guard
- :func:`decode_base64_content` — base64 → bytes for upload tools
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ── Async / serialization ────────────────────────────────────


def run_sync(fn: Callable[[], T]) -> asyncio.Future[T]:
    """Schedule a blocking function on the default thread pool.

    Usage::

        async def my_tool(...) -> str:
            def _run():
                return some_blocking_call()
            return to_json(await run_sync(_run))
    """
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(None, fn)


def to_json(obj: Any) -> str:
    """JSON-serialize with safe defaults (handles datetime, Path, etc.)."""
    return json.dumps(obj, ensure_ascii=False, default=str)


# ── Response builders ─────────────────────────────────────────


def ok(**fields: Any) -> dict[str, Any]:
    """Build a success response dict (no ``error`` key)."""
    return dict(fields)


def err(message: str, **extra: Any) -> dict[str, Any]:
    """Build an error response dict (always includes ``error`` key)."""
    payload: dict[str, Any] = {"error": message}
    payload.update(extra)
    return payload


def mcp_result(data: dict[str, Any]) -> Any:
    """Return a short text line + structured_content (avoid dual full JSON).

    MCP SDK otherwise puts the entire payload in TextContent *and*
    structuredContent when tools return a bare dict — doubling large trees
    or long document text in the model context.
    """
    try:
        from mcp.types import CallToolResult, TextContent

        is_err = bool(data.get("error"))
        if is_err:
            summary = f"error: {data.get('error')}"
            if data.get("extract_status"):
                summary += f" extract_status={data['extract_status']}"
            if data.get("hint"):
                summary += f" | {str(data['hint'])[:120]}"
        else:
            bits: list[str] = []
            if "collection" in data:
                bits.append(f"collection={data['collection']}")
            sm = data.get("summary") or {}
            if isinstance(sm, dict):
                if "unique_file_count" in sm:
                    bits.append(f"files={sm['unique_file_count']}")
                if "folder_count" in sm:
                    bits.append(f"folders={sm['folder_count']}")
                if "node_count" in sm:
                    bits.append(f"nodes={sm['node_count']}")
                if "detached_branch_count" in sm:
                    bits.append(f"detached={sm['detached_branch_count']}")
            if data.get("total") is not None:
                bits.append(f"total={data['total']}")
            if data.get("file_id"):
                bits.append(f"file_id={data['file_id']}")
            if data.get("extract_status"):
                bits.append(f"extract_status={data['extract_status']}")
            if data.get("total_chars") is not None:
                bits.append(f"chars={data['total_chars']}")
            if data.get("truncated"):
                bits.append("truncated=true")
            summary = "OK " + " ".join(bits) if bits else "OK"
            if data.get("warnings"):
                summary += f" warnings={len(data['warnings'])}"
            if data.get("read_hint"):
                summary += " | " + str(data["read_hint"])[:120]
        return CallToolResult(
            content=[TextContent(type="text", text=summary)],
            structured_content=data,
            is_error=is_err,
        )
    except Exception:
        # Fallback: plain dict (SDK will serialize; may dual-encode)
        return data


# ── Resource existence checks ────────────────────────────────


def require_collection(collection: str) -> dict[str, Any] | None:
    """Return None if collection exists, else return an error dict.

    Usage in a tool::

        def _run():
            if e := require_collection(collection):
                return e
            ...
    """
    # Lazy import to avoid loading services at MCP-module import time
    from src.services import services

    if not services.db.collection_exists(collection):
        return err(f"Collection '{collection}' does not exist")
    return None


def require_task(task_id: str) -> dict[str, Any] | tuple[Any, None] | None:
    """Return (task, None) if found, else (None, error_dict).

    Returns a 2-tuple so callers can both grab the task and short-circuit::

        task_or_err = require_task(task_id)
        if isinstance(task_or_err, dict):
            return json.dumps(task_or_err)
        task = task_or_err
    """
    from src.tasks import task_manager as _tm

    task = _tm.get_task(task_id)
    if not task:
        return err(f"Task '{task_id}' not found")
    return task


# ── Path / content safety ────────────────────────────────────


# Filename: allow letters, digits, dot, dash, underscore, space, parens, Chinese.
# Reject path separators and NUL bytes.
import re

_FILENAME_RE = re.compile(r"^[A-Za-z0-9._\- ()（）一-鿿]+$")


def safe_filename(name: str, max_len: int = 255) -> str:
    """Validate a user-supplied filename.

    Raises ValueError if ``name`` contains path separators, NUL, or other
    characters that could enable path traversal. Returns the name unchanged
    on success.
    """
    if not name:
        raise ValueError("Filename must not be empty")
    if len(name) > max_len:
        raise ValueError(f"Filename too long (max {max_len} chars)")
    if "/" in name or "\\" in name or "\x00" in name:
        raise ValueError(f"Invalid filename: {name!r}")
    if not _FILENAME_RE.match(name):
        # Reject anything outside the allowed character set (still passes
        # through trailing dots, leading dots, etc. — the OS will reject
        # truly illegal ones downstream).
        logger.warning("Filename contains unusual characters: %r", name)
    return name


def decode_base64_content(content_b64: str) -> bytes:
    """Decode a base64 string to raw bytes.

    Raises ValueError on invalid input. Tolerant of whitespace / newlines in
    the input (common when JSON-embedding base64 content).
    """
    if not content_b64:
        raise ValueError("Empty content")
    try:
        # stripdata: URLs sometimes prepend the data URL prefix
        if content_b64.startswith("data:"):
            _, _, content_b64 = content_b64.partition(",")
        return base64.b64decode(content_b64, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Invalid base64 content: {exc}") from exc


# ── Destructive-field guard ──────────────────────────────────

# Fields that the collection config exposes to MCP but are forbidden to modify
# because changing them would either destroy indexed data (chunk_mode) or
# require a full re-index (embedding_*).
DESTRUCTIVE_COLLECTION_FIELDS: frozenset[str] = frozenset({
    "chunk_mode",
    "embedding_provider",
    "embedding_model",
    "embedding_dimensions",
    "embedding_base_url",
    "embedding_api_key",
    "embedding_batch_size",
})


def filter_destructive_fields(updates: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Remove destructive fields from an update dict.

    Returns ``(safe_updates, rejected_fields)``. Callers may choose to raise
    on ``rejected_fields`` or simply log a warning.
    """
    rejected = [k for k in updates if k in DESTRUCTIVE_COLLECTION_FIELDS]
    safe = {k: v for k, v in updates.items() if k not in DESTRUCTIVE_COLLECTION_FIELDS}
    return safe, rejected


__all__ = [
    "run_sync",
    "to_json",
    "ok",
    "err",
    "mcp_result",
    "require_collection",
    "require_task",
    "safe_filename",
    "decode_base64_content",
    "DESTRUCTIVE_COLLECTION_FIELDS",
    "filter_destructive_fields",
]
