"""身份与授权（RBAC）。

- ``Actor`` / ``get_actor`` / ``set_actor`` / ``reset_actor``：请求级操作者。
- ``authorize`` / ``can``：动作闸门（策略引擎见 ``policy.py``）。
- ``visible_*`` / ``assert_can_read*``：可见集（隐藏的唯一裁决入口，见 ``visibility.py``）。
- ``roles`` / ``acl``：角色定义与 ACL 名单。
- ``audit``：审计钩子。

**子模块刻意不在这里 re-export**（避免 import 环）：按需 ``from src.identity import
policy`` / ``roles`` / ``acl`` / ``visibility``。
"""

from src.identity.actor import (
    ACTOR_LOCAL,
    ACTOR_SYSTEM,
    ROLE_OWNER,
    Actor,
    get_actor,
    reset_actor,
    set_actor,
    system_actor,
)
from src.identity.audit import audit
from src.identity.policy import authorize, can, is_owner, may_see_everything

__all__ = [
    "ACTOR_LOCAL",
    "ACTOR_SYSTEM",
    "ROLE_OWNER",
    "Actor",
    "audit",
    "authorize",
    "can",
    "get_actor",
    "is_owner",
    "may_see_everything",
    "reset_actor",
    "set_actor",
    "system_actor",
]
