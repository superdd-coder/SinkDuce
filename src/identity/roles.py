"""Shared role contract and capability-based persistence.

Custom role persistence and editing live in ``ee.identity.roles``. The retained
restricted core profile exposes the local Owner and remote Member roles.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.config import DATA_DIR

logger = logging.getLogger("identity.roles")
_lock = threading.Lock()


IDENTITY_DIR = DATA_DIR / "identity"
ROLES_PATH = IDENTITY_DIR / "roles.json"

# ── 功能区（modules）───────────────────────────────────────────
# 只有这五个域有模块开关：它们没有另一套 ACL，开关就是唯一控制。
# **meeting / hot_words / people 不在此列**——那三域的"能不能访问"就是各自
# ACL 的 read 档（一个控制，不是两个）。见设计 §3.3 的修订说明。
MODULE_CHAT = "chat"
MODULE_DATABASE = "database"
MODULE_RECALL = "recall"
MODULE_TASKS = "tasks"
MODULE_SETTINGS = "settings"

ALL_MODULES: tuple[str, ...] = (
    MODULE_CHAT,
    MODULE_DATABASE,
    MODULE_RECALL,
    MODULE_TASKS,
    MODULE_SETTINGS,
)

# ── 管理能力（capabilities）─────────────────────────────────────
CAP_CONFIG_WRITE = "config.write"          # 改全局配置（LLM/embedding/reranker…）
CAP_KEY_MANAGE = "key.manage"              # 签发/吊销配对钥匙
CAP_ACL_MANAGE = "acl.manage"              # 改角色定义与 ACL 名单
CAP_COLLECTION_CREATE = "collection.create"  # 建新知识库（结构级操作）
CAP_BOX_MANAGE = "box.manage"              # 改盒子档案（名称/对外地址）

ALL_CAPABILITIES: tuple[str, ...] = (
    CAP_CONFIG_WRITE,
    CAP_KEY_MANAGE,
    CAP_ACL_MANAGE,
    CAP_COLLECTION_CREATE,
    CAP_BOX_MANAGE,
)

#: 只有本机（owner）能授予的能力——签发上限的核心（设计 §7.1）。
#: 持它才可能造出"能再管钥匙的钥匙"，因此它不能绕一圈自己长出来。
OWNER_ONLY_CAPABILITIES: frozenset[str] = frozenset({CAP_KEY_MANAGE})


@dataclass(frozen=True)
class Role:
    """角色定义。``builtin=True`` 的角色只读（不可改名、不可改权限）。"""

    id: str
    name: str
    modules: dict[str, bool] = field(default_factory=dict)
    capabilities: dict[str, bool] = field(default_factory=dict)
    builtin: bool = False
    #: 能否被签发给钥匙。owner 为 False（本机身份隐含 owner，无需钥匙）。
    assignable: bool = True
    #: 可否删除/编辑。内置角色为 False。
    editable: bool = True

    def allows_module(self, module: str) -> bool:
        return bool(self.modules.get(module, False))

    def has(self, capability: str) -> bool:
        return bool(self.capabilities.get(capability, False))

    @property
    def capability_set(self) -> frozenset[str]:
        return frozenset(k for k, v in self.capabilities.items() if v)

    @property
    def module_set(self) -> frozenset[str]:
        return frozenset(k for k, v in self.modules.items() if v)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "builtin": self.builtin,
            "assignable": self.assignable,
            "editable": self.editable,
            "modules": dict(self.modules),
            "capabilities": dict(self.capabilities),
        }


def _all_on(keys: tuple[str, ...]) -> dict[str, bool]:
    return {k: True for k in keys}


def _all_off(keys: tuple[str, ...]) -> dict[str, bool]:
    return {k: False for k in keys}


# ── 内置四角色（设计 §3.2）─────────────────────────────────────
# owner/admin/member 全模块；visitor 定位=只读知识库（能进 chat/database）。
# 注意：member 与 visitor 的差别**不在这里**——是"能不能写数据"（在 collection ACL 里）
# 以及"有没有会议/热词/人物三域"（在领域 ACL 里）。

BUILTIN_ROLES: dict[str, Role] = {
    "owner": Role(
        id="owner",
        name="Owner",
        builtin=True,
        assignable=False,   # 只属于本机，不发钥匙
        editable=False,
        modules=_all_on(ALL_MODULES),
        capabilities=_all_on(ALL_CAPABILITIES),
    ),
    "admin": Role(
        id="admin",
        name="Admin",
        builtin=True,
        assignable=True,
        editable=False,
        modules=_all_on(ALL_MODULES),
        capabilities=_all_on(ALL_CAPABILITIES),
    ),
    "member": Role(
        id="member",
        name="Member",
        builtin=True,
        assignable=True,
        editable=False,
        modules=_all_on(ALL_MODULES),
        # 无管理能力：设置页只看得到「界面语言」。也不能建库（结构级操作）。
        capabilities=_all_off(ALL_CAPABILITIES),
    ),
    "visitor": Role(
        id="visitor",
        name="Visitor",
        builtin=True,
        assignable=True,
        editable=False,
        # 只读知识库：进得了 chat / database，进不了任务与回顾。
        # 会议/热词/人物三域由领域 ACL 排除（默认名单里没有 visitor）。
        modules={
            MODULE_CHAT: True,
            MODULE_DATABASE: True,
            MODULE_RECALL: False,
            MODULE_TASKS: False,
            MODULE_SETTINGS: True,  # 设置页可进（只显示语言项）
        },
        capabilities=_all_off(ALL_CAPABILITIES),
    ),
}

BUILTIN_IDS: frozenset[str] = frozenset(BUILTIN_ROLES)

#: 角色 id 前缀（自定义角色）。用不可预测的 hex 避免与内置名冲突、也便于识别。
CUSTOM_PREFIX = "role_"


class RoleError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


def is_builtin(role_id: str) -> bool:
    return role_id in BUILTIN_IDS


def load_roles() -> dict[str, Role]:
    from src.edition import has_permission_management
    if has_permission_management():
        from ee.identity.roles import load_roles as enterprise_load_roles
        return enterprise_load_roles()
    return {key: BUILTIN_ROLES[key] for key in ("owner", "member")}


def get_role(role_id: str | None) -> Role | None:
    return load_roles().get(role_id) if role_id else None


def list_roles() -> list[Role]:
    from src.edition import has_permission_management
    if has_permission_management():
        from ee.identity.roles import list_roles as enterprise_list_roles
        return enterprise_list_roles()
    return [BUILTIN_ROLES[key] for key in ("owner", "member")]


def create_custom_role(*, name: str, modules: dict[str, bool] | None = None,
                       capabilities: dict[str, bool] | None = None) -> Role:
    from src.edition import has_permission_management
    if not has_permission_management():
        raise RoleError("enterprise_required")
    from ee.identity.roles import create_custom_role as enterprise_create_role
    return enterprise_create_role(name=name, modules=modules, capabilities=capabilities)


def update_custom_role(role_id: str, *, name: str | None = None,
                       modules: dict[str, bool] | None = None,
                       capabilities: dict[str, bool] | None = None) -> Role:
    from src.edition import has_permission_management
    if not has_permission_management():
        raise RoleError("enterprise_required")
    from ee.identity.roles import update_custom_role as enterprise_update_role
    return enterprise_update_role(role_id, name=name, modules=modules, capabilities=capabilities)


def delete_custom_role(role_id: str) -> None:
    from src.edition import has_permission_management
    if not has_permission_management():
        raise RoleError("enterprise_required")
    from ee.identity.roles import delete_custom_role as enterprise_delete_role
    enterprise_delete_role(role_id)


def roles_path() -> Path:
    return ROLES_PATH
