"""Hub 联邦原语（设计文档 §2.3）：``list_boxes`` / ``switch_box``。

这两个工具是 Hub 的最小可用形态（§13 第 4 步，只读）。

## ★ 一个必须先讲清的实测结论：工具转发的 REST 等价性要**逐个**建立

设计文档当初设想的「Hub → 远端走已有 REST」在**覆盖面**上成立（REST 端点确实多），
但在**形状与语义等价性**上不成立。2026-09-18 逐个核对后发现两类不等价：

1. **形状不同**：如 ``list_collections``，REST 返回 ``{id,name,points_count,archived,permissions}``，
   而 MCP 工具还返回 ``qdrant_name`` / ``notes_count`` / ``last_active`` /
   ``catalog_definition``。直接转发 → 模型拿到的字段凭空少一半。
2. **语义不同（更危险）**：如 ``list_folders``，REST 端点调
   ``get_folder_tree(collection_id)``，其默认 ``include_unassigned=True``；而 MCP 工具
   **显式传 ``include_unassigned=False``**。直接转发 → 「未分配」文件夹会突然出现，
   看起来像功能正常、实则行为变了（用户 2026-09-13 的需求正是「未分配不可搜索」）。

所以**转发不是「配一条路由」那么便宜，而是要逐工具确认等价性**（必要时给 REST 加参数
或让工具接受新形状）。没确认过的工具**一律不转发**。

## 那没被转发的工具在切盒子后是什么行为？——**留在本机，并且如实告知**

绝不静默假装：``switch_box`` 的返回值里 **``tool_routing`` 与 ``local_only_tools``
是从真实注册表算出来的**（``routing.ROUTES`` 为空就是 ``false``），所以它**不可能
说假话**——将来加了适配器，这两项自动跟着变。
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import Context

from src.mcp.common import err, module_gated, to_json

logger = logging.getLogger("hub.tools")


def _box_view(b: dict, current: str) -> dict[str, Any]:
    """给模型看的盒子条目。**绝不含 token**（那是凭据，模型不需要也不该看到）。"""
    return {
        "box_id": b.get("box_id", ""),
        "name": b.get("box_name") or b.get("box_id", ""),
        "addr": b.get("addr", ""),
        "current": str(b.get("box_id") or "") == current,
        "has_key": bool(str(b.get("token") or "").strip()),
        "online": b.get("online"),
        "role": b.get("role", ""),
    }


def _routing_state() -> dict[str, Any]:
    """当前转发能力（从真实注册表算，不写死）。

    ``local_only_tools`` = 「作用在某个盒子上、但**还没接转发**」的工具 ——
    切盒子后它们仍打本机。**Hub 自有原语不算**：它们不属于任何盒子，
    尤其 ``search_all_boxes`` 恰恰是查全部盒子的（列进去等于说反话）。
    """
    from src.hub.routing import HUB_NATIVE_TOOLS, routed_tool_names

    from src.mcp.server import MCP_TOOL_NAMES

    routed = set(routed_tool_names())
    excluded = routed | set(HUB_NATIVE_TOOLS)
    local_only = sorted(n for n in MCP_TOOL_NAMES if n not in excluded)
    return {
        "tool_routing": bool(routed),
        "routed_tools": sorted(routed),
        "local_only_tools": local_only,
    }


async def list_boxes(ctx: Context | None = None) -> str:
    """List the SinkDuce boxes this Hub can reach, and which one is current.

    **Start here when the user mentions another box / another device / "my other
    computer"**, or when an answer might live somewhere other than this machine.

    Returns:
        JSON ``{"current": {...} | null, "boxes": [...], "tool_routing": bool,
        "routed_tools": [...], "local_only_tools": [...]}``.

        - ``current`` is ``null`` when the current box is **this machine** (default).
        - each box has ``box_id`` / ``name`` / ``addr`` / ``online`` / ``role``.
        - **``tool_routing`` is the load-bearing field**: when it is ``false``,
          ``switch_box`` only records intent and every tool still acts on **this**
          machine. Check it before assuming a box switch changed your data source.
        - ``local_only_tools`` lists tools that keep acting on this machine.
        - Keys are **never** included; ``has_key`` is a boolean only.

    Call ``switch_box`` to change the current box.
    """
    from src.hub import state
    from src.hub import store

    session = getattr(ctx, "session", None) if ctx is not None else None
    current_id = state.current_box_id(session)
    boxes = store.list_boxes()
    routing = _routing_state()

    current = None
    for b in boxes:
        if str(b.get("box_id") or "") == current_id:
            current = _box_view(b, current_id)
            break
    if current is None and current_id:
        # 选中的盒子已从列表移除（用户在设置页断开了）
        return to_json(
            {
                "current": {"box_id": current_id, "name": current_id, "disconnected": True},
                "boxes": [_box_view(b, current_id) for b in boxes],
                **routing,
                "hint": (
                    "The selected box is no longer connected. Call switch_box with "
                    'name="local" to go back to this machine.'
                ),
            }
        )
    return to_json({"current": current, "boxes": [_box_view(b, current_id) for b in boxes], **routing})


async def switch_box(name: str, ctx: Context | None = None) -> str:
    """Switch the current box (the target for tools that support it). Use ``list_boxes`` for names.

    Pass **``"local"``** to go back to this machine (the default).

    Switching runs a quick check against the target box so failures surface here
    instead of midway through a longer task:

    - not reachable → the box is off / off the network;
    - key rejected → it was revoked or the address changed;
    - otherwise you get the ``role`` that key holds there and any modules it lacks.

    **That check is informational only** — it is *not* an access decision. The target
    box re-checks every call, so a key revoked later still fails there. It also does
    not filter the tool list: the list stays complete, and a call you may not make
    returns a permission error. **When that happens, tell the user and do not retry.**

    ⚠️ **Read ``tool_routing`` in the response.** When it is ``false``, switching only
    records intent: no tool forwards to the other box yet, so results still come from
    **this** machine. Do not tell the user you are reading the other box.

    Returns:
        JSON with ``current`` (new box), ``reachable``, ``role`` / ``role_name``,
        ``unavailable`` (falsy modules/domains for that key), ``tool_routing``,
        ``routed_tools`` and ``local_only_tools``.

    Args:
        name: Box name from ``list_boxes``, or ``"local"`` for this machine.
    """
    from src.hub import state
    from src.hub import store

    session = getattr(ctx, "session", None) if ctx is not None else None
    target = (name or "").strip()
    if not target:
        return to_json(err('name is required; pass a box name from list_boxes, or "local"'))

    routing = _routing_state()

    if target.casefold() == "local":
        from src.config import is_remote_shell_runtime

        if is_remote_shell_runtime():
            # 远程模式瘦壳里没有「本机」这个盒子（无本地内容可回退）——
            # 明确报错并给出可用目标，绝不假装切换成功。
            return to_json(
                err(
                    "remote shell mode: this machine has no local box to switch "
                    "back to; pass a box name from list_boxes instead",
                    code="no_local_box",
                    connected=[
                        b.get("box_name") or b.get("box_id")
                        for b in store.list_boxes()
                    ],
                )
            )
        state.set_current_box_id(session, "")
        return to_json(
            {
                "current": None,
                "switched_to": "local",
                "note": "All tools act on this machine.",
                **routing,
            }
        )

    box = store.get_box_by_name(target) or store.get_box(target)
    if box is None:
        names = [b.get("box_name") or b.get("box_id") for b in store.list_boxes()]
        return to_json(
            err(
                f"no connected box named {target!r}",
                code="unknown_box",
                connected=names,
                hint='Use list_boxes for exact names, or "local" for this machine.',
            )
        )

    box_id = str(box.get("box_id") or "")
    # 先切过去再做探针：即使探针失败，用户/模型的意图（切到这台）也应当成立，
    # 否则「盒子正好离线」会让人无法切过去查看、也无法切走。
    state.set_current_box_id(session, box_id)

    from src.hub.client import HubCallError, probe

    display = box.get("box_name") or box_id
    note = (
        "Only the tools listed in routed_tools read from this box; everything else "
        "still acts on this machine — say so if the user assumes otherwise."
        if routing["tool_routing"]
        else (
            "Nothing forwards to this box yet (tool_routing=false): every tool still "
            "acts on this machine. Say so if the user expects the other box's data."
        )
    )

    try:
        me = await probe(box.get("addr", ""), box.get("token"))
    except HubCallError as exc:
        _remember_probe(box_id, online=False, role="", error=str(exc))
        return to_json(
            {
                # role 用刚探到的实况覆盖 store 快照（快照可能过期——2026-09-19 审计中
                # 换钥匙后 current.role 仍显示旧值，误导判读）。
                "current": _box_view({**box, "online": False, "role": ""}, box_id),
                "switched_to": display,
                "reachable": False,
                "reason": str(exc),
                "note": (
                    "This box did not answer. Calls against it will keep failing until "
                    'it is reachable — tell the user, and consider switch_box "local".'
                ),
                **routing,
            }
        )

    role_id = str((me or {}).get("role_id") or "")
    role_name = str((me or {}).get("role_name") or role_id)
    modules = (me or {}).get("modules") or {}
    domains = (me or {}).get("domains") or {}
    unavailable = sorted(
        [k for k, v in modules.items() if not v] + [k for k, v in domains.items() if not v]
    )
    _remember_probe(box_id, online=True, role=role_id, error="")

    return to_json(
        {
            "current": _box_view({**box, "online": True, "role": role_id}, box_id),
            "switched_to": display,
            "reachable": True,
            "role": role_id,
            "role_name": role_name,
            "unavailable": unavailable,
            "note": note,
            **routing,
        }
    )


async def search_all_boxes(
    query: str,
    top_k: int = 5,
    search_mode: str | None = None,
    use_reranker: bool | None = None,
    min_score: float = 0.0,
    include_archived: bool = False,
) -> str:
    """Search **every** connected box at once, grouped by box.

    Use it when the user asks something that might live on another machine
    ("search everything", "all my boxes", "my other computer too") — and when they
    have not told you which box holds the answer.

    **Read the response shape before reporting results:**

    - ``boxes`` is a **list of segments, one per box**, each with its own
      ``results``. The scores are ``NOT`` comparable between boxes (each box has
      its own embedding/reranker), so they were deliberately **not merged into a
      single ranking** — report per box, never as one global top-N.
    - every result carries ``box`` / ``box_id`` — **always say which box an answer
      came from**; an unattributed federated answer is unusable.
    - ``unreachable`` lists boxes that did not answer. When present, their contents
      are **missing**: tell the user the answer is partial and name them. Never
      present a partial result as complete.
    - duplicates across boxes are **kept on purpose** (deduping wrongly loses
      content).

    Args:
        query: The search string.
        top_k: Max chunks **per box** (not a global cap).
        search_mode: ``dense`` / ``sparse`` / ``hybrid``; omit for each box's own default.
        use_reranker: Force reranking on/off; omit for each box's own default.
        min_score: Drop chunks below this score (each box applies its own).
        include_archived: Include archived collections (default false).
    """
    from src.hub.federated import search_all_boxes_impl

    payload = await search_all_boxes_impl(
        query,
        top_k=top_k,
        search_mode=search_mode,
        use_reranker=use_reranker,
        min_score=min_score,
        include_archived=include_archived,
    )
    return to_json(payload)


def _remember_probe(box_id: str, *, online: bool, role: str, error: str) -> None:
    """把探测结果记进存储，供 ``list_boxes`` 显示在线状态。

    **只是展示用**（设计文档 §9 明确定位）：真正的执行在目标盒子上按请求判，
    这里缓存的结论过期了也不会造成越权——最坏是显示「在线」而调用失败。
    """
    from src.hub import store

    try:
        boxes = store.list_boxes()
        for b in boxes:
            if str(b.get("box_id") or "") == box_id:
                b["online"] = online
                b["role"] = role
                b["last_error"] = error
                break
        store.save(boxes)
    except Exception:
        # 记录观察结果失败不该让 switch_box 失败——它是展示信息。
        logger.exception("failed to persist probe result for %s", box_id)


# Hub 原语挂在 MCP 上（与其余工具同一套模块闸门语义：chat/database 任一即可，
# 因为「问别的盒子」既可能来自聊天语境也可能来自数据库语境）。
list_boxes = module_gated("chat", "database")(list_boxes)
switch_box = module_gated("chat", "database")(switch_box)
search_all_boxes = module_gated("chat", "database")(search_all_boxes)
