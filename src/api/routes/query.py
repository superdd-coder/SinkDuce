"""Legacy query endpoints.

POST /query and POST /query/stream are gone (410). Use
POST /api/sessions/{id}/messages. GET /history still reads
data/history/history.jsonl for MCP / leftover clients.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.api.schemas import QueryRequest
from src.config import DATA_DIR

logger = logging.getLogger(__name__)

router = APIRouter()

HISTORY_DIR = DATA_DIR / "history"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def _deprecated_response():
    return JSONResponse(
        status_code=410,
        content={
            "error": "Gone",
            "message": "This endpoint is deprecated. Please migrate to POST /api/sessions/{id}/messages",
            "migration_doc": "https://github.com/superdd-coder/sinkduce",
        },
        headers={"Deprecation": "true", "Sunset": "Sat, 01 Aug 2026 00:00:00 GMT"},
    )


@router.post("/query", include_in_schema=False)
def query(req: QueryRequest = None):
    """Deprecated. Use POST /api/sessions/{id}/messages instead."""
    return _deprecated_response()


@router.post("/query/stream", include_in_schema=False)
def query_stream(req: QueryRequest = None):
    """Deprecated. Use POST /api/sessions/{id}/messages instead."""
    return _deprecated_response()


@router.get("/history")
def get_history(limit: int = 50, include_details: bool = True):
    """遗留查询历史（MCP / 老客户端用）。

    RBAC（2026-09-16）：条目含 **question / answer / sources 全文**，是隐藏库最
    直接的泄露面（别人查过隐藏库，历史里就留着原文）。必须按 collection 可见性过滤。

    条目里的 ``collection`` 可能是显示名（历史数据如此），所以两种形态都要试：
    先按 id 判，不中再按显示名解析回 id 判——判定逻辑统一在
    ``visibility.history_entry_visible``。

    **过滤在 ``[-limit:]`` 之前**：反过来的话，被隐藏的条目会**占掉额度**，调用方
    拿到条数 < limit 本身就是"最近 N 条里有你看不到的东西"的可观测信号（少给一条
    既是功能损失，也是轻微信息泄露）。判定失败 → 空表（fail-closed）。
    """
    file = HISTORY_DIR / "history.jsonl"
    if not file.exists():
        return []
    entries = []
    for line in file.read_text().strip().split("\n"):
        if line:
            entries.append(json.loads(line))

    from src.identity import get_actor
    from src.identity import visibility as _vis

    try:
        actor = get_actor()
        # 批量版：集合/名字映射循环外建一次（逐条版是二次方，member 钥匙下 25s+）
        entries = _vis.history_entries_visible(actor, entries)
    except Exception:
        logger.exception("[History] visibility gate failed — returning empty")
        return []
    entries = entries[-limit:]
    if include_details:
        # 默认 True = 既有行为（网页与老客户端拿全量字段），保持不变。
        return entries
    # ``include_details=False`` 与 MCP 工具 ``get_query_history`` 的默认一致：
    # 只给 question + timestamp（轻量）。这个参数是给**跨盒子转发**用的 ——
    # 否则转发会把 answer/sources 全文带回来，而工具承诺的是轻量形状。
    return [
        {"question": e.get("question", ""), "timestamp": e.get("timestamp", "")}
        for e in entries
    ]
