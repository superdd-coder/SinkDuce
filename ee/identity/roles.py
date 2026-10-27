"""Enterprise custom-role persistence and editing."""
from __future__ import annotations
import json
import uuid
from pathlib import Path
from typing import Any
from src.identity import roles as core
from src.identity.roles import (Role, RoleError, ALL_MODULES, ALL_CAPABILITIES,
    BUILTIN_ROLES, BUILTIN_IDS, CUSTOM_PREFIX, is_builtin)

def custom_role_id() -> str:
    return f"{CUSTOM_PREFIX}{uuid.uuid4().hex[:12]}"


# ── 读写 ───────────────────────────────────────────────────────


def _role_from_dict(raw: dict[str, Any]) -> Role | None:
    rid = str(raw.get("id") or "").strip()
    if not rid:
        return None
    modules = {k: bool((raw.get("modules") or {}).get(k, False)) for k in ALL_MODULES}
    caps = {k: bool((raw.get("capabilities") or {}).get(k, False)) for k in ALL_CAPABILITIES}
    return Role(
        id=rid,
        name=str(raw.get("name") or rid),
        modules=modules,
        capabilities=caps,
        builtin=bool(raw.get("builtin", False)),
        assignable=bool(raw.get("assignable", True)),
        editable=bool(raw.get("editable", True)),
    )


def _read_custom_roles() -> dict[str, Role]:
    try:
        raw = json.loads(core.ROLES_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        core.logger.exception("roles.json unreadable; falling back to builtins only")
        return {}
    out: dict[str, Role] = {}
    for item in (raw.get("roles") or []):
        if not isinstance(item, dict):
            continue
        role = _role_from_dict(item)
        if role is None:
            continue
        # 内置角色以代码为准（任何人都不能通过改文件篡改它们）
        if role.id in BUILTIN_IDS:
            continue
        out[role.id] = role
    return out


def _write_custom_roles(roles: dict[str, Role]) -> None:
    from src.atomic_io import write_text_atomic

    core.IDENTITY_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "roles": [r.to_dict() for r in roles.values()]}
    write_text_atomic(core.ROLES_PATH, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    from src.edition import mark_enterprise_used
    mark_enterprise_used(core.IDENTITY_DIR.parent)


def load_roles() -> dict[str, Role]:
    """全部角色 = 内置（代码为准）+ 自定义（磁盘）。"""
    with core._lock:
        merged = dict(BUILTIN_ROLES)
        merged.update(_read_custom_roles())
        return merged


def get_role(role_id: str | None) -> Role | None:
    """取角色定义。未知 id → ``None``。

    **刻意不回落成 owner**：未知角色（数据损坏 / 人为改名 / 老版本遗留）必须是
    fail-closed——回落到 owner 等于把权限漏洞伪装成兼容性。
    """
    if not role_id:
        return None
    return load_roles().get(role_id)


def list_roles() -> list[Role]:
    """面向设置页：内置在前，自定义按名称排序。"""
    roles = load_roles()
    builtin = [roles[b] for b in ("owner", "admin", "member", "visitor") if b in roles]
    custom = sorted(
        (r for r in roles.values() if not r.builtin), key=lambda r: (r.name.lower(), r.id)
    )
    return builtin + custom


# ── 编辑（仅自定义角色）─────────────────────────────────────────


def create_custom_role(
    *,
    name: str,
    modules: dict[str, bool] | None = None,
    capabilities: dict[str, bool] | None = None,
) -> Role:
    clean = (name or "").strip()
    if not clean:
        raise RoleError("invalid_name", "role name must not be empty")
    role = Role(
        id=custom_role_id(),
        name=clean,
        builtin=False,
        assignable=True,
        editable=True,
        modules={k: bool((modules or {}).get(k, False)) for k in ALL_MODULES},
        capabilities={k: bool((capabilities or {}).get(k, False)) for k in ALL_CAPABILITIES},
    )
    with core._lock:
        current = _read_custom_roles()
        current[role.id] = role
        _write_custom_roles(current)
    core.logger.info("custom role created id=%s name=%r", role.id, role.name)
    return role


def update_custom_role(
    role_id: str,
    *,
    name: str | None = None,
    modules: dict[str, bool] | None = None,
    capabilities: dict[str, bool] | None = None,
) -> Role:
    if is_builtin(role_id):
        raise RoleError("builtin_immutable", "builtin roles cannot be edited")
    with core._lock:
        current = _read_custom_roles()
        role = current.get(role_id)
        if role is None:
            raise RoleError("not_found", f"unknown role {role_id!r}")
        new_name = (name or role.name).strip() or role.name
        new_modules = (
            {k: bool(modules.get(k, False)) for k in ALL_MODULES}
            if modules is not None
            else dict(role.modules)
        )
        new_caps = (
            {k: bool(capabilities.get(k, False)) for k in ALL_CAPABILITIES}
            if capabilities is not None
            else dict(role.capabilities)
        )
        updated = Role(
            id=role.id,
            name=new_name,
            builtin=False,
            assignable=True,
            editable=True,
            modules=new_modules,
            capabilities=new_caps,
        )
        current[role_id] = updated
        _write_custom_roles(current)
    core.logger.info("custom role updated id=%s name=%r", role_id, updated.name)
    return updated


def delete_custom_role(role_id: str) -> None:
    """删自定义角色。

    **调用方（路由层）负责先检查是否仍被钥匙使用**——被使用时返回 409 由路由处理；
    这里只在"能删"时执行删除（见设计 §9.1 的删除细节）。
    """
    if is_builtin(role_id):
        raise RoleError("builtin_immutable", "builtin roles cannot be deleted")
    with core._lock:
        current = _read_custom_roles()
        if role_id not in current:
            raise RoleError("not_found", f"unknown role {role_id!r}")
        del current[role_id]
        _write_custom_roles(current)
    core.logger.info("custom role deleted id=%s", role_id)


def roles_path() -> Path:
    return core.ROLES_PATH
