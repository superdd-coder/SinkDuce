"""``search_all_boxes`` —— 联邦检索（设计文档 §10，第 6 步）。

## 四个必须处理的现实约束（设计文档 §10，逐条对应到实现）

1. **分数不可比** —— 每台盒子各配各的 embedding / reranker（``config.yaml`` 是
   每台一份），所以跨盒子的 score **没有同一把尺子**。
   ⇒ **绝不按分数合并排序**（那个排名是假的）。做法：**按盒子分段呈现**
   （``{"box": ..., "results": [...]}``），让读者自己按段看。段内排序才是有意义的。
2. **来源标注强制** —— 每条结果都带 ``box``（盒子名）。不标来源的联邦结果
   **不可信**：读者无法判断哪句来自哪台。
3. **离线盒子必须明说** —— 连不上的盒子进 ``unreachable`` 列表，**绝不静默省略**。
   静默省略会给出「看起来完整、实际缺一块」的答案，是最危险的失败模式。
4. **延迟由最慢的盒子决定** —— 必须**并发扇出**（``asyncio.gather``）。

## 另外两条刻意的决定

- **不去重**：同一份文档在两台盒子里都存在是常见的（用户自己同步过去），
  去重很难做对，做错会**丢内容**。宁可见到重复，也不悄悄删掉一条。
- **本机也作为一个段**：Hub 是本机盒子，用户问「所有盒子」时把本机排除在外
  是反直觉的。本机那段走**同一套 REST**（回环），于是所有段形状一致。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("hub.federated")

#: 单台盒子的检索超时。比一次普通调用宽松（检索本身要跑 embedding + rerank），
#: 但必须有上限：一台卡住的盒子不能把整个联邦检索拖死。
SEARCH_TIMEOUT = 45.0
#: 列库列表要快（只是读元数据）。
LIST_TIMEOUT = 10.0


def _local_target() -> dict[str, Any]:
    """本机盒子作为一个检索段（走回环 REST，形状与其他段一致）。"""
    from src.config import advertised_listen
    from src.pairing.service import get_box_info

    host, port = advertised_listen()
    # 恒用回环：MCP 只经回环可达（不变式），而这里我们就是本机。
    if host not in ("127.0.0.1", "localhost", "::1"):
        host = "127.0.0.1"
    info = get_box_info() or {}
    return {
        "box_id": str(info.get("box_id") or "local"),
        "name": str(info.get("box_name") or "This machine"),
        "addr": f"http://{host}:{port}",
        "token": "",
        "is_local": True,
    }


def _remote_targets() -> list[dict[str, Any]]:
    from src.hub import store

    out = []
    for b in store.list_boxes():
        addr = str(b.get("addr") or "").strip()
        if not addr:
            continue
        out.append(
            {
                "box_id": str(b.get("box_id") or ""),
                "name": str(b.get("box_name") or b.get("box_id") or addr),
                "addr": addr,
                "token": str(b.get("token") or ""),
                "is_local": False,
            }
        )
    return out


def box_targets() -> list[dict[str, Any]]:
    """联邦检索的全部目标：本机 + 所有已连接盒子（本机排在最前）。

    远程模式瘦壳（``SINKDUCE_REMOTE_SHELL``）里本机没有可检索的内容
    （services 未初始化），跳过本机段——只联合远端盒子。
    """
    from src.config import is_remote_shell_runtime

    if is_remote_shell_runtime():
        return _remote_targets()
    return [_local_target(), *_remote_targets()]


def _normalize(results: list[dict], target: dict[str, Any]) -> list[dict[str, Any]]:
    """给每条结果打上**来源盒子**（约束 2）。"""
    out: list[dict[str, Any]] = []
    for r in results or []:
        if not isinstance(r, dict):
            continue
        out.append({**r, "box": target["name"], "box_id": target["box_id"]})
    return out


async def _search_one(
    target: dict[str, Any],
    query: str,
    *,
    top_k: int,
    search_mode: str | None,
    use_reranker: bool | None,
    min_score: float,
    include_archived: bool,
) -> dict[str, Any]:
    """检索一台盒子。**失败不抛**，而是返回带 ``error`` 的段。

    为什么吞掉异常：一个盒子的毛病不该让整次联邦检索失败（约束 3 要求
    「部分结果 + 明确说明」，而不是全盘报错）。
    """
    from src.hub.client import HubCallError, call

    base = {"box": target["name"], "box_id": target["box_id"], "is_local": target["is_local"]}
    try:
        cols = await call(
            target["addr"], "/api/collections", target["token"], timeout=LIST_TIMEOUT
        )
    except HubCallError as exc:
        return {**base, "results": [], "error": str(exc), "unreachable": True}

    ids = [
        str(c.get("id"))
        for c in (cols or [])
        if isinstance(c, dict) and c.get("id") and (include_archived or not c.get("archived"))
    ]
    if not ids:
        # 不是错误：这台盒子上没有（可检索的）库。要说清楚，别让读者以为被静默跳过了。
        return {**base, "results": [], "collections_searched": 0, "note": "no collections to search"}

    body: dict[str, Any] = {
        "query": query,
        "collections": ids,
        "top_k": top_k,
        "min_score": min_score,
        # 关掉 agentic：联邦层面每台各跑一次 agent 会成倍放大延迟与成本，
        # 而且跨盒子的「多跳」本来就没有统一规划者（见 §10 的分数不可比同理）。
        "use_agent": False,
    }
    if search_mode:
        body["search_mode"] = search_mode
    if use_reranker is not None:
        body["use_reranker"] = use_reranker

    try:
        resp = await call(
            target["addr"],
            "/api/recall/search",
            target["token"],
            method="POST",
            json_body=body,
            timeout=SEARCH_TIMEOUT,
        )
    except HubCallError as exc:
        return {**base, "results": [], "error": str(exc), "unreachable": True}

    results = (resp or {}).get("results") or []
    return {
        **base,
        "results": _normalize(results, target),
        "collections_searched": len(ids),
        "time_ms": (resp or {}).get("time_ms"),
    }


async def search_all_boxes_impl(
    query: str,
    *,
    top_k: int = 5,
    search_mode: str | None = None,
    use_reranker: bool | None = None,
    min_score: float = 0.0,
    include_archived: bool = False,
) -> dict[str, Any]:
    """并发扇出到所有盒子，按盒子分段返回（**不合并排序**——约束 1）。"""
    targets = box_targets()
    if not targets:
        return {"query": query, "boxes": [], "note": "No boxes to search."}

    segments = await asyncio.gather(
        *(
            _search_one(
                t,
                query,
                top_k=top_k,
                search_mode=search_mode,
                use_reranker=use_reranker,
                min_score=min_score,
                include_archived=include_archived,
            )
            for t in targets
        ),
        # gather 本身不该抛：_search_one 已捕获 HubCallError，这里是防御性兜底。
        return_exceptions=True,
    )

    cleaned: list[dict[str, Any]] = []
    for t, seg in zip(targets, segments):
        if isinstance(seg, BaseException):
            logger.exception("federated search segment failed for %s", t["name"])
            cleaned.append(
                {
                    "box": t["name"],
                    "box_id": t["box_id"],
                    "is_local": t["is_local"],
                    "results": [],
                    "error": f"{type(seg).__name__}: {seg}",
                    "unreachable": True,
                }
            )
        else:
            cleaned.append(seg)

    total = sum(len(s.get("results") or []) for s in cleaned)
    unreachable = [s["box"] for s in cleaned if s.get("unreachable")]
    payload: dict[str, Any] = {
        "query": query,
        "boxes": cleaned,
        "total": total,
        "box_count": len(cleaned),
        # **分数不可比**：明确告诉调用方不要拿段间分数互相比较（约束 1）
        "note": (
            "Results are grouped per box on purpose — each box has its own embedding "
            "and reranker, so scores are NOT comparable across boxes and were "
            "deliberately not merged into one ranking."
        ),
    }
    if unreachable:
        # 约束 3：绝不静默省略。这句话必须显眼，否则答案会「看起来完整、实际缺一块」。
        payload["unreachable"] = unreachable
        payload["warning"] = (
            "These boxes did not answer, so their contents are MISSING from this "
            f"result: {', '.join(unreachable)}. Tell the user the answer is partial."
        )
    return payload
