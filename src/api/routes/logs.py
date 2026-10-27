"""Log streaming — captures all Python logs and streams them via SSE."""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections import deque

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter()

# Ring buffer for recent log entries (last 500)
_recent_logs: deque[dict] = deque(maxlen=500)
# SSE subscribers — each gets its own queue
_subscribers: list[queue.Queue] = []
_lock = threading.Lock()


class BroadcastHandler(logging.Handler):
    """Push every log record to the ring buffer and all SSE subscribers."""

    def emit(self, record: logging.LogRecord):
        entry = {
            "time": record.created,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = str(record.exc_info[1])
        _recent_logs.append(entry)
        with _lock:
            dead: list[int] = []
            for i, q in enumerate(_subscribers):
                try:
                    q.put_nowait(entry)
                except queue.Full:
                    dead.append(i)
            for i in reversed(dead):
                _subscribers.pop(i)


# Install handler on root logger at import time
_root = logging.getLogger()
_root.setLevel(logging.INFO)
_root.addHandler(BroadcastHandler())

# Also capture uvicorn access logs
for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
    logging.getLogger(name).setLevel(logging.INFO)


def _require_ops_access() -> None:
    """服务端日志属**运维面** → 需要 owner/admin。

    为什么不按 collection 过滤：日志是全局文本流（含 uvicorn access log、
    异常堆栈、各模块 logger 输出），逐条解析归属不现实且不可靠；而它的用途
    本就是排障——那是管理员的活。非管理员一律拒绝，比"尽力过滤"更安全。
    """
    from fastapi import HTTPException

    from src.identity import get_actor
    from src.identity.policy import may_see_everything

    actor = get_actor()
    if actor is None:  # 无身份上下文（测试直接挂 router）
        return
    if not may_see_everything(actor):
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "server logs are owner/admin only"},
        )


@router.get("/logs")
def get_recent_logs(limit: int = 200):
    """Return the most recent log entries（owner/admin only）。"""
    _require_ops_access()
    entries = list(_recent_logs)[-limit:]
    return {"logs": entries}


@router.get("/logs/stream")
def stream_logs():
    """SSE endpoint — streams log entries in real time（owner/admin only）。"""
    _require_ops_access()

    def generate():
        q: queue.Queue = queue.Queue(maxsize=200)
        with _lock:
            _subscribers.append(q)
        try:
            while True:
                try:
                    entry = q.get(timeout=30)
                    yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            with _lock:
                if q in _subscribers:
                    _subscribers.remove(q)

    return StreamingResponse(generate(), media_type="text/event-stream")
