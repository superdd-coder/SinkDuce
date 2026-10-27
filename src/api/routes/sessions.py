from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse

from src.services import services

logger = logging.getLogger(__name__)
from src.api.routes.identity import require_module

router = APIRouter(dependencies=[Depends(require_module("chat"))])


# ── request / response models ─────────────────────────────────────

from pydantic import BaseModel, Field
from src.reports.models import ReviewRequest


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
    attachment_ids: list[str] = Field(default_factory=list, max_length=10)
    report_reviews: list[ReviewRequest] = Field(default_factory=list, max_length=10)


def require_session_access(session_id: str, *, need_owner: bool = False):
    """会话归属守卫（RBAC §3.4）：非本人且非 admin/owner → **404**。

    **404 而不是 403**：403 等于确认"这个会话存在，只是不属于你"——可枚举信息泄露。

    ``need_owner=True`` 用于写操作（改名/删除/发消息）也走同一判定——本设计里
    "能看"与"能动"对会话是同一条线（别人的会话看都不该看到，更谈不上改）。
    """
    from fastapi import HTTPException

    from src.identity import get_actor, may_see_everything

    actor = get_actor()
    if may_see_everything(actor):
        return
    owner = _session_owner(session_id)
    # 存量会话（owner 为 None/''）归本机：非本机设备一律视为不可见。
    normalized = owner or "local"
    if normalized != actor.id:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "session not found"}
        )


def _session_owner(session_id: str) -> str | None:
    """会话归属（RBAC §3.4）：谁开的谁看，admin/owner 看全部。

    事件投递要靠它判断能否发给某订阅者。取不到（老会话/异常）→ ``None``，
    投递侧会**保守不投**（宁少投不泄露）。
    """
    try:
        s = _get_store().get_session(session_id)
        return getattr(s, "created_by", None) or None
    except Exception:
        return None


class WebSearchConfirmRequest(BaseModel):
    confirm_id: str
    approved: bool


class TodoDeleteConfirmRequest(BaseModel):
    confirm_id: str
    approved: bool


class NoteCreateConfirmRequest(TodoDeleteConfirmRequest):
    """Note-create card decision; ``collection`` overrides the target."""
    collection: str | None = None


class SessionResponse(BaseModel):
    id: str
    title: str
    collections: list[str]
    created_at: str
    updated_at: str
    message_count: int = 0
    last_message: str | None = None
    # 该会话自己的一套配置（模型/Web/思考/知识库）。空 dict = 没存过 → 前端落回默认。
    config: dict = {}


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
    # 打开会话时前端据此恢复这套配置（空 dict → 落回默认）
    config: dict = {}


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
        config=getattr(session, "config", None) or {},
    )


# ── endpoints ─────────────────────────────────────────────────────

@router.get("/sessions")
def list_sessions():
    """List sessions ordered by updated_at descending. Quick-chat sessions (prefix 'quick_') are excluded.

    RBAC §3.4：会话归个人——谁开的谁看，admin/owner 看全部。
    """
    from src.identity import get_actor, may_see_everything

    store = _get_store()
    actor = get_actor()
    sessions = store.list_sessions(
        created_by=None if may_see_everything(actor) else actor.id
    )
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
    from src.reports.store import for_sessions as report_store
    reports = report_store(store)
    with reports._lock:
        existing = store.get_session(body.id) if body.id else None
        session = store.create_session(
            title=body.title,
            collections=body.collections,
            session_id=body.id,
        )
        if existing is None:
            reports.start_session(session.id)

    # Meeting transcript is injected ephemerally in ChatboxAgent._build_messages
    # (not persisted as a session system message).
    # 多端同步：其他设备的会话列表要能看到这个新会话（见 events 设计 §5）。
    from src.events.emit import emit

    emit("session.created", scope={"session_id": session.id, "owner": _session_owner(session.id)})

    return _session_response(session, store)


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    require_session_access(session_id)
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
        config=getattr(session, "config", None) or {},
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
    require_session_access(session_id)
    """Update session title."""
    store = _get_store()
    if store.get_session(session_id) is None:
        raise HTTPException(404, f"Session {session_id} not found")
    try:
        updated = store.update_session(session_id, title=body.title)
    except ValueError:
        raise HTTPException(404, f"Session {session_id} not found")
    # 多端同步：会话改名/更新要反映到其他设备（见 events 设计 §5）。
    from src.events.emit import emit

    emit("session.updated", scope={"session_id": session_id, "owner": _session_owner(session_id)})

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
    require_session_access(session_id)
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
    require_session_access(session_id)
    """Get the total message count for a session (useful for context-warning thresholds)."""
    store = _get_store()
    if store.get_session(session_id) is None:
        raise HTTPException(404, f"Session {session_id} not found")
    count = store.count_messages(session_id)
    return {"session_id": session_id, "message_count": count}


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str):
    require_session_access(session_id)
    """Delete session and cascade-delete its messages."""
    store = _get_store()
    from src.reports.store import for_sessions as report_store
    reports = report_store(store)
    with reports._lock:
        if not store.delete_session(session_id):
            raise HTTPException(404, f"Session {session_id} not found")
        reports.purge(session_id)
    from src.chatbox.image_memory import purge_session_images

    purge_session_images(session_id)
    from src.chat_attachments.store import for_sessions
    for_sessions(store).purge(session_id)
    # 多端同步：其他设备要把这个会话从列表里摘掉（见 events 设计 §5）。
    from src.events.emit import emit

    emit("session.deleted", scope={"session_id": session_id, "owner": _session_owner(session_id)})


@router.post("/sessions/{session_id}/messages")
async def send_message(session_id: str, body: SessionMessageRequest = Body(...)):
    """Send a message to the session, returning an SSE stream."""
    require_session_access(session_id)
    import asyncio
    from src.chatbox.stream_handler import ChatStreamHandler

    store = _get_store()
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(404, f"Session {session_id} not found")

    if (not body.content or not body.content.strip()) and not body.attachment_ids and not body.report_reviews:
        raise HTTPException(400, "Message content must not be empty")

    from src.chat_attachments.store import for_sessions
    from src.prompts import CHAT_ATTACHMENT_DEFAULT_REQUEST
    attachments = for_sessions(store)
    # Validate before starting SSE; the agent binds files with the persisted user row.
    try:
        if len(set(body.attachment_ids)) != len(body.attachment_ids):
            raise ValueError("Choose distinct attachments")
        for aid in body.attachment_ids:
            a = attachments.get(session_id, aid)
            if a is None or a.status != "ready":
                raise ValueError("All attachments must belong to this session and be ready")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    review_requests = [r.model_dump() for r in body.report_reviews]
    if review_requests:
        from src.reports.store import for_sessions as report_store
        try:
            report_store(store).validate_reviews(session_id, review_requests)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(409, str(exc)) from exc
    from src.prompts import CHAT_REPORT_REVIEW_DEFAULT_REQUEST
    content = body.content if body.content.strip() else (CHAT_REPORT_REVIEW_DEFAULT_REQUEST if review_requests else CHAT_ATTACHMENT_DEFAULT_REQUEST)

    agent = getattr(services, "chatbox_agent", None)
    if agent is None:
        raise HTTPException(503, "Chat agent not initialized — enable Function Calling on an LLM model in Settings")

    # 这套配置跟着会话走：重新打开该会话时恢复。整套替换（删掉的项不该残留）。
    # 请求体里是 web_search_enabled，存进会话统一叫 web_search。
    store.update_session(
        session_id,
        config={
            "provider_id": body.provider_id,
            "model": body.model,
            "web_search": body.web_search_enabled,
            "thinking": body.thinking,
            "collections": body.collections if body.collections is not None else session.collections,
        },
    )

    handler = ChatStreamHandler(agent)

    async def event_stream():
        # 多端同步：用户这一轮一被接受就广播，其他设备能看到会话开始更新。
        from src.events.emit import emit

        emit("session.message_created", scope={"session_id": session_id, "owner": _session_owner(session_id)}, payload={"role": "user"})
        try:
            async for sse in handler.handle(
                session_id, content,
                thinking=body.thinking, collections=body.collections,
                mode=body.mode,
                provider_id=body.provider_id, model=body.model,
                web_search_enabled=body.web_search_enabled,
                **({"attachment_ids": body.attachment_ids} if body.attachment_ids else {}),
                **({"report_reviews": review_requests} if review_requests else {}),
            ):
                yield sse
        finally:
            # 助手这一轮无论正常结束、报错还是被中断都会落库一条消息，所以放在
            # finally 里广播，让其他设备知道该会话内容已变（见 events 设计 §5）。
            emit(
                "session.message_created",
                scope={"session_id": session_id},
                payload={"role": "assistant"},
            )

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


@router.post("/chat/note-create-confirm")
async def confirm_note_create(body: NoteCreateConfirmRequest = Body(...)):
    """Approve / deny a pending Chat note-create HITL request from SSE.

    Same shape and async rationale as ``confirm_todo_delete``. An optional
    ``collection`` carries the card's dropdown override — the waiting
    stream creates the note there instead of the agent's original pick.
    """
    from src.chatbox.note_create_confirm import note_create_confirm_store

    confirm_id = (body.confirm_id or "").strip()
    if not confirm_id:
        raise HTTPException(400, "confirm_id is required")
    ok = note_create_confirm_store.resolve(
        confirm_id, body.approved, override=(body.collection or "").strip() or None
    )
    if not ok:
        raise HTTPException(
            404,
            f"No pending note-create confirmation for id={confirm_id}",
        )
    return {"ok": True, "confirm_id": confirm_id, "approved": body.approved}
