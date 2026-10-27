"""动作闸门（RBAC 策略引擎）。

**这是唯一裁决入口。** 业务代码只调 ``authorize(actor, action, resource)``，不自己判角色。

判定轴（设计 §5）：

1. **owner / 本机 / 系统** → 全通过（安全锚）。
2. **盒子级动作** → 查角色 ``modules``（进不进门）/ ``capabilities``（管理能力）。
3. **领域动作**（meeting / hot_words / people）→ 查领域 ACL；meeting 叠加**创建者例外**。
4. **collection 动作** → 取 ``resource["collection_id"]`` → 查该库 ACL。

**fail-closed**：collection 域动作缺 ``collection_id`` → **拒绝**。一个漏传资源键的
调用点就是一个后门，所以宁可拒绝（会在测试里立刻暴露），也不默许。

**404 vs 403**：不可见（对该资源没有 read）→ **404** —— 403 等于确认"这东西存在但
你没权限"，本身就是可枚举的信息泄露。其余越权 → 403。

**admin / owner 可见全部**（2026-09-16 用户定）：按归属隔离的个人资源（collection、
Chat 会话）对 admin/owner 不设限。这条**优先级高于归属判定**，所以"不可见 → 404"
的判定必须先过 admin/owner，否则管理员会把自己的盒子看窄。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from src.identity import acl as acl_mod
from src.identity.actor import Actor
from src.identity.roles import ALL_CAPABILITIES, ALL_MODULES, get_role

logger = logging.getLogger("identity.policy")


#: 不需要授权的动作（健康检查、身份自省等）。
PUBLIC_ACTIONS: frozenset[str] = frozenset({"identity.me", "health"})

# ══════════════════════════════════════════════════════════════════
# 动作 → (域, 档位) 静态表
# ══════════════════════════════════════════════════════════════════
#
# 先定"这个动作属于哪个域"，再定档位。域决定去哪里查名单：
#   box        → 角色 modules / capabilities
#   collection → 该库 ACL
#   meeting / hot_words / people → 领域 ACL

DOMAIN_BOX = "box"
DOMAIN_COLLECTION = "collection"

#: 精确动作 → (域, 档位)。
_EXACT: dict[str, tuple[str, str]] = {
    # ── 角色能力（盒子级）──
    "config.write": (DOMAIN_BOX, "config.write"),
    # 读配置属**管理面**（provider/模型/端点结构），与写同档。
    # 刻意**不**挂 module.settings：那个 modules 只表示"能进设置页"，
    # 而 member 需要它来改界面语言 —— 两者是不同的事（2026-09-17 修）。
    "config.read": (DOMAIN_BOX, "config.write"),
    "key.manage": (DOMAIN_BOX, "key.manage"),
    "key.read": (DOMAIN_BOX, "key.manage"),
    "acl.manage": (DOMAIN_BOX, "acl.manage"),
    "acl.read": (DOMAIN_BOX, "module.settings"),
    "collection.create": (DOMAIN_BOX, "collection.create"),
    "box.manage": (DOMAIN_BOX, "box.manage"),
    "box.read": (DOMAIN_BOX, "module.settings"),
    # ── collection 域 ──
    "collection.read": (DOMAIN_COLLECTION, "read"),
    "collection.update": (DOMAIN_COLLECTION, "config"),
    "collection.delete": (DOMAIN_COLLECTION, "delete"),
    # ── meeting 域（三档：read 纯读 / create 建+管自己的 / manage 管别人的）──
    "meeting.create": (acl_mod.DOMAIN_MEETING, "create"),
    "meeting.read": (acl_mod.DOMAIN_MEETING, "read"),
    # 下面这些**都是写入**：走 manage 档，但**创建者对自建会议一律放行**
    # （见 _check_domain）。所以 member 只有 create 档时，能录/能存能删自己的，
    # 动不了别人的。
    "meeting.update": (acl_mod.DOMAIN_MEETING, "manage"),
    "meeting.finalize": (acl_mod.DOMAIN_MEETING, "manage"),
    "meeting.archive": (acl_mod.DOMAIN_MEETING, "manage"),
    "meeting.delete": (acl_mod.DOMAIN_MEETING, "manage"),
    "meeting.discard": (acl_mod.DOMAIN_MEETING, "manage"),
    "meeting_group.update": (acl_mod.DOMAIN_MEETING, "manage"),
    "meeting_group.delete": (acl_mod.DOMAIN_MEETING, "manage"),
    # ── hot_words 域 ──
    "hot_words.read": (acl_mod.DOMAIN_HOT_WORDS, "read"),
    "hot_words.create": (acl_mod.DOMAIN_HOT_WORDS, "write"),
    "hot_words.update": (acl_mod.DOMAIN_HOT_WORDS, "write"),
    "hot_words.delete": (acl_mod.DOMAIN_HOT_WORDS, "delete"),
    # ── people 域（read 语义含 enroll，见设计 §3.3④）──
    "people.read": (acl_mod.DOMAIN_PEOPLE, "read"),
    "people.create": (acl_mod.DOMAIN_PEOPLE, "create"),
    "people.update": (acl_mod.DOMAIN_PEOPLE, "write"),
    # 路由层资料编辑（PATCH /speakers/{id}、PUT /speakers/me）用的动作名。
    # 与 people.update 同档，但名字更直白：write 档 = 改人物资料。
    "people.write": (acl_mod.DOMAIN_PEOPLE, "write"),
    "people.delete": (acl_mod.DOMAIN_PEOPLE, "delete"),
}

#: collection 域的动作前缀族（file_mgmt / notes 的一大族写动作都落在这里）。
_COLLECTION_PREFIXES: tuple[str, ...] = (
    "file.",
    "file_path.",
    "folder.",
    "node.",
    "chain.",
    "message.",
    "todo.",
    "group.",
    "note.",
)

#: 读类后缀：collection 域里这些后缀归 ``read`` 档，其余归 ``write``——
#: 注意 ``file.delete`` 这类是**库内写操作**（write），不是"删库"（delete 档 =
#: ``collection.delete``）。这两件事的破坏半径差一个数量级，绝不能混。
_READ_SUFFIXES = ("read", "list", "get", "view", "search")


def resolve_action(action: str) -> tuple[str, str] | None:
    """动作 → (域, 档位)；未知动作 → ``None``（fail-closed，调用方拒绝）。"""
    exact = _EXACT.get(action)
    if exact is not None:
        return exact
    if "." in action:
        prefix, _, suffix = action.partition(".")
        if f"{prefix}." in _COLLECTION_PREFIXES:
            if suffix in _READ_SUFFIXES:
                return (DOMAIN_COLLECTION, "read")
            return (DOMAIN_COLLECTION, "write")
    return None


# ══════════════════════════════════════════════════════════════════
# 判定
# ══════════════════════════════════════════════════════════════════


def _http_error(status: int, code: str, message: str):
    """延迟导入 FastAPI：策略层也可能在非 HTTP 语境（后台任务）使用。"""
    from fastapi import HTTPException

    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _deny(action: str, actor: Actor, *, not_found: bool = False, message: str = "") -> None:
    """统一拒绝出口：不可见 → 404；其余 → 403。"""
    if not_found:
        raise _http_error(404, "not_found", message or "resource not found")
    raise _http_error(
        403, "forbidden", message or f"role {actor.role_id!r} may not perform {action!r}"
    )


def is_owner(actor: Actor | None) -> bool:
    """本机 / 系统 / owner 角色 —— 全通过。``None``（无身份上下文）不算 owner。"""
    if actor is None:
        return False
    from src.edition import has_permission_management

    if not has_permission_management():
        return actor.kind in ("local", "system")
    return actor.role_id == "owner" or actor.kind in ("local", "system")


def may_see_everything(actor: Actor | None) -> bool:
    """admin / owner 可见全部（按归属隔离的个人资源例外）。``None`` → False。"""
    if actor is None:
        return False
    from src.edition import has_permission_management

    if not has_permission_management():
        return is_owner(actor)
    return is_owner(actor) or actor.role_id == "admin"


def _check_box(actor: Actor, action: str, kind: str, role) -> None:
    """盒子级：``module.<name>`` 查 modules，其余查 capabilities。"""
    if kind.startswith("module."):
        module = kind[len("module.") :]
        if module not in ALL_MODULES:
            _deny(action, actor)
        if not role.allows_module(module):
            _deny(action, actor, message=f"module {module!r} not enabled for this role")
        return
    if kind not in ALL_CAPABILITIES:
        _deny(action, actor)
    if not role.has(kind):
        _deny(action, actor, message=f"capability {kind!r} not granted to this role")


def _check_domain(
    actor: Actor, action: str, domain: str, tier: str, resource: Mapping[str, Any]
) -> None:
    """领域 ACL；meeting 域叠加创建者所有权。

    **meeting 三档语义（2026-09-17 用户重定）**：

    - ``read``  纯读取（列表、转写稿、回放、总结、简报）。**没有任何写权限**，
      包括对自己建的会议——"读取就是纯读取"。
    - ``create`` 能建会议，**并且对自己建的会议拥有一切权限**（录制/转写/上传/
      改名/归档/删除）。
    - ``manage`` 能操作 / 删除**别人的**会议（跨会议的会议组也归它）。

    判定式：

        放行 ⟺ role ∈ acl[domain][tier]
              ∨ (meeting ∧ 有 create 档 ∧ tier ≠ read ∧ created_by == actor)

    **创建者例外为什么要额外要求 ``create`` 档**：所有权是 ``create`` 带来的
    （"创建权限 = 可以创建转录以及操作自己的 Meeting，有自己创建的 Meeting 的所有
    权限"）。若只看"tier ≠ read + 是创建者"，那么一个**只有 read 档**的角色也能改
    自己名下的会议——那与"读取就是纯读取"矛盾。所以例外挂在 create 上：没 create
    就纯读，有 create 才拥有自己的会议。

    为什么例外覆盖**所有写入档位**（而不是只覆盖某一档）：旧版把它挂在 ``operate``
    上，而 member 默认只有 ``create`` → "能建但建完用不了"（保存转写都 403、会议变
    红色草稿）。用户实测后要求修掉这种荒谬配置。

    ``read`` 档本身不参与例外：read 是地基档，create 默认已含 read 子集。
    """
    if acl_mod.domain_allows(domain, tier, actor.role_id):
        return
    if (
        domain == acl_mod.DOMAIN_MEETING
        and tier != acl_mod.BASE_TIER
        # 所有权的前提是"有创建权"（见 docstring）。
        and acl_mod.domain_allows(domain, "create", actor.role_id)
    ):
        creator = None
        meeting = resource.get("meeting")
        if isinstance(meeting, Mapping):
            creator = meeting.get("created_by")
        creator = creator or resource.get("created_by")
        # 存量会议无 created_by → 例外不生效（保守，避免被当成"人人所有"）。
        if creator and actor.id and str(creator) == str(actor.id):
            return
    _deny(action, actor)


def _check_collection(
    actor: Actor, action: str, tier: str, resource: Mapping[str, Any]
) -> None:
    collection_id = resource.get("collection_id")
    if not collection_id or not isinstance(collection_id, str):
        # fail-closed：缺资源键 = 漏传 = 后门。拒绝并留下明显日志。
        logger.warning("authorize(%s) called without collection_id — denied", action)
        _deny(action, actor, not_found=True, message="collection not found")
    if acl_mod.collection_allows(collection_id, tier, actor.role_id):
        return
    # 没有 read → 不可见 → 404（与"不存在"不可区分）；有 read 但越权 → 403。
    if not acl_mod.collection_allows(collection_id, "read", actor.role_id):
        _deny(action, actor, not_found=True, message="collection not found")
    _deny(action, actor)


def authorize(actor: Actor, action: str, resource: Mapping[str, Any] | None = None) -> None:
    """放行则返回；拒绝则抛 ``HTTPException``（404 不可见 / 403 越权）。"""
    resource = resource or {}
    if action in PUBLIC_ACTIONS:
        return
    if is_owner(actor):
        return
    from src.edition import has_permission_management

    if not has_permission_management() and actor.role_id != "member":
        _deny(action, actor)

    resolved = resolve_action(action)
    if resolved is None:
        # 未登记的动作 → 拒绝。新动作必须先在 _EXACT 或前缀族里登记。
        logger.warning("authorize: unregistered action %r — denied", action)
        _deny(action, actor)

    domain, tier = resolved

    role = get_role(actor.role_id)
    if role is None:
        # 未知角色（数据损坏 / 老版本遗留）→ 拒绝，不回落 owner。
        logger.warning(
            "authorize: unknown role %r for actor %r — denied", actor.role_id, actor.id
        )
        _deny(action, actor)

    if domain == DOMAIN_BOX:
        _check_box(actor, action, tier, role)
        return
    if domain == DOMAIN_COLLECTION:
        _check_collection(actor, action, tier, resource)
        return
    _check_domain(actor, action, domain, tier, resource)


def can(actor: Actor, action: str, resource: Mapping[str, Any] | None = None) -> bool:
    """布尔版（后台任务 / 工具层挑选用）。"""
    try:
        authorize(actor, action, resource)
        return True
    except Exception:
        return False
