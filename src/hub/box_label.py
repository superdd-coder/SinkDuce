"""「当前盒子」的**可读名字**（给人类看的提示语用）。

为什么单独成模块：``state.current_box_id`` 给的是 id，而提示语里要出现**人能认出来**
的名字（「On box: Office Box」而不是一串 hex）。这个查询需要 ``mcp.get_context()``，
而 :mod:`src.hub.confirm` 也要用它 —— 放在 confirm 里会让 confirm 既当"确认逻辑"
又当"盒子名字查询"，且 routing/tools 想要名字时就得 import confirm。抽出来更干净。
"""

from __future__ import annotations


def current_box_label() -> str:
    """当前选中的盒子名（空串 = 本机）。

    **永远不抛**：拿不到就返回空串（提示语退化为「On this machine」），
    绝不因为一个展示用的查询失败而挡住一次真实操作。
    """
    try:
        from src.hub import state
        from src.mcp.server import mcp

        ctx = mcp.get_context()
        box_id = state.current_box_id(getattr(ctx, "session", None))
        if not box_id:
            return ""
        from src.hub import store

        box = store.get_box(box_id) or {}
        return str(box.get("box_name") or box_id)
    except Exception:
        return ""


__all__ = ["current_box_label"]
