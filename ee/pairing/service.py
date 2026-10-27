"""Enterprise key role assignment and grant ceiling."""
from __future__ import annotations
from src.pairing import service as core
from src.pairing.service import PairingError

def can_grant_role(issuer_role, target_role) -> bool:
    """签发上限（RBAC §7.1）——**通用规则，不按角色名**。

    ``可签发 ⟺ 发行者持 key.manage
              且 目标角色的能力与模块 ⊆ 发行者的（不得授予自己没有的东西）
              且 (目标角色含 key.manage → 发行者必须是本机/owner)``

    为什么按**能力集合**比较而不是角色名：否则新建一个名字不同、能力等同 admin 的
    角色就成了绕过口。

    ``issuer_role`` 为 ``None`` 表示本机（owner 全权，但 owner 本身仍不可签发——
    它是本机身份，不是一把钥匙）。
    """
    from src.identity.roles import get_role

    if target_role is None:
        return False
    # owner 保留给本机身份：不发钥匙（安全锚不可复制）。
    if target_role.id == "owner":
        return False
    # 本机 = owner：可签发任何可签发角色。
    if issuer_role is None:
        return target_role.assignable
    if not target_role.assignable:
        return False
    issuer = get_role(issuer_role)
    if issuer is None:
        return False  # 未知角色 fail-closed
    if not issuer.has("key.manage"):
        return False
    # 不得授予自己没有的能力/模块
    if not target_role.capability_set <= issuer.capability_set:
        return False
    if not target_role.module_set <= issuer.module_set:
        return False
    # 「能再管钥匙的钥匙」只能由本机造出
    if "key.manage" in target_role.capability_set:
        return False
    return True


def set_token_role(rec_id: str, role: str, *, issuer_role: str | None = None) -> dict | None:
    """改一把已发出钥匙的角色（返回更新后的记录；找不到 → None）。

    **为什么现在可以安全地支持**（原设计曾以"降权瞬间在飞的短票"为由推迟，该理由
    经核实不成立）：

    短票（access token）的 payload 里**只有 ``kid`` 与 ``exp``，不含角色**，且
    ``verify_access_token`` 最后会**读盘取长钥匙记录**再返回。也就是说权限是
    **每次请求实时从 tokens.json 读的**，不在票里固化 —— 改完角色下一请求即生效，
    没有"旧权限残留"的窗口。

    **天花板同样适用**：不能靠"改角色"绕过签发上限（否则先发一把 member 再改成
    admin 就等效于签发 admin）。所以这里复用 ``can_grant_role`` 判定，
    并由调用方（路由层）传入签发者自己的角色。
    """
    from src.identity.roles import get_role

    target_role = get_role(role)
    if target_role is None:
        raise PairingError("unknown_role", f"unknown role {role!r}")

    records = core.store.load()
    for rec in records:
        if rec.get("id") != rec_id:
            continue
        # 本机自动钥匙不参与（它代表本机，角色恒为 owner）
        if core._is_local_key(rec):
            raise PairingError("not_modifiable", "the local machine key cannot be re-roled")
        current = rec.get("role") or "member"
        if current == role:
            return dict(rec)
        # 上限：与签发同一套规则（防"改角色"绕过天花板）
        # owner 目前是本机身份，不存在 owner 钥匙（迁移时已降为 admin），
        # 但若历史数据里有，也不允许改成别的（它是锚）。
        if current == "owner" and not core._is_local_key(rec):
            raise PairingError("not_modifiable", "owner keys cannot be re-roled")
        if not can_grant_role(issuer_role, target_role):
            raise PairingError(
                "role_not_grantable",
                f"cannot change this key to {role!r} with this identity",
            )
        rec["role"] = role
        core.store.save(records)
        from src.edition import mark_enterprise_used
        mark_enterprise_used(core.store.PAIRING_DIR.parent)
        core.logger.info("pairing token role changed id=%s -> %s", rec_id, role)
        return dict(rec)
    return None
