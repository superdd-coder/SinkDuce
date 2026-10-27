"""任务（async task）的可见性判定 —— REST 与 MCP 的**唯一裁决入口**。

**为什么单独成文件**：任务列表是**跨库聚合**——每条任务带 ``collection`` 和
``filename``，等于把"某个库存在、里面有什么文件"漏出去。而任务有**两条通道**
（REST ``/api/documents/tasks*`` 与 MCP ``list_tasks`` 等 5 个工具），2026-09-18
实测发现两侧判得不一样：

- REST 列表端点**有**收窄（`_visible_collection_ids_contain`）；
- **MCP 全部 5 个 tasks 工具完全没有**。

结果：同一把钥匙，REST 上看不到的库的任务，走 MCP 能全看到。**同一把钥匙两条通道
答案不同 = 权限模型的裂缝**，所以判定必须收成一处。

**口径**：任务的可见性**继承其所属库**（任务没有独立的 ACL）。库不可见 → 任务不可见
（与"这个库不存在"同形）。**没有归属库的任务**（如某些后台任务）不按库收窄 ——
它没有可泄露的库归属。

无身份上下文（测试直调 / 内部调用）→ 放行：真实请求必经配对门卫设置身份，
而拦下无上下文的调用会让所有直接挂 router 的测试失效。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def task_collection(task_id: str, task: Any | None = None) -> str | None:
    """解析任务归属的库 id；解析不到返回 None。

    取 ``task.collection`` 优先（REST 列表也用它），回退 ``_task_args`` 里的
    ``collection``（个别任务类型只把库放在 kwargs 里）。
    """
    col = getattr(task, "collection", None)
    if col:
        return str(col)
    try:
        from src.tasks import task_manager as _tm

        _ttype, kwargs = _tm.task_manager._task_args.get(task_id, (None, {}))
        if isinstance(kwargs, dict) and kwargs.get("collection"):
            return str(kwargs["collection"])
    except Exception:
        logger.debug("task_collection: cannot read args for %s", task_id, exc_info=True)
    return None


def can_see_task(actor: Any, task_id: str, task: Any | None = None) -> bool:
    """该 actor 能否看见这条任务。无归属库 → 可见。判定失败 → **不可见**（fail-closed）。"""
    if actor is None:
        return True
    try:
        from src.identity.policy import may_see_everything

        if may_see_everything(actor):
            return True
    except Exception:
        logger.exception("can_see_task: owner check failed for %s — denying", task_id)
        return False

    col = task_collection(task_id, task)
    if not col:
        # 没有库归属 → 无从按库收窄，也漏不出"某库存在"。放行。
        return True
    try:
        from src.identity import visibility as _vis

        return _vis.is_collection_visible(actor, col)
    except Exception:
        logger.exception("can_see_task: visibility failed for %s — denying", task_id)
        return False


def filter_visible_tasks(actor: Any, tasks: list[Any]) -> list[Any]:
    """把一批任务收窄到可见集（列表出口用）。"""
    return [t for t in tasks if can_see_task(actor, getattr(t, "id", ""), t)]
