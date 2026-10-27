"""Hub 钥匙托管的持久化层（``data/hub/boxes.json``）。

**这个文件里全是别人的钥匙明文**，所以它比一般 JSON 存储敏感一档：

1. **读写面只许本机**：端点准入必须带 ``_require_local``（见 ``routes.py``）——
   远端盒子（哪怕持有效 member 钥匙）绝不能读或写这台盒子的出站钥匙。
   这是本模块最重要的不变量：``boxes.json`` 是**这台盒子的凭据集合**。
2. **绝不进日志**：日志只记 ``box_id`` 与地址，token 明文不落日志。
3. 原子写（``src.atomic_io``）：与其余运行时数据一致，避免半截文件。

**为什么不复用 ``src/pairing/store.py``**：那个文件是**入站**（别人拿哪把钥匙进我），
这个是**出站**（我拿哪把钥匙去访问别人）。混在一起会让「打印钥匙列表」这类操作
一不小心把出站钥匙也吐出来。物理隔离是这里最便宜的防线。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.config import DATA_DIR

logger = logging.getLogger("hub.store")

HUB_DIR = DATA_DIR / "hub"


def _boxes_path() -> Path:
    return HUB_DIR / "boxes.json"


def load() -> list[dict]:
    path = _boxes_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # 文件坏掉时按「没有盒子」处理而不是崩掉——Hub 是附加能力，不该拖垮 MCP 面。
        logger.exception("hub boxes.json unreadable; treating as empty")
        return []
    boxes = data.get("boxes", []) if isinstance(data, dict) else []
    return [b for b in boxes if isinstance(b, dict)]


def save(boxes: list[dict]) -> None:
    from src.atomic_io import write_text_atomic

    write_text_atomic(
        _boxes_path(),
        json.dumps({"version": 1, "boxes": boxes}, ensure_ascii=False, indent=2),
        # 这里全是别的盒子的钥匙明文，权限与 local-key.txt 对齐（0600），
        # 且必须在 tmp 落盘时就生效——先写后 chmod 有敞开窗口。
        mode=0o600,
    )


def _normalize_addr(addr: str) -> str:
    """去掉尾部斜杠，避免拼出 ``http://host//api/...``。"""
    return (addr or "").strip().rstrip("/")


def list_boxes() -> list[dict]:
    return load()


def get_box(box_id: str) -> dict | None:
    for b in load():
        if str(b.get("box_id") or "") == box_id:
            return b
    return None


def get_box_by_name(name: str) -> dict | None:
    """按盒子名查（``switch_box`` 用名字选盒子——模型更容易记住名字）。"""
    target = (name or "").strip()
    if not target:
        return None
    boxes = load()
    # 先精确匹配，再退化到 casefold（对中文名无副作用，对英文名便于输入）。
    for b in boxes:
        if str(b.get("box_name") or "") == target:
            return b
    for b in boxes:
        if str(b.get("box_name") or "").casefold() == target.casefold():
            return b
    return None


def replace_boxes(incoming: list[dict]) -> list[dict]:
    """用浏览器那份**整体替换**（浏览器是来源的唯一事实源）。

    为什么是整体替换而不是逐条 upsert：
    - **删除天然被覆盖**：逐条接口必须额外处理「这条没了」，整体替换里它就是不在列表里；
    - **不会漂移**：两侧最终就是同一份，不存在「后端多了一条谁都不记得的盒子」；
    - **幂等**：重复调用同一份内容结果相同，前端可以在多个时机无脑同步。

    代价：浏览器 localStorage 被清空时，后端这份也会被清空。这是**接受的**——
    两者表达的是同一件事（我能访问哪些盒子），清空 localStorage 时用户在 UI 上
    也确实看到「没有已连接设备」，两侧保持一致比各自为政更不容易出怪事。

    落盘的记录**保留探测状态**（online/role/last_error）：那是后端自己的观察结果，
    不属于浏览器要同步的内容，重写时丢掉会让 ``list_boxes`` 的在线指示闪烁。
    """
    previous = {str(b.get("box_id") or ""): b for b in load()}
    out: list[dict] = []
    seen: set[str] = set()
    for raw in incoming or []:
        box_id = str((raw or {}).get("box_id") or "").strip()
        addr = _normalize_addr(str((raw or {}).get("addr") or ""))
        if not box_id or not addr or box_id in seen:
            # 没有 id 或地址的记录无法使用（地址是调用目标）；重复 id 只留第一条。
            continue
        seen.add(box_id)
        prior = previous.get(box_id) or {}
        incoming_token = str((raw or {}).get("token") or "").strip()
        out.append(
            {
                "box_id": box_id,
                "box_name": str((raw or {}).get("box_name") or ""),
                "addr": addr,
                # **空钥匙 = 保留已存的**（不是清空）。理由：清空是**静默吊销** ——
                # 浏览器若因某个 bug / 竞态漏传了一次钥匙，后端就会永久失去对那台盒子
                # 的访问能力，而用户在 UI 上完全看不出（他看到的仍是「已连接」）。
                # 保留则最坏是持有一把过期的钥匙，下一次调用自然 401，可发现、可恢复。
                # 真要移除一整台盒子，是**它不在 incoming 列表里**（整体替换覆盖删除），
                # 而不是靠传空钥匙。
                "token": incoming_token or str(prior.get("token") or ""),
                "last_checked": str((raw or {}).get("last_checked") or ""),
                # 保留后端自己的观察结果
                "online": prior.get("online"),
                "role": prior.get("role", ""),
                "last_error": prior.get("last_error", ""),
            }
        )
    save(out)
    logger.info("hub boxes synced count=%d", len(out))
    return out


def clear() -> None:
    save([])
