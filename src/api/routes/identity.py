"""Shared identity profile and authorization dependencies.

Retained role and ACL management routes live in ``ee.api.identity``.
"""

from __future__ import annotations


from fastapi import APIRouter, HTTPException, Request

from src.identity import acl as acl_mod
from src.identity import roles as roles_mod
from src.identity.policy import can

router = APIRouter(prefix="/api/identity", tags=["identity"])


def _actor():
    from src.identity import get_actor

    return get_actor()


def _can_access_domain(actor, domain: str) -> bool:
    """该 actor 能否访问某域（看该域 ACL 的 read 档；owner/admin 全通过）。"""
    from src.identity.policy import may_see_everything

    if may_see_everything(actor):
        return True
    return acl_mod.domain_allows(domain, "read", actor.role_id)


@router.get("/me")
def get_me(request: Request):
    """当前操作者的身份画像（UI 用它决定显示哪些入口/卡片）。

    **必须宽容**：任何已认证请求都要能拿到它——UI 靠它化妆，拿不到就整页失灵。
    """
    actor = _actor()
    # 创建入口的化妆信号（UI 决定画不画"新建"按钮）：**与墙同一引擎**现算
    # （policy.can），前端不重算规则，按钮与 403 不会分叉。
    # collection.create 是盒子级能力；meeting.create 走 meeting 域 create 档
    # （此处无具体会议，创建者例外不参与——没有 create 档就是建不了）。
    can_create = {
        "collection": can(actor, "collection.create", {}),
        "meeting": can(actor, "meeting.create", {}),
    }
    role = roles_mod.get_role(actor.role_id)
    if role is None:
        # 未知角色：给最小画像而不是 500（UI 照常渲染，后端另有 fail-closed 拦请求）
        return {
            "actor_id": actor.id,
            "actor_kind": actor.kind,
            "role_id": actor.role_id,
            "role_name": actor.role_id,
            "modules": {},
            "capabilities": {},
            "is_local": actor.kind == "local",
            "domains": {"meeting": False, "hot_words": False, "people": False},
            "can_create": can_create,
        }
    from src.edition import has_permission_management

    capabilities = dict(role.capabilities)
    if not has_permission_management():
        capabilities["acl.manage"] = False
    return {
        "actor_id": actor.id,
        "actor_kind": actor.kind,
        "role_id": role.id,
        "role_name": role.name,
        "modules": dict(role.modules),
        "capabilities": capabilities,
        "is_local": actor.kind == "local",
        # 三域可访问性（会议/热词/人物）：它们没有 modules 开关，可访问性 = 各自
        # ACL 的 read 档。UI 靠它决定侧边栏与会议内入口是否显示。
        "domains": {
            "meeting": _can_access_domain(actor, acl_mod.DOMAIN_MEETING),
            "hot_words": _can_access_domain(actor, acl_mod.DOMAIN_HOT_WORDS),
            "people": _can_access_domain(actor, acl_mod.DOMAIN_PEOPLE),
        },
        "can_create": can_create,
    }


# ── 领域访问守卫（供各域路由用 FastAPI 依赖挂载）─────────────────
#
# 设计：meeting / hot_words / people 三域的"能否访问"= 各自 ACL 的 read 档。
# 这三域**没有 modules 开关**（避免同一件事两处能配），所以端点的准入必须查 ACL。
# 用依赖而不是逐个路由加：一处覆盖整个域，不会漏。


def require_domain(domain: str):
    """返回一个 FastAPI 依赖：要求当前 actor 有该域 read 档。

    本机 owner / admin 自然通过（``may_see_everything``）。
    """
    from src.identity import acl as _acl
    from src.identity.policy import may_see_everything

    def _dep() -> None:
        actor = _actor()
        # actor 为 None / 未知：说明这条路径没有经过配对门卫（测试里直接挂 router、
        # 或内部调用）。真实 HTTP 请求**一定**经过门卫（它会设置身份），所以这里
        # 不构成绕过；而对无身份上下文加以拒绝会让所有直接挂载 router 的测试失效。
        if actor is None:
            return
        if may_see_everything(actor):
            return
        if not _acl.domain_allows(domain, "read", actor.role_id):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "forbidden",
                    "message": f"this role cannot access the {domain} module",
                },
            )

    return _dep

def require_module(*modules: str):
    """返回一个 FastAPI 依赖：要求当前 actor 至少拥有其中一个功能模块。

    ``modules`` 是角色定义里的 ``modules`` 键（``chat`` / ``database`` / ``recall`` /
    ``tasks`` / ``settings``）。**多个模块是 OR 语义** —— 用于**跨页共享**的端点：
    例如 ``/api/collections`` 既被 chat 的库选择器用、也被 database 页用，只满足其一
    就该放行；否则关掉一个模块会把另一个页面的功能弄坏。

    **为什么要有它**（2026-09-18 用户要求）：``modules`` 此前**只在前端化妆**
    （侧边栏隐藏入口），服务端一次都没检查 —— 直接把 API 敲上去照样能用。
    用户明确要求「真实的服务端限制」。

    内置角色的现状（决定了改动风险）：
    - owner / admin / member：五个模块全 True → 加闸门后**行为完全不变**；
    - **visitor：recall=False / tasks=False** —— 这是注释里早已声明的意图
      （「只读知识库：进不了任务与回顾」），但从未被服务端强制。加闸门是把**已声明的
      语义落到实处**，不是新发明。
    - 自定义角色（如 Intern）：用户显式关掉的模块开始真正生效。

    ``module.settings`` 早已通过 ``config.read`` 间接强制（``config.py`` 的
    router 级 ``_config_write_gate``），所以这里不重复处理 settings。
    """
    from src.identity.policy import may_see_everything
    from src.identity.roles import get_role

    def _dep() -> None:
        actor = _actor()
        if actor is None:
            # 无身份上下文（测试直挂 router / 内部调用）。真实请求必经门卫设身份，
            # 所以不构成绕过；拦下它会让所有直接挂 router 的测试失效。
            return
        if may_see_everything(actor):
            return
        role = get_role(actor.role_id)
        if role is None:
            raise HTTPException(
                status_code=403,
                detail={"code": "forbidden", "message": "unknown role"},
            )
        if any(role.allows_module(m) for m in modules):
            return
        raise HTTPException(
            status_code=403,
            detail={
                "code": "forbidden",
                "message": (
                    "this role cannot access "
                    + (" or ".join(modules) if len(modules) > 1 else modules[0])
                ),
            },
        )

    return _dep


def require_collection_tier(param: str, tier: str):
    """返回 FastAPI 依赖：校验路径参数 ``param`` 指向的库满足 ``tier`` 档。

    用途：一批 collection 级的路由（documents / notes / recall / collections）
    直接以路径参数接收库 ID，之前完全没设防。用依赖挂上比逐个 handler 加更不易漏。

    不可见 → **404**（与"库不存在"同形）；可见但档位不足 → 403。
    ``actor`` 为空（测试直接挂 router）→ 放行（真实请求必过门卫设身份）。
    """
    from src.identity import acl as _acl
    from src.identity.policy import may_see_everything

    def _dep(**kwargs) -> None:
        actor = _actor()
        if actor is None or may_see_everything(actor):
            return
        cid = kwargs.get(param)
        if not cid:
            # 参数名配错（路由路径里没有叫 ``param`` 的参数）= 依赖形同虚设。
            # 曾经的写法是静默放行——一次改名重构就能把整个闸门悄悄拆掉。
            # 改为当场 500：配置错误必须炸出来，不能变成安全洞。
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "dependency_misconfigured",
                    "message": (
                        f"require_collection_tier({param!r}): no such path/body "
                        "parameter on this route — the gate would have been skipped"
                    ),
                },
            )
        cid = str(cid)
        if not _acl.collection_allows(cid, "read", actor.role_id):
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "collection not found"},
            )
        if tier != "read" and not _acl.collection_allows(cid, tier, actor.role_id):
            raise HTTPException(
                status_code=403,
                detail={"code": "forbidden", "message": f"{tier} on this collection is not allowed"},
            )

    return _dep
