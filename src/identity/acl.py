"""Shared ACL contract and capability-based persistence.

Configurable persistence lives in ``ee.identity.acl``. This module keeps
stable read and write entry points for REST, search, MCP, and background tasks.

ACL 名单（RBAC 的"对具体资源能做什么"一半）。

两类，同一个形状（名单上写"允许哪些角色 id"）：

- **领域 ACL**（``data/identity/domain_acl.json``）：meeting / hot_words / people
  三域**全盒子一份**。这三域不按对象细分（用户明确：不对单个会议做细粒度），
  所以"能否访问"与 `read` 档是同一件事——**不再给角色加模块开关**（避免同一件事
  两处能配）。
- **collection ACL**（各库 ``data/collections/{id}/meta.json`` 的 ``acl`` 键）：
  **每库一份**。因为"对哪个库能写"天然是 per-collection 的事——放进 ACL 后，
  member 与 visitor 的差别不需要任何角色字段，自定义角色也无需额外开关。

**档位包含关系**（保存时强制）：每个域 `read` 是地基档，其余档位必须是它的子集
（``create ⊆ read``、``operate/write ⊆ read``、``delete ⊆ read``）。能创建却看不到
自己创建的会议是荒谬配置；这条也让"能否访问"始终只有一个答案。

**owner / admin 是隐式角色**（2026-09-17 用户定）：这两者永远拥有每一档，因此在
ACL 里**不是可勾选项**——UI 不展示，数据也由 ``_with_implicit`` 在每个档位上强制并上。
理由：一个关不掉的开关比没有开关更糟（UI 上「取消勾选 admin」是句谎话），而把它们
当成"能被移除的普通角色"会让名单出现两种真相（存了 admin vs 没存 admin，行为相同）。
写入时并上、读取时并上，所以手改过或历史遗留的名单也会被自愈。

**默认值**：缺省一律按默认求值，**读时默认、不写迁移**——存量库不迁移。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from src.config import DATA_DIR

logger = logging.getLogger("identity.acl")

IDENTITY_DIR = DATA_DIR / "identity"
DOMAIN_ACL_PATH = IDENTITY_DIR / "domain_acl.json"

# ── 域与档位 ───────────────────────────────────────────────────

DOMAIN_MEETING = "meeting"
DOMAIN_HOT_WORDS = "hot_words"
DOMAIN_PEOPLE = "people"

#: 每域合法的档位（顺序即 UI 展示顺序）。
DOMAIN_TIERS: dict[str, tuple[str, ...]] = {
    # 用户 2026-09-17 重定（三档：读取 / 创建 / 管理）：
    #   read   → 纯读取（列表、转写稿、回放、总结、简报）
    #   create → 能建会议，并且**对自己建的会议拥有一切权限**（录制、转写、上传、
    #            改名、归档、删除都行）——"创建权限应该是可以创建转录以及操作自己的
    #            Meeting，有自己创建的 Meeting 的所有权限"
    #   manage → 能操作 / 删除**别人的**会议（以及跨会议的会议组）
    # 旧的第 2 档叫 `operate`，语义是"操作含删除"，且创建者例外挂在它上面 → 结果
    # 是"能建但用不了"（member 有 create 无 operate，建完连保存转写都被拒），
    # 用户实测后要求改成现在这样。见 `policy._check_domain` 的判定。
    DOMAIN_MEETING: ("read", "create", "manage"),
    DOMAIN_HOT_WORDS: ("read", "write", "delete"),
    # people.read 语义**含写**（enroll 写声纹）——UI 文案「查看与标注」，
    # 不是只读档。见设计 §3.3④ 的命名警告。
    DOMAIN_PEOPLE: ("read", "create", "write", "delete"),
}

#: 档位改名兼容：旧 key → 现 key。存量 `domain_acl.json` 里写的是 `operate`，
#: 读的时候按新名 `manage` 收下，**不静默丢弃**（丢配置等于悄悄改权限）。
_TIER_ALIASES: dict[str, str] = {
    "operate": "manage",
}

#: 地基档：其余档位必须是它的子集。
BASE_TIER = "read"

#: collection ACL 档位。
COLLECTION_TIERS: tuple[str, ...] = ("read", "write", "delete", "config")

#: 隐式角色：永远拥有每一档，**不出现在 ACL 名单的可选项里**（UI 过滤 + 数据强制并上）。
#: owner = 本机安全锚；admin = "和 Owner 权限差不多"。两者在 policy 层另有短路
#: （``may_see_everything``），这里并进名单是为了**数据自描述**——任何一处直接查
#: ``collection_allows``/``domain_allows`` 的代码都不会因为"名单里没写 admin"而误拒。
IMPLICIT_ROLES: tuple[str, ...] = ("owner", "admin")

#: 默认名单里**只列非隐式角色**；隐式角色由 ``_with_implicit`` 并上（唯一真相）。
_MEMBER_ONLY: tuple[str, ...] = ("member",)


def _with_implicit(role_ids: Any) -> list[str]:
    """规整成「隐式角色在前 + 去重保序」的名单。读写两侧都过它，所以手改过的名单自愈。"""
    out: list[str] = []
    given = (
        [str(x) for x in role_ids if isinstance(x, str) and x]
        if isinstance(role_ids, list)
        else []
    )
    for role_id in (*IMPLICIT_ROLES, *given):
        if role_id not in out:
            out.append(role_id)
    return out


def _explicit_only(role_ids: Any) -> list[str]:
    """去掉隐式角色——落盘/传输用的**显式名单**（避免两处存同一事实）。"""
    if not isinstance(role_ids, list):
        return []
    return [
        str(x) for x in role_ids if isinstance(x, str) and x and x not in IMPLICIT_ROLES
    ]


#: 领域 ACL 默认值（设计 §3.3①）。**隐式角色不在此列出**（见 ``IMPLICIT_ROLES``）。
#: **visitor 不出现在任何档位**——visitor = 只读知识库的人，与会议/热词/人物无关。
DEFAULT_DOMAIN_ACL: dict[str, dict[str, list[str]]] = {
    DOMAIN_MEETING: {
        # member 能读、能建；建完对自己那个有一切权限（见 policy 的创建者判定）。
        "read": list(_MEMBER_ONLY),
        "create": list(_MEMBER_ONLY),
        # 管**别人**的：默认只给隐式角色（owner/admin）→ 列表为空。
        "manage": [],
    },
    DOMAIN_HOT_WORDS: {
        "read": list(_MEMBER_ONLY),
        "write": list(_MEMBER_ONLY),
        "delete": [],
    },
    DOMAIN_PEOPLE: {
        "read": list(_MEMBER_ONLY),
        "create": list(_MEMBER_ONLY),
        "write": list(_MEMBER_ONLY),
        "delete": [],
    },
}

#: collection ACL 默认值（设计 §3.3③）。同样只列非隐式角色。
DEFAULT_COLLECTION_ACL: dict[str, list[str]] = {
    "read": ["member", "visitor"],
    "write": ["member"],
    "delete": [],
    "config": [],
}

_lock = threading.Lock()


class AclError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


# ── 领域 ACL ───────────────────────────────────────────────────


def default_domain_acl() -> dict[str, dict[str, list[str]]]:
    return {
        d: {t: _with_implicit(v) for t, v in tiers.items()}
        for d, tiers in DEFAULT_DOMAIN_ACL.items()
    }


def _normalize_domain_acl(raw: Any) -> dict[str, dict[str, list[str]]]:
    """把磁盘内容规整成完整形状：缺失的域/档位回落默认值，忽略未知键。

    兼容两种外形：``{"version":…, "domains": {…}}``（落盘格式）与直接 ``{domain: {…}}``
    （请求体/内存格式）。**写进去的形状必须能读回来**——形状不一致会让保存静默失效。
    """
    out = default_domain_acl()
    if not isinstance(raw, dict):
        return out
    body = raw.get("domains") if isinstance(raw.get("domains"), dict) else raw
    for domain, tiers in DOMAIN_TIERS.items():
        given = body.get(domain)
        if not isinstance(given, dict):
            continue
        for tier in tiers:
            value = given.get(tier)
            # 档位改过名（operate → manage）时按别名回收旧值，**别静默丢弃**
            # （丢配置等于悄悄改权限）。新名有值时以新名为准。
            if not isinstance(value, list):
                for old, new in _TIER_ALIASES.items():
                    if new == tier and isinstance(given.get(old), list):
                        value = given.get(old)
                        break
            if isinstance(value, list):
                out[domain][tier] = _with_implicit(value)
    return out


def load_domain_acl() -> dict[str, dict[str, list[str]]]:
    from src.edition import has_permission_management
    if has_permission_management():
        from ee.identity.acl import load_domain_acl as enterprise_load_domain_acl
        return enterprise_load_domain_acl()
    return default_domain_acl()


def save_domain_acl(acl: dict[str, dict[str, list[str]]]) -> dict[str, dict[str, list[str]]]:
    from src.edition import has_permission_management
    if not has_permission_management():
        raise AclError("enterprise_required")
    from ee.identity.acl import save_domain_acl as enterprise_save_domain_acl
    return enterprise_save_domain_acl(acl)


def validate_domain_acl(acl: dict[str, dict[str, list[str]]]) -> None:
    from src.edition import has_permission_management
    if not has_permission_management():
        raise AclError("enterprise_required")
    from ee.identity.acl import validate_domain_acl as enterprise_validate_domain_acl
    enterprise_validate_domain_acl(acl)


def domain_tier_roles(domain: str, tier: str) -> list[str]:
    acl = load_domain_acl()
    return list((acl.get(domain) or {}).get(tier) or [])


def domain_allows(domain: str, tier: str, role_id: str) -> bool:
    return role_id in domain_tier_roles(domain, tier)


def can_access_domain(role_id: str, domain: str) -> bool:
    """能否访问某域 = 该域 read 档是否含此角色。"""
    return domain_allows(domain, BASE_TIER, role_id)


def save_domain_acl_payload(payload: dict[str, Any]) -> dict[str, Any]:
    from src.edition import has_permission_management
    if not has_permission_management():
        raise AclError("enterprise_required")
    from ee.identity.acl import save_domain_acl_payload as enterprise_save_payload
    return enterprise_save_payload(payload)


def default_collection_acl() -> dict[str, list[str]]:
    return {t: _with_implicit(v) for t, v in DEFAULT_COLLECTION_ACL.items()}


def canonical_collection_id(collection_id: str) -> str:
    """把任意形态的库标识收敛到 **meta 的规范 id**，解析不了就原样返回。

    为什么需要（2026-09-17 排查时发现）：ACL 存在 ``meta.json`` 里、以 **meta.id** 为键，
    而 **可见集枚举走的是 Qdrant 的库名**——存量/重命名的库里这两者可能不同
    （本机实测 40 个 meta 里有 34 个 ``id != qdrant_name``）。若直接拿 Qdrant 名去查
    ACL，会查不到 meta 而回落默认名单 = 把配过权限的库悄悄放行。这里统一收敛，
    免得每个调用点各判一套。

    解析顺序：meta.id 精确命中 → ``qdrant_name`` 命中 → 原样返回（调用方按未知处理）。
    对非法 id（含空格等）``get_collection_meta`` 会抛错，此时继续走 qdrant_name 路径 —
    这也顺带让"用显示名/库名来查 ACL"能落到正确的那份配置上。
    """
    from src.collections.store import find_collection_by_qdrant_name, get_collection_meta

    try:
        if get_collection_meta(collection_id) is not None:
            return collection_id
    except Exception:
        pass
    try:
        meta = find_collection_by_qdrant_name(collection_id)
    except Exception:
        meta = None
    if meta and meta.get("id"):
        return str(meta["id"])
    return collection_id


def collection_acl(collection_id: str) -> dict[str, list[str]]:
    from src.edition import has_permission_management, IncompatibleEditionData
    if has_permission_management():
        from ee.identity.acl import collection_acl as enterprise_collection_acl
        return enterprise_collection_acl(collection_id)
    from src.collections.store import get_collection_meta

    canonical = canonical_collection_id(collection_id)
    try:
        meta = get_collection_meta(canonical)
    except Exception:
        return {tier: [] for tier in COLLECTION_TIERS}
    if meta is not None and "acl" in meta:
        raise IncompatibleEditionData(f"collection ACL requires enterprise edition: {canonical}")
    return {
        "read": ["owner", "member"],
        "write": ["owner", "member"],
        "delete": ["owner"],
        "config": ["owner"],
    }


def collection_tier_roles(collection_id: str, tier: str) -> list[str]:
    return list(collection_acl(collection_id).get(tier) or [])


def collection_allows(collection_id: str, tier: str, role_id: str) -> bool:
    return role_id in collection_tier_roles(collection_id, tier)


def save_collection_acl(collection_id: str, acl: dict[str, Any]) -> dict[str, list[str]]:
    from src.edition import has_permission_management
    if not has_permission_management():
        raise AclError("enterprise_required")
    from ee.identity.acl import save_collection_acl as enterprise_save_collection_acl
    return enterprise_save_collection_acl(collection_id, acl)


def acl_path() -> Path:
    return DOMAIN_ACL_PATH
