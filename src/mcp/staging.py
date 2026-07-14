"""In-memory content staging store for MCP uploads.

Problem
-------
Passing file content as an MCP tool parameter means the bytes become part of
the LLM conversation transcript, wasting tokens and polluting context.

Solution
--------
A side-channel HTTP endpoint (``POST /api/mcp/stage-content``) accepts file
content out-of-band — via multipart upload, raw octet-stream, JSON base64, or
a server-local ``file_path`` — and returns a short-lived *staging token* (UUID).

The single MCP tool ``upload_document_from_staging`` then only passes this
~36-char token through the LLM context — the actual file bytes never enter
the transcript.  Meeting audio uploads use the same pattern via
``upload_meeting_audio_from_staging``.

Lifecycle
---------
1. Client sends content to ``POST /api/mcp/stage-content`` → gets ``{staging_token}``
2. Client calls MCP tool with just the token
3. Server reads content from store, processes it, deletes the entry

Tokens expire after ``TTL_SECONDS`` (600 s = 10 min).  A background task sweeps
expired entries every 60 s.  The store also enforces a maximum total size limit.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────

TTL_SECONDS: int = 600                # 10 minutes
MAX_TOTAL_BYTES: int = 500 * 1024 * 1024  # 500 MB
SWEEP_INTERVAL: int = 60              # clean up expired entries every 60 s


# ── Data model ───────────────────────────────────────────────────

@dataclass
class StagedContent:
    token: str
    filename: str
    content: bytes
    created_at: float = field(default_factory=time.monotonic)


# ── Store ────────────────────────────────────────────────────────

class StagingStore:
    """Thread-safe in-memory store for staged upload content."""

    def __init__(self) -> None:
        self._entries: dict[str, StagedContent] = {}
        self._lock = asyncio.Lock()
        self._sweep_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Begin periodic sweeping of expired entries."""
        if self._sweep_task is not None:
            return
        self._sweep_task = asyncio.create_task(self._sweep_loop())

    async def stop(self) -> None:
        if self._sweep_task is not None:
            self._sweep_task.cancel()
            self._sweep_task = None

    async def _sweep_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(SWEEP_INTERVAL)
                await self._sweep()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("StagingStore sweep failed")

    async def _sweep(self) -> None:
        now = time.monotonic()
        async with self._lock:
            expired = [
                token
                for token, entry in self._entries.items()
                if now - entry.created_at > TTL_SECONDS
            ]
            for token in expired:
                del self._entries[token]
            if expired:
                logger.info(
                    "StagingStore swept %d expired entries (%d remaining)",
                    len(expired), len(self._entries),
                )

    async def put(self, filename: str, content: bytes) -> str:
        """Store content and return a staging token.

        Raises ValueError if the total store size would exceed MAX_TOTAL_BYTES.
        """
        token = uuid.uuid4().hex
        entry = StagedContent(token=token, filename=filename, content=content)
        async with self._lock:
            total = sum(len(e.content) for e in self._entries.values())
            if total + len(content) > MAX_TOTAL_BYTES:
                raise ValueError(
                    f"Staging store full ({total / 1024 / 1024:.1f} MB in use, "
                    f"max {MAX_TOTAL_BYTES / 1024 / 1024:.0f} MB). "
                    f"Wait for pending uploads to complete or expired entries to be swept."
                )
            self._entries[token] = entry
        logger.debug("Staged content token=%s filename=%r size=%d", token, filename, len(content))
        return token

    async def take(self, token: str) -> StagedContent | None:
        """Retrieve and remove a staged entry. Returns None if not found or expired."""
        async with self._lock:
            entry = self._entries.pop(token, None)
        if entry is None:
            return None
        now = time.monotonic()
        if now - entry.created_at > TTL_SECONDS:
            logger.info("Staged content token=%s expired", token)
            return None
        return entry

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    @property
    def total_bytes(self) -> int:
        return sum(len(e.content) for e in self._entries.values())


# ── Global singleton ─────────────────────────────────────────────

staging_store = StagingStore()


__all__ = [
    "StagingStore",
    "StagedContent",
    "staging_store",
    "TTL_SECONDS",
    "MAX_TOTAL_BYTES",
]
