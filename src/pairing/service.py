from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import socket
import uuid
from datetime import datetime, timedelta, timezone

from . import store

logger = logging.getLogger("pairing")

TOKEN_PREFIX = "sd1_"
# 短命 access token（长短钥匙）：由长钥匙 HMAC 派生，TTL 秒级，不落盘
ACCESS_TOKEN_PREFIX = "sda1_"
ACCESS_TOKEN_TTL_SECONDS = 600
_LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"}
_LOOPBACK_PREFIXES = ("127.", "::ffff:127.")
_LAST_SEEN_THROTTLE = 60  # 秒：写盘节流窗口

# 本机专用端点：只有物理本地通道（回环客户端，或容器里的本机入口端口）能调用它，
# 用于静默发放主人钥匙。
LOCAL_KEY_PATH = "/api/pairing/local-key"
# 本机钥匙在设备钥匙列表之外的保留名（每盒最多一把）
LOCAL_KEY_NAME = "__local__"


class PairingError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def is_loopback_host(host: str | None) -> bool:
    """回环地址判定。桌面壳只绑回环，其客户端来源即回环——免钥匙。"""
    if not host:
        return False
    return host in _LOOPBACK or host.startswith(_LOOPBACK_PREFIXES)


def local_entry_port() -> int:
    """本机入口端口号 = 主 API 端口（默认 18900）。

    容器里宿主机只把这个端口发布到回环（127.0.0.1），因此「请求落在它上面」=
    请求来自盒子本机。对外（局域网）入口是另一个端口（默认 18901，需钥匙）。
    """
    from src.config import get_config, resolve_bind_port

    env = (os.environ.get("SINKDUCE_PORT") or "").strip()
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    try:
        return resolve_bind_port(get_config().server.api_port)
    except Exception:
        return 18900


def _server_port(scope) -> int | None:
    """请求落在哪个监听端口。uvicorn 在 scope['server'] 提供，客户端无法伪造。"""
    server = scope.get("server")
    if not server:
        return None
    try:
        return int(server[1])
    except (TypeError, ValueError, IndexError):
        return None


def is_local_scope(scope) -> bool:
    """本机判定（门卫与路由守卫共用）。

    两道闸先过，再看来源：
    1. **Host 必须是本机自己的地址** —— 防 DNS rebinding。攻击者页面在 evil.com:18900，
       DNS 重绑到本机后浏览器打 127.0.0.1 仍是「同源」请求（不带 Origin），只有 Host 能识破。
    2. **Origin 必须是本机自己** —— 防盒子浏览器里的恶意页面跨源直连本机端口。
    然后（**来源 IP 永不作为本机信号**）：
    - 客户端地址是回环 → 本机（桌面/裸机进程直连）；
    - 容器形态且请求落在本机入口端口 → 本机（宿主机只把它映射到回环）；
    - 其余一律不算本机，走钥匙。

    为什么不信「客户端 = 盒子自己的网卡地址」：同机 Docker 容器出站经 Docker Desktop
    NAT 后，源地址就是宿主机的网卡地址，与「本机浏览器用 LAN IP 打开」完全不可区分
    （2026-09-19 实弹审计：容器持**假钥匙**以 owner 打进桌面盒的管理面与 /mcp）。
    本机浏览器请用 127.0.0.1；用 LAN IP 打开的浏览器按局域网客户端处理（需钥匙）。
    """
    if not _host_is_local(scope):
        return False
    if not _origin_is_acceptable(scope):
        return False
    client = scope.get("client")
    client_host = client[0] if client else None
    if is_loopback_host(client_host):
        return True
    if _in_container():
        # 容器里客户端 IP 一律是网关地址（实测），没有区分度，身份只能靠端口映射：
        # 落在本机入口端口 = 宿主机只把它映射到回环 → 本机。
        return _server_port(scope) == local_entry_port()
    # 裸机 / 桌面：只认回环客户端（理由见 docstring——同机 NAT 下源地址不可区分）。
    return False


def is_local_request(request) -> bool:
    """本机判定（路由层用）。与门卫同一套逻辑，只是从 request 取 scope。

    注意不能只看 `request.client.host`：Docker 里客户端 IP 是网关地址，会把
    本机当外人（设置页配对卡片 401 → 前端弹钥匙门，即用户实测到的现象）。
    """
    scope = getattr(request, "scope", None)
    if isinstance(scope, dict):
        return is_local_scope(scope)
    client = getattr(request, "client", None)
    return is_loopback_host(client.host if client else None)


# 领钥匙端点的准入判定结果（门卫据此区分 401 / 403）
LOCAL_KEY_NONE = "none"  # 与本端点无关：走普通门禁
LOCAL_KEY_GRANT = "grant"  # 本机入口端口 + 本机 Host/Origin：放行
LOCAL_KEY_DENY = "deny"  # 本机入口端口但 Host/Origin 不像本机浏览器：403


def local_key_access(scope) -> str:
    """领钥匙端点的准入判定。

    只有「物理本地」通道才发钥匙——局域网设备即使伪造 Host 也拿不到：

    - 端口：scope['server'] 端口 == 本机入口端口；
    - **物理本地**（二选一）：
      * 客户端地址是回环（桌面/裸机：本机进程直连，无 NAT，地址可信）；
      * 或运行在容器里（宿主机把本机入口端口只映射到回环，网络层隔离）。
        ——容器内客户端地址一律是网关 IP，无法靠地址判断，故靠端口映射。
    - Host 必须回环 → 防 DNS rebinding（把 evil.com 解析到 127.0.0.1）；
    - Origin 必须是本机自己的来源（回环/盒子网卡）或缺失 → 防盒子浏览器内恶意网页跨源领钥匙。

    任一不符：端口不符 → NONE（走普通门禁 401）；端口符但其余不符 → DENY（403）。
    """
    if _server_port(scope) != local_entry_port():
        return LOCAL_KEY_NONE
    if not (_host_is_local(scope) and _origin_is_acceptable(scope)):
        return LOCAL_KEY_DENY
    client = scope.get("client")
    if is_loopback_host(client[0] if client else None):
        return LOCAL_KEY_GRANT
    if _in_container():
        return LOCAL_KEY_GRANT
    return LOCAL_KEY_DENY


def _in_container() -> bool:
    from src.config import in_container

    return in_container()


def _header(scope, name: str) -> str | None:
    for k, v in scope.get("headers") or []:
        if k.decode("latin-1").lower() == name:
            return v.decode("latin-1")
    return None


def _bare_host(value: str) -> str:
    """从 Host 头（可含端口）取出主机部分；IPv6 方括号一并剥掉。"""
    v = value.strip()
    if v.startswith("["):
        return v[1 : v.find("]")] if "]" in v else v
    return v.rsplit(":", 1)[0] if ":" in v else v


def _host_is_local(scope) -> bool:
    """Host 必须是本机自己的地址（回环，或盒子网卡地址）。

    这是防 DNS rebinding 的关键：攻击者页面在 evil.com:18900，DNS 重绑到本机后浏览器
    打 127.0.0.1 属「同源」（甚至不带 Origin），只有 Host 还是 evil.com，能据此识破。
    """
    raw = _header(scope, "host")
    if not raw:
        return False  # HTTP/1.1 必须带 Host；缺失即不可信
    host = _bare_host(raw)
    return is_loopback_host(host) or host in local_addresses()


def _origin_is_acceptable(scope) -> bool:
    """Origin 校验：只认「本机自己的来源」。

    浏览器对跨源请求必带 Origin（且网页改不了它），所以用它挡住「盒子浏览器里的恶意
    网页借本机端口读写数据」。放行范围 = 回环 + 盒子自己的网卡地址 + 无 Origin
    （同机 curl / MCP agent）。**不接受任意私网来源**——局域网里别人的服务器也是私网
    地址，放行它等于给恶意页面开正门。DNS rebinding 由 Host 校验兜住（见 is_local_scope）。
    """
    raw = _header(scope, "origin")
    if raw is None or not raw.strip():
        return True  # 非浏览器调用没有 Origin
    from urllib.parse import urlparse

    host = urlparse(raw.strip()).hostname
    if not host:
        return False
    return is_loopback_host(host) or host in local_addresses()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(24)


def _is_active(rec: dict) -> bool:
    if rec.get("revoked_at"):
        return False
    exp = rec.get("expires_at")
    if exp and _now() > datetime.fromisoformat(exp):
        return False
    return True


def _is_local_key(rec: dict) -> bool:
    return rec.get("name") == LOCAL_KEY_NAME


def _device_records(records: list[dict]) -> list[dict]:
    """设备钥匙（不含本机自动钥匙）：门禁与列表都只看这批。"""
    return [r for r in records if not _is_local_key(r)]


def can_issue(*, is_local: bool, has_valid_token: bool) -> bool:
    """默认拒绝：本机来源或持有效钥匙才能签发。"""
    return is_local or has_valid_token


def role_has_capability(role_id: str | None, capability: str) -> bool:
    """该角色是否有某管理能力（钥匙管理面准入用）。未知角色 fail-closed。"""
    from src.identity.roles import get_role

    role = get_role(role_id)
    return bool(role is not None and role.has(capability))


def can_grant_role(issuer_role, target_role) -> bool:
    from src.edition import has_permission_management
    if not has_permission_management():
        return issuer_role is None and target_role is not None and target_role.id == "member"
    from ee.pairing.service import can_grant_role as enterprise_can_grant_role
    return enterprise_can_grant_role(issuer_role, target_role)


def issue_token(
    *,
    name: str,
    ttl_days: int | None = None,
    sliding: bool = False,
    role: str = "member",
    is_local: bool = False,
    has_valid_token: bool = False,
    issuer_role: str | None = None,
) -> dict:
    if ttl_days is not None and ttl_days < 1:
        # 0 / 小数曾走 falsy 分支静默变「永久」——必须显式拒绝（2026-09-16）
        raise PairingError("invalid_ttl", "ttl_days must be a positive whole number of days")
    records = store.load()
    active = [r for r in _device_records(records) if _is_active(r)]
    if not can_issue(is_local=is_local, has_valid_token=has_valid_token):
        raise PairingError("pairing_required")

    from src.edition import has_permission_management

    if not has_permission_management():
        if not is_local or role != "member":
            raise PairingError("role_not_grantable", "only the local machine may issue Member keys")

    # 签发上限（RBAC）：本机 = owner（issuer_role=None）；否则用签发者钥匙自身的角色。
    from src.identity.roles import get_role

    target_role_def = get_role(role)
    if target_role_def is None:
        raise PairingError("unknown_role", f"unknown role {role!r}")
    effective_issuer_role = None if is_local else issuer_role
    if has_permission_management() and not can_grant_role(effective_issuer_role, target_role_def):
        raise PairingError(
            "role_not_grantable",
            f"cannot issue a {role!r} key with this identity",
        )

    box = store.ensure_box()
    token = generate_token()
    now = _iso(_now())
    rec = {
        "id": uuid.uuid4().hex,
        "name": name,
        "role": role,  # RBAC：角色随钥匙走（默认 member）
        "token_hash": hash_token(token),
        "token_hint": token[:10],
        "created_at": now,
        "expires_at": _iso(_now() + timedelta(days=ttl_days)) if ttl_days else None,
        # 滑动钥匙：活跃使用把 expires_at 顺延为「last_seen + ttl」；不用则到期即死。
        # 为将来账号钥匙预留（账号层落地即用），UI 暂不暴露。
        "sliding_ttl_days": ttl_days if sliding else None,
        "revoked_at": None,
    }
    records.append(rec)
    store.save(records)
    if has_permission_management() and role != "member":
        from src.edition import mark_enterprise_used

        mark_enterprise_used(store.PAIRING_DIR.parent)
    # 明文 token 永不落日志（CONN-1 验收项）
    logger.info("pairing token issued id=%s name=%r active=%d", rec["id"], name, len(active) + 1)
    return {
        "token": token,
        "id": rec["id"],
        "name": name,
        "box_id": box["box_id"],
        "created_at": rec["created_at"],
        "expires_at": rec["expires_at"],
    }


def verify_token(token: str | None) -> dict | None:
    if not token:
        return None
    h = hash_token(token)
    records = store.load()
    now = _now()
    for rec in records:
        if rec.get("token_hash") == h:
            if not _is_active(rec):
                return None
            # last_seen 写盘节流：CONN-2 的「最后活跃」展示数据源
            ls = rec.get("last_seen")
            if not ls or now - datetime.fromisoformat(ls) > timedelta(seconds=_LAST_SEEN_THROTTLE):
                rec["last_seen"] = _iso(now)
                # 滑动钥匙：活跃使用把 expires_at 顺延为「现在 + ttl」，与 last_seen 同一次写盘
                slide = rec.get("sliding_ttl_days")
                if slide:
                    rec["expires_at"] = _iso(now + timedelta(days=int(slide)))
                store.save(records)
            return dict(rec)
    return None


def revoke_token(rec_id: str) -> bool:
    records = store.load()
    for rec in records:
        if rec.get("id") == rec_id:
            if not rec.get("revoked_at"):
                rec["revoked_at"] = _iso(_now())
                store.save(records)
                logger.info("pairing token revoked id=%s", rec_id)
            return True
    return False


def set_token_role(rec_id: str, role: str, *, issuer_role: str | None = None) -> dict | None:
    from src.edition import has_permission_management
    if not has_permission_management():
        raise PairingError("enterprise_required")
    from ee.pairing.service import set_token_role as enterprise_set_token_role
    return enterprise_set_token_role(rec_id, role, issuer_role=issuer_role)


def list_tokens() -> list[dict]:
    """面向设置页的钥匙列表：只列设备钥匙（本机自动钥匙对用户不可见）。"""
    keys = ("id", "name", "role", "token_hint", "created_at", "last_seen", "expires_at", "revoked_at")
    return [
        {k: rec.get(k) for k in keys}
        for rec in store.load()
        if not _is_local_key(rec)
    ]


def active_token_count() -> int:
    """活跃设备钥匙数（不含本机自动钥匙）。"""
    return sum(1 for r in store.load() if _is_active(r) and not _is_local_key(r))


def local_addresses() -> list[str]:
    """尽力枚举本机非回环 IPv4（多网卡/容器环境可能不全，前端允许手改）。"""
    addrs: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                addrs.add(ip)
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("223.5.5.5", 80))  # UDP connect 不发包，仅取默认路由源地址
            addrs.add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    return sorted(addrs)


def ensure_local_key() -> dict:
    """确保存在一把活跃的本机自动钥匙，返回其明文（幂等）。

    本机前端从本机入口端口静默领走它，用户全程无感。该钥匙不计配额、
    不出现在钥匙列表里（对用户而言「本机 = 无钥匙」）。

    明文只存 recovery 文件（0600，data/pairing/local-key.txt）：本机登录用户
    可 `cat` 自取，Docker 里数据目录映射到宿主机时也能直接看到值。明文永不进
    tokens.json。文件丢失时直接轮换一把新的。
    """
    box = store.ensure_box()
    records = store.load()
    changed = _migrate_legacy_owner_key(records)
    # RBAC 迁移（2026-09-16）：存量设备钥匙的 role 是硬编码 "owner"，而 owner 现在
    # 定义为"只属于本机、不发钥匙"的安全锚。这些钥匙是主人自己的设备——保留管理
    # 能力（迁为 admin），但不再具备不可吊销的锚语义。
    changed = _migrate_owner_keys_to_admin(records) or changed
    if changed:
        store.save(records)

    existing = _read_recovery_token()
    if existing:
        h = hash_token(existing)
        for rec in records:
            if _is_local_key(rec) and rec.get("token_hash") == h and _is_active(rec):
                return _local_key_payload(existing, rec, box)

    # 没有可用的本机钥匙（首次启动 / 文件被删 / 被吊销）：吊销残留，发新的。
    rotated = False
    for rec in records:
        if _is_local_key(rec) and not rec.get("revoked_at"):
            rec["revoked_at"] = _iso(_now())
            rotated = True

    token = generate_token()
    rec = {
        "id": uuid.uuid4().hex,
        "name": LOCAL_KEY_NAME,
        "role": "owner",
        "token_hash": hash_token(token),
        "token_hint": token[:10],
        "created_at": _iso(_now()),
        "expires_at": None,
        "revoked_at": None,
    }
    records.append(rec)
    store.save(records)
    _write_recovery_file(token)
    logger.info("local auto key %s id=%s", "rotated" if rotated else "issued", rec["id"])
    return _local_key_payload(token, rec, box)


def _local_key_payload(token: str, rec: dict, box: dict) -> dict:
    return {
        "token": token,
        "id": rec["id"],
        "box_id": box["box_id"],
        "box_name": box["box_name"],
    }


def _read_recovery_token() -> str | None:
    try:
        return (store.PAIRING_DIR / "local-key.txt").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _migrate_owner_keys_to_admin(records: list[dict]) -> bool:
    """把存量设备钥匙的 ``role="owner"`` 迁为 ``admin``（RBAC，2026-09-16）。

    **本机自动钥匙 ``__local__`` 保持 owner**——它就代表本机。

    为什么必须迁：owner 在 RBAC 里被定义为**安全锚**（只属于本机、不发钥匙、
    不可被吊销）。若存量钥匙继续挂着 owner，就等于盒子上有多把"不可吊销的锚"，
    一旦其中一把泄露就踢不掉——锚的唯一性没了。

    迁成 admin 是恰当降级：它们本就是主人自己的设备，管理能力一个不少
    （`admin` 除 `key.manage` 的授予权以外与 owner 等同），只是不能再当锚。

    幂等：已经是 admin 的记录不动。
    """
    changed = False
    for rec in records:
        if _is_local_key(rec):
            continue  # 本机钥匙保持 owner
        if rec.get("role", "owner") == "owner":
            rec["role"] = "admin"
            changed = True
            logger.info("RBAC migration: key id=%s owner → admin", rec.get("id"))
    return changed


def _migrate_legacy_owner_key(records: list[dict]) -> bool:
    """清理旧方案的「手抄主人钥匙」（升级迁移）。

    旧版 ensure_owner_key 自动发一把名为 owner 的钥匙并把明文写进 owner-key.txt。
    新方案不再需要它——留着会占配额、在列表里显示成一把神秘设备钥匙。用
    owner-key.txt 的内容哈希精确比对（只吊销那一把），然后删除该文件。

    返回 True 表示有改动需要落盘。
    """
    legacy_path = store.PAIRING_DIR / "owner-key.txt"
    try:
        legacy_token = legacy_path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not legacy_token:
        return False

    h = hash_token(legacy_token)
    changed = False
    for rec in records:
        if rec.get("token_hash") == h and not rec.get("revoked_at"):
            rec["revoked_at"] = _iso(_now())
            logger.info("revoked legacy owner key during migration id=%s", rec.get("id"))
            changed = True
    try:
        legacy_path.unlink()
    except OSError:
        pass
    return changed


def _write_recovery_file(token: str) -> None:
    """把本机钥匙明文写到 data/pairing/local-key.txt（0600）。

    明文永不进 tokens.json；该文件是唯一副本，丢失时 ensure_local_key 直接轮换。
    """
    key_path = store.PAIRING_DIR / "local-key.txt"
    from src.atomic_io import write_text_atomic

    write_text_atomic(key_path, token + "\n", mode=0o600)
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass


def authenticate(token: str | None) -> dict | None:
    """门卫与路由共用的验票入口：按前缀分派长钥匙 / 短票。

    双接受契约：长钥匙路径一字不变（CLI/MCP/脚本永远可用）；短票验不过时
    调用方拿 None，与「没带钥匙」同义——前端回落长钥匙，正确性不依赖换票成功。
    """
    if not token:
        return None
    if token.startswith(ACCESS_TOKEN_PREFIX):
        return verify_access_token(token)
    return verify_token(token)


def mint_access_token(presented: str | None) -> dict:
    """持有效长钥匙换一张短命 access token（不落盘、不占配额）。

    只认长钥匙：短票在 tokens.json 里查无哈希，自然换不出新票——
    无限续期链从这里断掉（ possession of a short token never extends access）。
    """
    rec = verify_token(presented)
    if rec is None:
        raise PairingError("pairing_required")
    now = int(_now().timestamp())
    payload = json.dumps(
        {"kid": rec["id"], "exp": now + ACCESS_TOKEN_TTL_SECONDS},
        separators=(",", ":"),
    )
    body = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    sig = hmac.new(_load_access_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    return {
        "access_token": f"{ACCESS_TOKEN_PREFIX}{body}.{sig}",
        "expires_in": ACCESS_TOKEN_TTL_SECONDS,
        "key_id": rec["id"],
    }


def verify_access_token(token: str) -> dict | None:
    """验短票：HMAC 签名 + 有效期 + 绑定的长钥匙在 tokens.json 里仍活跃。

    最后一步读盘校验让「吊销长钥匙」即时杀死在飞短票——读盘成本与长钥匙
    路径相同（本来每请求都读）。
    """
    try:
        body, sig = token[len(ACCESS_TOKEN_PREFIX):].split(".")
    except ValueError:
        return None
    expected = hmac.new(_load_access_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        kid = payload["kid"]
        exp = int(payload["exp"])
    except (ValueError, KeyError, TypeError):
        return None
    if _now().timestamp() > exp:
        return None
    for rec in store.load():
        if rec.get("id") == kid:
            return dict(rec) if _is_active(rec) else None
    return None


def _load_access_secret() -> bytes:
    """短票签名密钥（0600，首次使用时生成）。轮换 = 在飞短票全部作废，无其他影响。"""
    path = store.PAIRING_DIR / "access-secret"
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing.encode("utf-8")
    except OSError:
        pass
    from src.atomic_io import write_text_atomic

    write_text_atomic(path, secrets.token_hex(32) + "\n", mode=0o600)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path.read_text(encoding="utf-8").strip().encode("utf-8")


def get_box_info() -> dict:
    box = store.ensure_box()
    return {**box, "addresses": local_addresses()}


def normalize_advertised_addr(raw: str) -> str:
    """对外地址归一化：只留 host[:port] 本体，去scheme/尾斜杠/空白。

    用户可能整段粘贴 `http://192.168.1.30:18901` 或裸 IP；存紧凑形态，
    前端拼链接时再自己决定要不要补协议和端口。
    """
    addr = (raw or "").strip()
    addr = re.sub(r"^https?://", "", addr, flags=re.IGNORECASE)
    return addr.rstrip("/").strip()


def set_box_profile(box_name: str, advertised_addr: str | None = None) -> dict:
    box = store.ensure_box()
    box["box_name"] = box_name.strip() or box["box_name"]
    # None = 未提供（保持原值）；空串 = 显式清除
    if advertised_addr is not None:
        addr = normalize_advertised_addr(advertised_addr)
        if addr:
            box["advertised_addr"] = addr
        else:
            box.pop("advertised_addr", None)
    store.save_box(box)
    logger.info("box profile updated id=%s name=%r", box["box_id"], box["box_name"])
    return box
