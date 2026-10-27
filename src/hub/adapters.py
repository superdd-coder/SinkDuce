"""转发适配器：把「REST 响应」整形成「工具承诺的形状」。

## 为什么单独放一个文件

每个工具的 ``@hub_routed(...)`` 声明里，形状差异（信封、计算型 query、参数守卫）
如果都写成内联 lambda，会散落在 6 个工具文件里、各写各的、无法统一测试。
集中在这里的好处：

- **一处可测**：``tests/test_hub_forward_adapters.py`` 直接拿真 REST 响应喂进来断言；
- **一处可核**：每个适配器都写明「REST 给什么 / 工具承诺什么」，便于日后复查等价性。

## 命名约定

``*_shape``  = ``Callable[[payload, args], tool_output]``（整形成工具输出）
``*_query``  = ``Callable[[args], dict]``（算出 REST query 参数）
``*_refuse`` = ``Callable[[args], str | None]``（非空字符串 = 拒绝转发）

## 每条适配器都必须回答的两个问题（写进 docstring）

1. REST 返回的形状是什么？
2. 工具**承诺**的形状是什么？差异在哪、为什么这样补？
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger("hub.adapters")


# ── 信封类：REST 返回裸列表，工具承诺 {<key>: [...], ...} ──────


def envelope(
    list_key: str,
    *,
    extra: Callable[[dict[str, Any], Any], dict[str, Any]] | None = None,
) -> Callable[[Any, dict[str, Any]], Any]:
    """把裸列表包成工具承诺的信封。

    Args:
        list_key: 工具用哪个键装列表（``folders`` / ``groups`` / ``todos`` / ...）。
        extra: 追加标量字段（如 ``total`` / ``mine``）。
    """

    def _shape(payload: Any, args: dict[str, Any]) -> Any:
        if isinstance(payload, dict) and list_key in payload:
            # REST 已经带信封（例如 /notes 返回 {collection, notes}）→ 只补缺的
            out = dict(payload)
        else:
            out = {list_key: payload if isinstance(payload, list) else [payload]}
        if "collection" in args and "collection" not in out:
            out["collection"] = args.get("collection")
        if extra:
            out.update(extra(args, out.get(list_key) or []) or {})
        return out

    return _shape


def total_of(list_key: str) -> Callable[[dict[str, Any], Any], dict[str, Any]]:
    """``extra`` 用的助手：补 ``total`` = 列表长度。"""

    def _extra(_args: dict[str, Any], items: Any) -> dict[str, Any]:
        return {"total": len(items or [])}

    return _extra


# ── 计算型 query ─────────────────────────────────────────────


def passthrough_query(*names: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """把列出的工具参数原样当 query 传（缺省值 None 会被 client 丢掉）。"""

    def _q(args: dict[str, Any]) -> dict[str, Any]:
        return {n: args.get(n) for n in names}

    return _q


# ── 参数守卫：REST 无法忠实执行的参数 → 拒绝，而不是静默忽略 ──


# ── 后端"超集"字段的处理 ─────────────────────────────────────


__all__ = [
    "envelope",
    "passthrough_query",
    "total_of",
]
