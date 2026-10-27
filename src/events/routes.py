"""SSE frame generator + endpoint for the event stream.

Design: development_doc/2026-09-16-multi-device-state-sync-design.md (§6, §7).

Frames (SSE event name / data):
  hello   {seq, oldest}                       sent first, so the client learns the cursor
  change  EventEnvelope                       a change occurred
  resync  {reason: "gap" | "overflow"}        replay impossible; client reconciles fully
  : keepalive                                 comment, every 20s

The generator is an ASYNC generator on purpose. Starlette wraps a *sync*
generator in `iterate_in_threadpool`, which takes one of anyio's 40 threadpool
tokens per `next()` call; a sync generator that then blocked in `queue.get()`
held that token for the whole keepalive interval, so ~40 idle streams starved
every sync `def` route in the app (measured: 42 streams → 15s stall on
`/api/collections`). Awaiting is free by comparison, so the subscriber's delivery
queue is an `asyncio.Queue` woken with `loop.call_soon_threadsafe` from the bus,
which stays synchronous and thread-safe for publishers.
"""

from __future__ import annotations

import asyncio
import json
import queue
from typing import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from src.events.bus import EventBus, Subscription, bus

router = APIRouter()

KEEPALIVE_SECONDS = 20


def _encode(frame: str, data) -> str:
    if frame == ":":
        return ": keepalive\n\n"
    if hasattr(data, "model_dump_json"):
        body = data.model_dump_json()
    else:
        body = json.dumps(data, ensure_ascii=False)
    return f"event: {frame}\ndata: {body}\n\n"


def _event_visible_to(actor, ev) -> bool:
    """该事件能否投递给这个 actor（RBAC，2026-09-16）。

    **这是多端同步最容易被漏掉的泄露面**：总线把每个变更广播给所有订阅者，
    不过滤的话无权限设备能从事件里读出隐藏库/会议的活动（名字、数量、节奏）。

    判定用 ``ev.scope`` 里的作用域键：
    - ``collection_id`` → 该库可见才投递；
    - ``meeting_id`` / ``group_id`` → 会议 read 档（含创建者例外）才投递；
    - ``session_id`` → 会话归属（owner/admin 见全部）；
    - ``library_id``（热词）/ ``person_id``（人物）→ 对应域 read 档。

    **拿不准的一律不投递**（宁少投，不泄露）——过度过滤的代价只是对方少刷新一次，
    而漏投的代价是泄露。
    """
    try:
        from src.identity import visibility as _vis
        from src.identity import acl as _acl

        if _vis.may_see_everything(actor):
            return True

        scope = getattr(ev, "scope", None) or {}
        if not isinstance(scope, dict):
            return False

        cid = scope.get("collection_id")
        if cid:
            return _vis.is_collection_visible(actor, str(cid))

        mid = scope.get("meeting_id")
        if mid:
            return _vis.is_meeting_visible(actor, str(mid))

        gid = scope.get("group_id")
        if gid:
            # 会议组随会议域：有会议读权限即可见组活动。
            return _acl.domain_allows(
                _acl.DOMAIN_MEETING, "read", actor.role_id
            )

        sid = scope.get("session_id")
        if sid:
            # 会话归属在 emit 时带 owner（见 events/emit.py）；拿不到归属就不投递。
            owner = scope.get("owner")
            if not owner:
                return False
            return str(owner) == actor.id

        lib = scope.get("library_id")
        if lib:
            return _acl.domain_allows(_acl.DOMAIN_HOT_WORDS, "read", actor.role_id)

        pid = scope.get("person_id")
        if pid:
            return _acl.domain_allows(_acl.DOMAIN_PEOPLE, "read", actor.role_id)

        # 无作用域键的事件（全局配置/任务清理等）：默认不投递给非管理员。
        return False
    except Exception:
        import logging as _logging

        _logging.getLogger("events").exception("visibility check failed; dropping event")
        return False


async def iter_event_frames(
    event_bus: EventBus,
    sub: Subscription,
    since: int | None,
    *,
    limit: int | None = None,
    timeout: float = KEEPALIVE_SECONDS,
    actor=None,
) -> AsyncIterator[tuple[str, object]]:
    """Yield (event_name, payload) frames for one subscriber.

    `since` is the client's last-seen seq, or None on a fresh connection (whose
    mount-time fetch is its snapshot). `limit` bounds the stream; production
    passes None and the caller disconnects. `timeout` is the idle interval before
    a keepalive comment frame is emitted.

    Subscribes-before-snapshot ordering is intentional: an event published
    between subscribe and snapshot would otherwise be lost, so we snapshot after
    subscribing and dedupe live events against the replay cursor instead.
    """
    emitted = 0

    def more() -> bool:
        return limit is None or emitted < limit

    head = event_bus.seq
    yield "hello", {"seq": head, "oldest": event_bus.oldest_seq}
    emitted += 1

    cursor = head
    if since is not None:
        if event_bus.can_replay(since):
            for ev in event_bus.snapshot_since(since):
                if not more():
                    return
                cursor = ev.seq
                # 不可见的事件**完全跳过**（不占 emitted 配额、也不推进客户端游标）。
                if actor is not None and not _event_visible_to(actor, ev):
                    continue
                yield "change", ev
                emitted += 1
        else:
            if not more():
                return
            yield "resync", {"reason": "gap"}
            emitted += 1
            # No replay possible; live delivery resumes from the current head and
            # the client's full reconcile covers whatever was missed.
            cursor = event_bus.seq

    while more():
        if sub.take_overflowed():
            # This client fell behind; events were dropped rather than blocking
            # the bus. Tell it to reconcile, then discard the stale backlog.
            yield "resync", {"reason": "overflow"}
            emitted += 1
            drain_subscription(sub)
            cursor = event_bus.seq
            continue
        try:
            ev = await sub.aget(timeout)
        except asyncio.TimeoutError:
            yield ":", None
            emitted += 1
            continue
        if ev.seq <= cursor:
            continue
        cursor = ev.seq
        if actor is not None and not _event_visible_to(actor, ev):
            continue
        yield "change", ev
        emitted += 1


def drain_subscription(sub: Subscription) -> None:
    """Drop any queued events for a subscriber that must resync instead.

    Drains only the queue actually in use for this subscription (see
    Subscription), and clears the overflow flag so live delivery can resume.
    """
    if sub.is_async and sub.aqueue is not None:
        while True:
            try:
                sub.aqueue.get_nowait()
            except asyncio.QueueEmpty:
                break
    else:
        while True:
            try:
                sub.queue.get_nowait()
            except queue.Empty:
                break
    sub.take_overflowed()


@router.get("/events/stream")
async def stream_events(
    request: Request,
    since: int | None = None,
    client: str | None = None,
):
    """App-wide change stream. Gated by PairingGateMiddleware like any /api route.

    client id 来源（二选一）：``?client=``（旧版前端/WS 场景）或
    ``X-Client-Id`` 请求头（2026-09-19 token 收口后 fetch 流走 header）。
    """
    from src.events.context import set_client

    client_id = client or None
    if client_id is None and request is not None:
        # 测试会直接以 request=None 调 handler；真实请求才有 headers 可读
        client_id = request.headers.get("x-client-id") or None
    if client_id:
        # Bind so that any emit performed while serving this request is attributed.
        set_client(client_id)
    # Register the running loop so the (synchronous) bus can wake this
    # subscriber without holding a thread.
    sub = bus.subscribe(loop=asyncio.get_running_loop())

    from src.identity import get_actor

    actor = get_actor()

    async def generate():
        try:
            async for frame, data in iter_event_frames(bus, sub, since, actor=actor):
                if frame == ":":
                    yield ": keepalive\n\n"
                else:
                    yield _encode(frame, data)
        finally:
            bus.unsubscribe(sub)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
