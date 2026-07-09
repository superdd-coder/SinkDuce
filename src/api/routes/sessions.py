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
    msgs = store.get_messages(session.id, limit=1)
    last_msg = msgs[0].content[:100] if msgs else None
    # Count messages cheaply: use len of get_messages up to a reasonable limit
    all_msgs = store.get_messages(session.id, limit=1000)
    return SessionResponse(
        id=session.id,
        title=session.title,
        collections=session.collections,
        created_at=session.created_at,
        updated_at=session.updated_at,
        message_count=len(all_msgs),
        last_message=last_msg,
    )


# ── endpoints ─────────────────────────────────────────────────────

@router.get("/sessions")
def list_sessions():
    """List sessions ordered by updated_at descending. Quick-chat sessions (prefix 'quick_') are excluded."""
    store = _get_store()
    sessions = store.list_sessions()
    # Filter out quick-chat sessions — they are collection-scoped, not user-facing
    sessions = [s for s in sessions if not s.id.startswith("quick_")]
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
    return _session_response(session, store)


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    """Get session detail including message list."""
    store = _get_store()
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(404, f"Session {session_id} not found")
    msgs = store.get_messages(session_id)
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


@router.post("/sessions/{session_id}/generate-title")
def generate_title(session_id: str):
    """Generate a concise title from the first Q&A exchange using the chat LLM."""
    store = _get_store()
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(404, f"Session {session_id} not found")

    msgs = store.get_messages(session_id)
    user_msg = None
    assistant_msg = None
    for m in msgs:
        if m.role == "user" and user_msg is None:
            user_msg = m
        elif m.role == "assistant" and user_msg is not None and assistant_msg is None:
            assistant_msg = m
            break

    if not user_msg or not assistant_msg:
        raise HTTPException(400, "Need at least one Q&A exchange to generate a title")

    agent = getattr(services, "chatbox_agent", None)
    if agent is None:
        raise HTTPException(503, "Chat agent not initialized")

    llm = agent._llm
    client = getattr(llm, "_client", None)
    model = getattr(llm, "_model", "gpt-4")

    # Reuse the chat agent's context builder for KV cache reuse
    messages = agent._build_messages(session_id, "")
    # Trim to just the first Q&A — keep everything up to (but not including)
    # the second user message, so tool_call + tool_result pairs stay intact.
    user_count = 0
    cut_at = len(messages)
    for i, m in enumerate(messages):
        if m["role"] == "user":
            user_count += 1
            if user_count == 2:
                cut_at = i
                break
    messages = messages[:cut_at]
    messages.append({
        "role": "user",
        "content": "Write a short title (6 words max) for this conversation. Reply with ONLY the title, nothing else.",
    })

    try:
        if client:
            kwargs: dict = dict(
                model=model, messages=messages,
                temperature=0.3, max_tokens=30,
            )
            # MiniMax: thinking.type supports "adaptive" and "disabled" (not "enabled").
            # All other providers: use "disabled" to skip reasoning overhead.
            model_lower = (model or "").lower()
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
            resp = client.chat.completions.create(**kwargs)
            raw = resp.choices[0].message.content or ""
            # Strip <think> tags — some models (MiniMax, R1-style) emit them
            # even when thinking is nominally disabled.
            from src.providers.llm.openai_compat import _strip_think
            title = _strip_think(raw).strip()
        else:
            title = llm.generate(
                "Write a short title (6 words max) for this conversation. Reply with ONLY the title, nothing else.",
                system="You write short conversation titles.",
                thinking=False,
            ).strip()
    except Exception as e:
        logger.exception("Title generation failed for session %s", session_id)
        raise HTTPException(500, f"Title generation failed: {e}")

    # Sanitize: strip quotes and limit length
    title = title.strip("\"'.,;:!? ")
    if len(title) > 80:
        title = title[:80]
    if not title:
        title = "New Chat"

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
