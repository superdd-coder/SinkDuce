"""可见集 —— "完全隐藏"的唯一裁决入口。

**为什么必须集中**：隐藏有十多个出口（catalog 枚举、检索 fan-out、文件树、工具、
事件流…）。如果每处各判一套，迟早有一处判得不一样，而那一处就是泄露。所以全部走这里。

**核心纪律**：不可见 = **该资源根本不进入结果集**，不是"查出来再过滤"。检索层尤其
如此——过滤后置意味着数据已经到了模型上下文里。

**不可见 → 404**（不是 403）：403 等于确认"这东西存在但你没权限"，本身就是可枚举的
信息泄露。

**admin / owner 可见全部**（2026-09-16 用户定）：这条优先级最高，先判。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from src.identity import acl as acl_mod
from src.identity.actor import Actor
from src.identity.policy import is_owner, may_see_everything

logger = logging.getLogger("identity.visibility")


def _all_collection_ids() -> list[str]:
    """全部（非内部）库 id。拿不到时返回空 —— 保守：宁可让调用方看到空列表。"""
    try:
        from src.services import services

        return [c for c in services.db.list_collections() if not c.startswith("__")]
    except Exception:
        logger.exception("cannot list collections for visibility")
        return []


def visible_collection_ids(actor: Actor) -> set[str] | None:
    """该 actor 可见的库 id 集合；``None`` = 全部（owner / admin）。

    可见 = 该库 collection ACL 的 ``read`` 档含此角色。
    """
    if may_see_everything(actor):
        return None
    role_id = actor.role_id
    return {c for c in _all_collection_ids() if acl_mod.collection_allows(c, "read", role_id)}


def is_collection_visible(actor: Actor, collection_id: str | None) -> bool:
    if not collection_id:
        return False
    if may_see_everything(actor):
        return True
    return acl_mod.collection_allows(collection_id, "read", actor.role_id)


def can_read_collection(actor: Actor, collection_id: str | None) -> bool:
    """读权限（可见即可读——两者同义，读档同时承担"能否访问"）。"""
    return is_collection_visible(actor, collection_id)


def assert_can_read(actor: Actor, collection_id: str | None) -> None:
    """不可见 → **404**（与"不存在"不可区分）。"""
    if is_collection_visible(actor, collection_id):
        return
    from fastapi import HTTPException

    raise HTTPException(
        status_code=404, detail={"code": "not_found", "message": "collection not found"}
    )


def filter_collection_ids(actor: Actor, ids: Iterable[str]) -> list[str]:
    """把一批库 id 收窄到可见集（枚举出口用）。"""
    if may_see_everything(actor):
        return list(ids)
    role_id = actor.role_id
    return [c for c in ids if acl_mod.collection_allows(c, "read", role_id)]


def history_entry_visible(actor: Actor, entry: Mapping[str, Any]) -> bool:
    """查询历史条目能否给这个 actor 看。

    历史条目里 `collection` 字段存的可能是 **id**（新数据）也可能是**显示名**
    （早期数据，如 "Project A"），所以两种形态都要认。

    **用「可见集成员判定」而不是 ``is_collection_visible``**（2026-09-17 用户第三次
    报隐藏库仍能从 Chat 找到）：后者对"取不到 ACL 的未知 id"会回落默认名单，于是
    **已删除库**的 id、**拼错的显示名**都被判成可见——而历史条目里存着
    question / answer / sources **全文**，这正是泄露出处。成员判定对"不在可见集里"
    一律拒绝（fail-closed），并且天然覆盖两形态。

    没有归属信息的条目（空 collection）**保守不给非管理员看**——与事件流
    "拿不准不投"同一条纪律。
    """
    if may_see_everything(actor):
        return True
    raw = str((entry or {}).get("collection") or "").strip()
    if not raw:
        return False
    try:
        visible = visible_collection_ids(actor)
    except Exception:
        logger.exception("history visibility gate failed — refusing entry")
        return False
    if visible is None:
        return True
    if raw in visible:
        return True
    try:
        from src.collections import store as _cstore

        meta = _cstore.find_collection_by_name(raw)
    except Exception:
        logger.exception("history entry collection %r unresolvable", raw)
        return False
    return bool(meta and str(meta.get("id") or "") in visible)


def history_entries_visible(actor: Actor, entries) -> list:
    """批量版的 :func:`history_entry_visible`——语义完全一致，但把两样**循环外提升**：

    1. ``visible_collection_ids(actor)`` 只算**一次**（原实现每条重建）；
    2. 显示名 → id 的解析表只建**一次**（原实现每条 ``find_collection_by_name``
       = 全目录扫描——582 条历史 × 3553 个目录实测 ~25s，member 钥匙路径
       2026-09-19 全工具走查抓到；owner 因短路不受影响，所以网页上从未现形）。

    单条版保留给非批量场景；判定规则（成员判定、fail-closed、空 collection
    保守拒绝）与单条版逐字一致。
    """
    from src.collections.store import list_collections_meta

    if may_see_everything(actor):
        return list(entries)
    try:
        visible = visible_collection_ids(actor)
    except Exception:
        logger.exception("history visibility gate failed — refusing all entries")
        return []
    if visible is None:
        return list(entries)
    # 显示名 → id，一次建好（只认在册库——尸体目录的名字解析不出，同 fail-closed）
    try:
        name_to_id = {
            str(m.get("name") or ""): str(m.get("id") or "")
            for m in list_collections_meta()
        }
    except Exception:
        logger.exception("history name map failed — id-only matching")
        name_to_id = {}

    def _ok(entry) -> bool:
        raw = str((entry or {}).get("collection") or "").strip()
        if not raw:
            return False
        if raw in visible:
            return True
        mid = name_to_id.get(raw)
        return bool(mid and mid in visible)

    return [e for e in entries if _ok(e)]


def collection_permissions(actor: Actor, collection_id: str) -> dict[str, bool]:
    """该库对当前 actor 的四档开关（read / write / delete / config 各 True|False）。

    **给 UI 化妆用**：前端靠它决定"设置入口要不要画出来"。真正的墙仍在各端点
    （``authorize`` / ``_ccol`` / ``assert_can_read``）——这里返回 True 不代表能绕过。
    不可见的库四档全 False（不泄露"存在但无权"）。
    """
    tiers = acl_mod.COLLECTION_TIERS
    if may_see_everything(actor):
        return {t: True for t in tiers}
    if not acl_mod.collection_allows(collection_id, "read", actor.role_id):
        return {t: False for t in tiers}
    return {t: acl_mod.collection_allows(collection_id, t, actor.role_id) for t in tiers}


# ── 会议 ───────────────────────────────────────────────────────


def _all_meeting_ids() -> list[str]:
    try:
        from src.meeting.store import list_meetings

        return [m.id for m in list_meetings()]
    except Exception:
        logger.exception("cannot list meetings for visibility")
        return []


def visible_meeting_ids(actor: Actor) -> set[str] | None:
    """可见的会议 id 集合；``None`` = 全部（owner / admin）。

    会议没有 per-meeting ACL（用户明确不对单个会议细粒度），所以判定是：
    角色在 meeting ACL 的 ``read`` 档 → 全部会议可见。创建者例外只影响 ``operate``
    档（他改得动自己建的），不影响能否看见。
    """
    if may_see_everything(actor):
        return None
    if not acl_mod.domain_allows(acl_mod.DOMAIN_MEETING, "read", actor.role_id):
        return set()
    return set(_all_meeting_ids())


def is_meeting_visible(actor: Actor, meeting_id: str | None) -> bool:
    if not meeting_id:
        return False
    if may_see_everything(actor):
        return True
    return acl_mod.domain_allows(acl_mod.DOMAIN_MEETING, "read", actor.role_id)


def assert_can_read_meeting(actor: Actor, meeting_id: str | None) -> None:
    """会议不可见 → **404**。"""
    if is_meeting_visible(actor, meeting_id):
        return
    from fastapi import HTTPException

    raise HTTPException(
        status_code=404, detail={"code": "not_found", "message": "meeting not found"}
    )


def filter_meeting_ids(actor: Actor, ids: Iterable[str]) -> list[str]:
    if may_see_everything(actor):
        return list(ids)
    if not acl_mod.domain_allows(acl_mod.DOMAIN_MEETING, "read", actor.role_id):
        return []
    return list(ids)


def meeting_permissions(actor: Actor, meeting_id: str | None) -> dict[str, bool]:
    """该会议对当前 actor 的可读 / 可写（**给 UI 化妆用**）。

    真正的墙在 ``store.delete_meeting`` / ``update_meeting`` 等处的 ``authorize``；
    这里只是"后端会不会放行"的提前回答，好让前端不画出按不动的按钮。

    ``write`` 走**与执行路径同一套判定**（``can(actor, "meeting.delete", …)``）而不是
    自己重算 ACL 名单——重算就有两个真相，一旦档位语义变了，按钮和墙就会分叉。
    更新/归档/删除/丢弃在动作表里同属 meeting 的写档，所以一个布尔够用。

    **必须把 ``created_by`` 一并交给判定**：meeting 的创建者对自建会议在所有写入档上
    放行（见 ``policy._check_domain``）。只传 meeting_id 的话创建者例外取不到 creator
    → 自己的会议也会被判成"不能写"→ UI 把按钮藏了，而后端其实放行（反过来的假信号）。
    """
    from src.identity.policy import can

    if not meeting_id:
        return {"read": False, "write": False}
    if may_see_everything(actor):
        return {"read": True, "write": True}
    created_by = _meeting_creator(meeting_id)
    return {
        "read": is_meeting_visible(actor, meeting_id),
        # 形状与 store 侧的调用一致（meeting_id + created_by）。
        "write": can(
            actor,
            "meeting.delete",
            {"meeting_id": meeting_id, "created_by": created_by},
        ),
    }


def _meeting_creator(meeting_id: str) -> str | None:
    """读会议的创建者；读不到返回 None（例外不生效，保守）。"""
    try:
        from src.meeting.store import get_meeting

        return getattr(get_meeting(meeting_id), "created_by", None)
    except Exception:
        logger.exception("cannot read creator for meeting %r", meeting_id)
        return None


def can_manage_meetings(actor: Actor) -> bool:
    """角色**整体**上有没有会议 ``manage`` 档（不含逐会议创建者例外）。

    给"分组"这类**跨会议**的入口用：组里可能混着别人的会议，单个会议的创建者判定
    无法代表"这一下能不能删整组"（删整组会牵连别人的会议），所以用最保守的口径——
    没有 manage 档就一律不给。创建者例外**不**在这里生效。
    """
    if may_see_everything(actor):
        return True
    return acl_mod.domain_allows(acl_mod.DOMAIN_MEETING, "manage", actor.role_id)


# ── 通用：能否访问某域（hot_words / people 也用同一套）──────────


def can_access_domain(actor: Actor, domain: str) -> bool:
    if may_see_everything(actor):
        return True
    return acl_mod.can_access_domain(actor.role_id, domain)
