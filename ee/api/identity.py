"""Enterprise identity and ACL management routes."""
from __future__ import annotations

import logging
from typing import Any
from fastapi import APIRouter, Body, HTTPException
from src.identity import acl as acl_mod
from src.identity import roles as roles_mod
from src.identity.policy import authorize
from src.api.routes.identity import _actor

logger = logging.getLogger("identity.routes.ee")
router = APIRouter(prefix="/api/identity", tags=["identity"])

def _require_acl_manage() -> None:
    """管理权限规则需要 ``acl.manage``（本机 owner 自然通过）。"""
    authorize(_actor(), "acl.manage", {})


def _role_payload(role: roles_mod.Role, key_counts: dict[str, int]) -> dict[str, Any]:
    return {
        "id": role.id,
        "name": role.name,
        "builtin": role.builtin,
        "assignable": role.assignable,
        "editable": role.editable,
        "modules": dict(role.modules),
        "capabilities": dict(role.capabilities),
        # 正在使用该角色的钥匙数（删除前提示用）
        "key_count": key_counts.get(role.id, 0),
    }


def _key_counts() -> dict[str, int]:
    """各角色在用钥匙数（含本机自动钥匙，因为它是 owner 的实例）。"""
    from src.pairing import service as pairing_service, store as pairing_store

    counts: dict[str, int] = {}
    try:
        for rec in pairing_store.load():
            if not rec.get("revoked_at"):
                rid = rec.get("role") or "member"
                counts[rid] = counts.get(rid, 0) + 1
    except Exception:
        logger.exception("cannot count keys per role")
    _ = pairing_service  # 保持导入语义（未来可能按 is_active 过滤）
    return counts


@router.get("/capabilities")
def list_capabilities():
    """可勾选的能力与模块清单（角色编辑器渲染用）。"""
    return {
        "modules": list(roles_mod.ALL_MODULES),
        "capabilities": list(roles_mod.ALL_CAPABILITIES),
        # 只有本机能授予的能力（UI 置灰；后端同样强制）
        "owner_only_capabilities": sorted(roles_mod.OWNER_ONLY_CAPABILITIES),
    }


@router.get("/roles")
def list_roles():
    _require_acl_manage()
    counts = _key_counts()
    return {"roles": [_role_payload(r, counts) for r in roles_mod.list_roles()]}


@router.post("/roles")
def create_role(body: dict = Body(...)):
    _require_acl_manage()
    name = (body.get("name") or "").strip()
    # 创建者不得授予自己没有的能力（签发上限的同一条纪律）
    caps = body.get("capabilities") or {}
    _assert_capabilities_grantable(caps)
    try:
        role = roles_mod.create_custom_role(
            name=name,
            modules=body.get("modules") or {},
            capabilities=caps,
        )
    except roles_mod.RoleError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)})
    return _role_payload(role, _key_counts())


@router.put("/roles/{role_id}")
def update_role(role_id: str, body: dict = Body(...)):
    _require_acl_manage()
    if body.get("capabilities") is not None:
        _assert_capabilities_grantable(body.get("capabilities") or {})
    try:
        role = roles_mod.update_custom_role(
            role_id,
            name=body.get("name"),
            modules=body.get("modules"),
            capabilities=body.get("capabilities"),
        )
    except roles_mod.RoleError as exc:
        status = 403 if exc.code == "builtin_immutable" else 404
        raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})
    return _role_payload(role, _key_counts())


@router.delete("/roles/{role_id}")
def delete_role(role_id: str):
    _require_acl_manage()
    # 正在被钥匙使用的角色删不掉——直接删会让那些钥匙悄悄失去全部权限，
    # 用户会当成 bug。提示使用中的数量，要求先用别的角色重发钥匙。
    counts = _key_counts()
    in_use = counts.get(role_id, 0)
    if in_use:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "role_in_use",
                "message": f"{in_use} key(s) still use this role",
                "params": {"count": in_use},
            },
        )
    try:
        roles_mod.delete_custom_role(role_id)
    except roles_mod.RoleError as exc:
        status = 403 if exc.code == "builtin_immutable" else 404
        raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})
    return {"ok": True}


def _assert_capabilities_grantable(caps: dict[str, Any]) -> None:
    """不得授予自己没有的能力（与签发上限同一纪律）。

    重要的一项：``key.manage`` 只能由本机（owner）授予——否则"能改权限规则的人"
    可以给自己/别人造出"能再管钥匙的钥匙"，天花板就没了。
    """
    actor = _actor()
    role = roles_mod.get_role(actor.role_id)
    if role is None:
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "unknown role"})
    for cap, enabled in (caps or {}).items():
        if not enabled:
            continue
        if cap not in roles_mod.ALL_CAPABILITIES:
            raise HTTPException(
                status_code=400, detail={"code": "unknown_capability", "message": cap}
            )
        if cap in roles_mod.OWNER_ONLY_CAPABILITIES and actor.kind != "local":
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "capability_owner_only",
                    "message": f"{cap} can only be granted from the local machine",
                },
            )
        if not role.has(cap):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "capability_not_grantable",
                    "message": f"you cannot grant {cap} (you do not have it)",
                },
            )


@router.get("/domain-acl")
def get_domain_acl():
    _require_acl_manage()
    return {
        "domains": acl_mod.load_domain_acl(),
        "tiers": {d: list(t) for d, t in acl_mod.DOMAIN_TIERS.items()},
        # 隐式角色（owner/admin）永远有每一档，UI 不渲染它们的勾选（后端也已强制并上）。
        "implicit_roles": list(acl_mod.IMPLICIT_ROLES),
    }


@router.put("/domain-acl")
def put_domain_acl(body: dict = Body(...)):
    _require_acl_manage()
    try:
        saved = acl_mod.save_domain_acl_payload(body)
    except acl_mod.AclError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)})
    return {
        **saved,
        "tiers": {d: list(t) for d, t in acl_mod.DOMAIN_TIERS.items()},
        "implicit_roles": list(acl_mod.IMPLICIT_ROLES),
    }


# ── collection ACL（每个库自己的设置里调用）──────────────────────


@router.get("/collection-acl/{collection_id}")
def get_collection_acl(collection_id: str):
    from src.identity import visibility

    _require_acl_manage()
    visibility.assert_can_read(_actor(), collection_id)
    return {
        "acl": acl_mod.collection_acl(collection_id),
        "tiers": list(acl_mod.COLLECTION_TIERS),
        # 隐式角色：永远可读/写/删/配，UI 不渲染（见 acl.IMPLICIT_ROLES）。
        "implicit_roles": list(acl_mod.IMPLICIT_ROLES),
    }


@router.put("/collection-acl/{collection_id}")
def put_collection_acl(collection_id: str, body: dict = Body(...)):
    """改某库的 ACL（需 ``acl.manage``）。

    注意与"改库 config"（``collection.update``）是两件事——所以这里不检查后者，
    而是检查前者（见 ``acl.save_collection_acl`` 的说明）。
    """
    _require_acl_manage()
    try:
        saved = acl_mod.save_collection_acl(collection_id, body.get("acl") or body)
    except acl_mod.AclError as exc:
        status = 404 if exc.code == "not_found" else 400
        raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})
    return {
        "acl": saved,
        "tiers": list(acl_mod.COLLECTION_TIERS),
        "implicit_roles": list(acl_mod.IMPLICIT_ROLES),
    }
