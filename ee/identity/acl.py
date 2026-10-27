"""Enterprise domain and per-Collection ACL persistence."""
from __future__ import annotations
import json
from typing import Any
from src.identity import acl as core
from src.identity.acl import (AclError, BASE_TIER, COLLECTION_TIERS, DOMAIN_TIERS,
    _with_implicit, _explicit_only, _normalize_domain_acl,
    default_domain_acl, default_collection_acl, canonical_collection_id)

def load_domain_acl() -> dict[str, dict[str, list[str]]]:
    try:
        raw = json.loads(core.DOMAIN_ACL_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default_domain_acl()
    except Exception:
        core.logger.exception("domain_acl.json unreadable; using defaults")
        return default_domain_acl()
    return _normalize_domain_acl(raw)


def validate_domain_acl(acl: dict[str, dict[str, list[str]]]) -> None:
    """包含关系校验：非地基档必须是 read 的子集。越界抛错（不自动补集）。"""
    for domain, tiers in DOMAIN_TIERS.items():
        given = acl.get(domain) or {}
        base = set(given.get(BASE_TIER) or [])
        for tier in tiers:
            if tier == BASE_TIER:
                continue
            extra = set(given.get(tier) or []) - base
            if extra:
                raise AclError(
                    "tier_not_subset",
                    f"{domain}.{tier} contains roles not in {domain}.{BASE_TIER}: "
                    f"{sorted(extra)}",
                )


def save_domain_acl(acl: dict[str, dict[str, list[str]]]) -> dict[str, dict[str, list[str]]]:
    from src.atomic_io import write_text_atomic

    normalized = _normalize_domain_acl(acl)
    validate_domain_acl(normalized)
    # 落盘只写**显式名单**（隐式角色每档必有，写进去等于两处存同一事实）。
    on_disk = {
        d: {t: _explicit_only(v) for t, v in tiers.items()}
        for d, tiers in normalized.items()
    }
    with core._lock:
        core.IDENTITY_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "domains": on_disk}
        write_text_atomic(
            core.DOMAIN_ACL_PATH, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )
    from src.edition import mark_enterprise_used
    mark_enterprise_used(core.IDENTITY_DIR.parent)
    core.logger.info("domain acl saved")
    return normalized


def save_domain_acl_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """兼容两种请求体：``{"domains": {...}}`` 或直接 ``{domain: {...}}``。"""
    raw = payload.get("domains") if isinstance(payload.get("domains"), dict) else payload
    saved = save_domain_acl(raw)
    return {"domains": saved}


def _normalize_collection_acl(raw: Any) -> dict[str, list[str]]:
    out = default_collection_acl()
    if not isinstance(raw, dict):
        return out
    for tier in COLLECTION_TIERS:
        value = raw.get(tier)
        if isinstance(value, list):
            out[tier] = _with_implicit(value)
    return out


def _deny_all_collection_acl() -> dict[str, list[str]]:
    """**取不到 ACL 时的名单 = 谁都不许**（fail-closed）。

    与 ``default_collection_acl()`` 的区别是本质的：默认名单是"这个库**没有专门配过**
    ACL，按产品默认来"（member/visitor 可读，存量库如此）；而这里是"**我们读不到这个库
    的 ACL**"——读不到就不能假设它有默认权限。

    **为什么必须区分**（2026-09-17 用户第三次报"隐藏库还能在 Chat 里找出来"）：
    ``get_collection_meta`` 对**非法 id**（如显示名 "Project A" 带空格）会抛
    ``ValueError``，旧代码在 ``except`` 里回落 ``default_collection_acl()`` —— 于是
    "库名拼错/已删除" 反而被判成**可见**。历史记录恰恰按**显示名**存 collection，
    结果隐藏库的历史 Q&A 全文对所有人可见。异常 → 放行是最典型的 fail-open。
    """
    return {t: [] for t in COLLECTION_TIERS}


def collection_acl(collection_id: str) -> dict[str, list[str]]:
    """读某库的 ACL（先收敛到规范 id，见 ``canonical_collection_id``）。

    - 库存在但**没有** ``acl`` 键 → 产品默认（存量库不迁移，读时默认）；
    - 库标识**不合法 / meta 读不出来** → **空名单**（fail-closed，见
      ``_deny_all_collection_acl``）。绝不把"读不到"当成"有默认权限"。
    """
    from src.collections.store import get_collection_meta

    canonical = canonical_collection_id(collection_id)
    try:
        meta = get_collection_meta(canonical)
    except Exception:
        core.logger.warning(
            "cannot read meta for collection=%r; denying (fail-closed)", collection_id
        )
        return _deny_all_collection_acl()
    if meta is None:
        # 目录里没有这个库的 meta：可能是纯 Qdrant 的存量库（合法），也可能根本不存在。
        # 无法在这里区分，所以交给调用方——可见集判定只对 ``_all_collection_ids()``
        # （真实存在的库）逐一调用本函数，而"点名一个不存在/非法 id"的路径有
        # ``require_collection`` 先做存在性校验。
        return default_collection_acl()
    return _normalize_collection_acl(meta.get("acl"))


def save_collection_acl(
    collection_id: str, acl: dict[str, Any]
) -> dict[str, list[str]]:
    """把 ACL 写进该库 ``meta.json`` 的 ``acl`` 键（复用既有读写路径，避免双真相源）。

    **注意**：绝不用 ``update_collection_meta`` 存 ACL —— 那个函数自带
    ``collection.update`` 授权（那是对**库内容**的 config 档），与"改权限规则"是
    两回事。权限规则的闸门是能力 ``acl.manage``，由路由层把住；这里只负责写。
    """
    from src.collections.store import get_collection_meta, set_collection_acl

    if not get_collection_meta(collection_id):
        raise AclError("not_found", f"collection {collection_id!r} not found")
    normalized = _normalize_collection_acl(acl)
    # collection ACL 不做"档位子集"校验：读/写/删/配是**独立能力轴**，
    # 允许"能删但不能写"这种组合（例如只让 admin 清理）。领域 ACL 才需要子集约束
    # （因为那三域的 read 同时承担"能否访问"）。
    # 落盘只写**显式名单**（隐式角色读时并上，见 ``IMPLICIT_ROLES``）。
    set_collection_acl(
        collection_id, {t: _explicit_only(v) for t, v in normalized.items()}
    )
    from src.edition import mark_enterprise_used
    mark_enterprise_used(core.IDENTITY_DIR.parent)
    core.logger.info("collection acl saved collection=%s", collection_id)
    return normalized
