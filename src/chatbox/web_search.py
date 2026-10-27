"""Web search for Chat — Tavily client + HITL confirmation gate.

Flow:
1. Agent calls ``request_web_search(query=…)``
2. Stream emits ``web_search_confirm`` and waits on :class:`WebSearchConfirmStore`
3. Frontend POSTs approve/deny to ``/api/chat/web-search-confirm``
4. On approve, Tavily runs; results are strongly tagged ``source_type=web``
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"

WEB_BANNER = (
    "⚠️ WEB / INTERNET RESULTS — NOT from the private knowledge base. "
    "Treat as external information; cite as web sources only. "
    "Do NOT present these as knowledge-base documents."
)


@dataclass
class _PendingConfirm:
    query: str
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool | None = None


class WebSearchConfirmStore:
    """In-process store bridging SSE streams and confirm HTTP calls.

    Uses :class:`threading.Event` because the confirm HTTP handler may run
    in a worker thread while the chat stream waits on the asyncio loop.
    """

    def __init__(self) -> None:
        self._pending: dict[str, _PendingConfirm] = {}
        self._guard = threading.Lock()

    def create(self, query: str) -> str:
        confirm_id = f"wsc_{uuid.uuid4().hex[:16]}"
        with self._guard:
            self._pending[confirm_id] = _PendingConfirm(query=query)
        return confirm_id

    def resolve(self, confirm_id: str, approved: bool) -> bool:
        with self._guard:
            item = self._pending.get(confirm_id)
        if item is None:
            return False
        item.approved = bool(approved)
        item.event.set()
        return True

    async def wait(self, confirm_id: str, timeout: float = 120.0) -> bool:
        with self._guard:
            item = self._pending.get(confirm_id)
        if item is None:
            return False
        loop = asyncio.get_running_loop()
        ok = await loop.run_in_executor(
            None, lambda: item.event.wait(timeout=timeout)
        )
        with self._guard:
            self._pending.pop(confirm_id, None)
        if not ok:
            logger.info("Web search confirm timed out: %s", confirm_id)
            return False
        return bool(item.approved)

    def cancel(self, confirm_id: str) -> None:
        with self._guard:
            item = self._pending.pop(confirm_id, None)
        if item is not None:
            item.approved = False
            item.event.set()


web_search_confirm_store = WebSearchConfirmStore()


def has_web_search_api_key() -> bool:
    """True when a Tavily API key is configured in Settings."""
    try:
        from src.config import get_config

        return bool((get_config().web_search.api_key or "").strip())
    except Exception:
        return False


def is_web_search_configured() -> bool:
    """Backward-compat alias: API key present (Chat UI owns the on/off switch)."""
    return has_web_search_api_key()


def get_web_search_config():
    from src.config import get_config

    return get_config().web_search


def web_toggle_label(*, web_search_enabled: bool) -> str:
    """Agent-facing Web UI toggle state (independent of user Allow/Decline)."""
    return "enabled" if web_search_enabled and has_web_search_api_key() else "disabled"


def format_web_tool_result(
    *,
    status: str,
    web_toggle: str,
    query: str = "",
    message: str = "",
) -> str:
    """Structured tool result so the model does not confuse toggle vs user decline.

    status values:
      - disabled: Web toggle OFF or no API key (cannot search)
      - user_declined: user clicked Decline for this turn (toggle may still be enabled)
      - ok: search completed
      - error: configuration / provider failure
    """
    lines = [
        f"status: {status}",
        f"web_toggle: {web_toggle}",
    ]
    if query:
        lines.append(f"query: {query.strip()[:300]}")
    if message:
        lines.append(f"message: {message}")
    return "\n".join(lines)


def tavily_search(
    query: str,
    *,
    api_key: str,
    max_results: int = 5,
    search_depth: str = "basic",
) -> dict[str, Any]:
    """Call Tavily Search API (sync). Returns normalized payload or error dict."""
    q = (query or "").strip()
    if not q:
        return {"error": "Empty search query", "results": []}
    if not (api_key or "").strip():
        return {"error": "Tavily API key is not configured", "results": []}

    depth = search_depth if search_depth in ("basic", "advanced") else "basic"
    max_results = max(1, min(int(max_results or 5), 10))

    payload = {
        "api_key": api_key.strip(),
        "query": q,
        "max_results": max_results,
        "search_depth": depth,
        "include_answer": False,
        "include_images": False,
        "include_raw_content": False,
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(TAVILY_SEARCH_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        logger.warning("Tavily HTTP error: %s", e)
        body = ""
        try:
            body = e.response.text[:300]
        except Exception:
            pass
        return {
            "error": f"Tavily HTTP {e.response.status_code}: {body or e.response.reason_phrase}",
            "results": [],
        }
    except Exception as e:
        logger.exception("Tavily request failed")
        return {"error": str(e), "results": []}

    raw_results = data.get("results") or []
    results: list[dict[str, Any]] = []
    for i, r in enumerate(raw_results):
        if not isinstance(r, dict):
            continue
        results.append({
            "title": r.get("title") or f"Web result {i + 1}",
            "url": r.get("url") or "",
            "content": r.get("content") or r.get("snippet") or "",
            "score": float(r.get("score") or 0.0),
            "source_type": "web",
        })
    return {
        "query": q,
        "provider": "tavily",
        "source_type": "web",
        "results": results,
        "warning": WEB_BANNER,
    }


def format_web_results_for_llm(payload: dict[str, Any], *, max_chars: int = 8000) -> str:
    """Build tool message text with strong web-source labeling.

    Caps length so old/long WEB dumps do not dominate the next model turn
    (e.g. answering a new country question with a previous Korea search).
    """
    if payload.get("error") and not payload.get("results"):
        return (
            f"{WEB_BANNER}\n\n"
            f"Web search failed: {payload['error']}\n"
            "Continue using the private knowledge base only."
        )

    lines = [
        WEB_BANNER,
        "",
        f"Web search query: {payload.get('query', '')}",
        f"Provider: {payload.get('provider', 'tavily')}",
        f"Hits: {len(payload.get('results') or [])}",
        "",
        "=== BEGIN WEB / INTERNET SOURCES (not knowledge base) ===",
        "Use ONLY these results for claims about this query; do not reuse older web dumps.",
    ]
    for i, r in enumerate(payload.get("results") or [], 1):
        snippet = (r.get("content") or "")[:1200]
        lines.append(f"\n[WEB-{i}] {r.get('title', '')}")
        lines.append(f"URL: {r.get('url', '')}")
        lines.append(f"Content: {snippet}")
    lines.append("\n=== END WEB / INTERNET SOURCES ===")
    lines.append(
        "\nWhen answering: clearly separate knowledge-base facts from web facts. "
        "Label web claims as internet sources. Never merge them into KB citations. "
        "Ground the answer in the query above — do not switch to a different region/topic "
        "from earlier conversation web results."
    )
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…[truncated web tool result]"
    return text


def web_results_to_sources(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert Tavily payload to Chat source dicts for the Sources panel."""
    sources: list[dict[str, Any]] = []
    for r in payload.get("results") or []:
        sources.append({
            "text": (r.get("content") or "")[:500],
            "score": float(r.get("score") or 0.0),
            "metadata": {
                "source": r.get("url") or r.get("title") or "web",
                "source_label": r.get("title") or r.get("url") or "Web result",
                "url": r.get("url") or "",
                "source_type": "web",
                "provider": "tavily",
            },
        })
    return sources


async def run_web_search_after_confirm(
    query: str,
    *,
    timeout: float | None = None,
    on_confirm_request=None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """HITL web search: emit confirm, wait, then search.

    ``on_confirm_request(confirm_id, query)`` is awaited/called so the stream
    can yield an SSE event before waiting.

    Returns ``(tool_content, sources, raw_payload)``.
    """
    if not has_web_search_api_key():
        msg = (
            "Tavily API key is missing. "
            "Add it under Settings → Web Search (Tavily)."
        )
        return msg, [], {"error": msg, "results": []}

    cfg = get_web_search_config()
    confirm_id = web_search_confirm_store.create(query)
    if on_confirm_request is not None:
        maybe = on_confirm_request(confirm_id, query)
        if asyncio.iscoroutine(maybe):
            await maybe

    wait_timeout = float(
        timeout if timeout is not None else getattr(cfg, "confirm_timeout_sec", 120) or 120
    )
    approved = await web_search_confirm_store.wait(confirm_id, timeout=wait_timeout)
    if not approved:
        msg = (
            f"{WEB_BANNER}\n\n"
            "User declined or timed out on web search confirmation. "
            "Do NOT invent internet results. Use the knowledge base only."
        )
        return msg, [], {"error": "declined_or_timeout", "results": [], "query": query}

    # Run blocking HTTP in a thread
    loop = asyncio.get_running_loop()
    payload = await loop.run_in_executor(
        None,
        lambda: tavily_search(
            query,
            api_key=cfg.api_key,
            max_results=cfg.max_results,
            search_depth=cfg.search_depth,
        ),
    )
    content = format_web_results_for_llm(payload)
    sources = web_results_to_sources(payload)
    return content, sources, payload
