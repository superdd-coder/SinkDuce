from __future__ import annotations

import logging

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse

from src.services import services

logger = logging.getLogger(__name__)
router = APIRouter()


# ── request / response models ─────────────────────────────────────

from pydantic import BaseModel


class SessionCreateRequest(BaseModel):
    title: str = ""
    collections: list[str] | None = None
    id: str | None = None  # Optional fixed ID (used for quick-chat sessions)


class SessionUpdateRequest(BaseModel):
    title: str | None = None


class SessionMessageRequest(BaseModel):
    content: str
    thinking: bool = True
    collections: list[str] | None = None
    mode: str = "agentic"  # "agentic" | "direct"
    provider_id: str | None = None  # temporary override for this message
    model: str | None = None        # temporary override for this message
    # Chat-UI switch (default off). Requires Tavily API key in Settings.
    web_search_enabled: bool = False


class WebSearchConfirmRequest(BaseModel):
    confirm_id: str
    approved: bool


class TodoDeleteConfirmRequest(BaseModel):
    confirm_id: str
    approved: bool


class SessionResponse(BaseModel):
    id: str
    title: str
    collections: list[str]
    created_at: str
    updated_at: str
    message_count: int = 0
    last_message: str | None = None


class MessageResponse(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    sources: list[dict] | None = None
    metadata: dict | None = None
    created_at: str


class SessionDetailResponse(BaseModel):
    id: str
    title: str
    collections: list[str]
    created_at: str
    updated_at: str
    messages: list[MessageResponse]


# ── helpers ───────────────────────────────────────────────────────


def _get_store():
    store = services.session_store
    if store is None:
        raise HTTPException(503, "Session store not initialized")
    return store


def _session_response(session, store) -> SessionResponse:
    # limit=1 → last dialogue unit (+ tools); system may be first in the list
    msgs = store.get_messages(session.id, limit=1)
    non_system = [m for m in msgs if m.role != "system"]
    last = non_system[-1] if non_system else (msgs[-1] if msgs else None)
    last_msg = (last.content[:100] if last and last.content else None)
    message_count = store.count_messages(session.id)
    return SessionResponse(
        id=session.id,
        title=session.title,
        collections=session.collections,
        created_at=session.created_at,
        updated_at=session.updated_at,
        message_count=message_count,
        last_message=last_msg,
    )


# ── endpoints ─────────────────────────────────────────────────────

@router.get("/sessions")
def list_sessions():
    """List sessions ordered by updated_at descending. Quick-chat sessions (prefix 'quick_') are excluded."""
    store = _get_store()
    sessions = store.list_sessions()
    # Filter out quick-chat sessions — they are collection-scoped, not user-facing
    sessions = [
        s for s in sessions
        if not s.id.startswith("quick_")
        and not s.id.startswith("meeting_")
        and not s.id.startswith("group_")
    ]
    return [_session_response(s, store) for s in sessions]


@router.post("/sessions", status_code=201)
def create_session(body: SessionCreateRequest = Body(...)):
    """Create a new session. If *id* is provided, uses it as the session ID (for quick-chat sessions)."""
    store = _get_store()
    session = store.create_session(
        title=body.title,
        collections=body.collections,
        session_id=body.id,
    )

    # Meeting transcript is injected ephemerally in ChatboxAgent._build_messages
    # (not persisted as a session system message).

    return _session_response(session, store)


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    """Get session detail including message list."""
    store = _get_store()
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(404, f"Session {session_id} not found")
    msgs = store.get_messages(session_id, limit=None)
    # Filter out internal tool/function messages — they are LLM conversation
    # context (tool_call + tool_result pairs), not user-visible chat content.
    visible_msgs = [m for m in msgs if m.role in ("user", "assistant")]
    return SessionDetailResponse(
        id=session.id,
        title=session.title,
        collections=session.collections,
        created_at=session.created_at,
        updated_at=session.updated_at,
        messages=[
            MessageResponse(
                id=m.id,
                session_id=m.session_id,
                role=m.role,
                content=m.content,
                sources=m.sources,
                metadata=m.metadata,
                created_at=m.created_at,
            )
            for m in visible_msgs
        ],
    )


@router.patch("/sessions/{session_id}")
def update_session(session_id: str, body: SessionUpdateRequest = Body(...)):
    """Update session title."""
    store = _get_store()
    if store.get_session(session_id) is None:
        raise HTTPException(404, f"Session {session_id} not found")
    try:
        updated = store.update_session(session_id, title=body.title)
    except ValueError:
        raise HTTPException(404, f"Session {session_id} not found")
    return _session_response(updated, store)


def _fallback_session_title(question: str) -> str:
    """Short title from the first user line when the LLM call fails."""
    line = (question or "").strip().splitlines()[0].strip()
    if line.startswith("[Current time:"):
        rest = line.split("]", 1)
        line = rest[1].strip() if len(rest) > 1 else line
    line = line.strip("\"'.,;:!? ")
    if len(line) > 60:
        line = line[:60].rstrip()
    return line or "New Chat"


def _sanitize_session_title(raw: str, *, fallback: str) -> str:
    title = (raw or "").strip().strip("\"'.,;:!? ")
    title = " ".join(title.split())
    if len(title) > 80:
        title = title[:80].rstrip()
    return title or fallback


@router.post("/sessions/{session_id}/generate-title")
def generate_title(session_id: str):
    """Generate a concise title from the first Q&A using the default chat LLM."""
    store = _get_store()
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(404, f"Session {session_id} not found")

    msgs = store.get_messages(session_id, limit=None)
    user_msg = None
    assistant_msg = None
    for m in msgs:
        if m.role == "user" and user_msg is None:
            user_msg = m
        elif m.role == "assistant" and user_msg is not None and assistant_msg is None:
            if not (m.content or "").strip():
                continue
            assistant_msg = m
            break

    if not user_msg or not assistant_msg:
        raise HTTPException(400, "Need at least one Q&A exchange to generate a title")

    question = (user_msg.content or "").strip()
    answer = (assistant_msg.content or "").strip()
    fallback = _fallback_session_title(question)

    agent = getattr(services, "chatbox_agent", None)
    llm = getattr(agent, "_llm", None) if agent is not None else None
    title = fallback
    if llm is not None:
        try:
            from src.prompts import SESSION_TITLE_SYSTEM, SESSION_TITLE_USER
            from src.providers.llm.openai_compat import _strip_think

            raw = llm.generate(
                SESSION_TITLE_USER.format(
                    question=question[:800],
                    answer=answer[:800],
                ),
                system=SESSION_TITLE_SYSTEM,
                temperature=0.3,
                max_tokens=30,
                thinking=False,
            )
            title = _sanitize_session_title(_strip_think(raw or ""), fallback=fallback)
        except Exception:
            logger.exception(
                "Title LLM failed for session %s — using question fallback",
                session_id,
            )
            title = fallback

    updated = store.update_session(session_id, title=title)
    logger.info("Generated title for session %s: %r", session_id, updated.title)
    return {"title": updated.title}


@router.get("/sessions/{session_id}/message-count")
def get_message_count(session_id: str):
    """Get the total message count for a session (useful for context-warning thresholds)."""
    store = _get_store()
    if store.get_session(session_id) is None:
        raise HTTPException(404, f"Session {session_id} not found")
    count = store.count_messages(session_id)
    return {"session_id": session_id, "message_count": count}


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str):
    """Delete session and cascade-delete its messages."""
    store = _get_store()
    if not store.delete_session(session_id):
        raise HTTPException(404, f"Session {session_id} not found")


@router.post("/sessions/{session_id}/messages")
async def send_message(session_id: str, body: SessionMessageRequest = Body(...)):
    """Send a message to the session, returning an SSE stream."""
    import asyncio
    from src.chatbox.stream_handler import ChatStreamHandler

    store = _get_store()
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(404, f"Session {session_id} not found")

    if not body.content or not body.content.strip():
        raise HTTPException(400, "Message content must not be empty")

    agent = getattr(services, "chatbox_agent", None)
    if agent is None:
        raise HTTPException(503, "Chat agent not initialized — enable Function Calling on an LLM model in Settings")

    handler = ChatStreamHandler(agent)

    async def event_stream():
        async for sse in handler.handle(
            session_id, body.content,
            thinking=body.thinking, collections=body.collections,
            mode=body.mode,
            provider_id=body.provider_id, model=body.model,
            web_search_enabled=body.web_search_enabled,
        ):
            yield sse

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/web-search-confirm")
def confirm_web_search(body: WebSearchConfirmRequest = Body(...)):
    """Approve or deny a pending web-search HITL request from Chat SSE."""
    from src.chatbox.web_search import web_search_confirm_store

    confirm_id = (body.confirm_id or "").strip()
    if not confirm_id:
        raise HTTPException(400, "confirm_id is required")
    ok = web_search_confirm_store.resolve(confirm_id, body.approved)
    if not ok:
        raise HTTPException(
            404,
            f"No pending web-search confirmation for id={confirm_id}",
        )
    return {"ok": True, "confirm_id": confirm_id, "approved": body.approved}


@router.post("/chat/todo-delete-confirm")
async def confirm_todo_delete(body: TodoDeleteConfirmRequest = Body(...)):
    """Approve or deny a pending Chat todo-delete HITL request from SSE.

    Async so it does not compete with ``wait()`` for the default thread pool
    (the stream holds a worker on ``Event.wait`` until this resolve).
    """
    from src.chatbox.todo_delete_confirm import todo_delete_confirm_store

    confirm_id = (body.confirm_id or "").strip()
    if not confirm_id:
        raise HTTPException(400, "confirm_id is required")
    ok = todo_delete_confirm_store.resolve(confirm_id, body.approved)
    if not ok:
        raise HTTPException(
            404,
            f"No pending todo-delete confirmation for id={confirm_id}",
        )
    return {"ok": True, "confirm_id": confirm_id, "approved": body.approved}
