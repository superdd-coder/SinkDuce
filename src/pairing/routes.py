from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from . import service
from .models import BoxNameBody, IssueBody, RevokeBody
from src.edition import has_permission_management

logger = logging.getLogger("pairing")
router = APIRouter(prefix="/api/pairing", tags=["pairing"])


def _token_from_request(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    tok = request.headers.get("x-pairing-token")
    if tok:
        return tok.strip() or None
    return request.query_params.get("token")


async def _require_local_or_token(request: Request):
    """钥匙**管理面**准入：本机，或持 `key.manage` 能力的钥匙（RBAC §7.4）。

    钥匙列表/吊销是管理动作——能看到所有设备钥匙、能踢设备。member/visitor 不该碰。
    """
    if service.is_local_request(request):
        return None
    if not has_permission_management():
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "local machine required"})
    rec = service.authenticate(_token_from_request(request))
    if rec is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "pairing_required", "message": "pairing token required"},
        )
    if not service.role_has_capability(rec.get("role"), "key.manage"):
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "key.manage capability required"},
        )
    return rec


@router.get("/local-key")
async def local_key(request: Request):
    """本机自动领钥匙：只在本机独占端口上可达（门卫已做端口 + Host + Origin 准入）。

    本机前端启动时静默调用它并把钥匙存进 localStorage——用户看不到「钥匙」这个
    概念，除非他要连别的设备。幂等：每次返回同一把活跃钥匙。

    路由层再守一道：门卫对「端口不符的领钥匙请求」返回 NONE 后会走普通门禁，
    于是**带有效钥匙的远端设备**也能穿过它打到本端点。领钥匙通道只该属于物理
    本机（2026-09-16 防御纵深补丁），非本机一律 403。
    """
    if not service.is_local_request(request):
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "not a local request"},
        )
    return service.ensure_local_key()


@router.post("/issue")
async def issue(body: IssueBody, request: Request):
    presented = _token_from_request(request)
    rec = service.authenticate(presented)
    try:
        result = service.issue_token(
            name=body.name,
            ttl_days=body.ttl_days,
            sliding=body.sliding,
            role=body.role,
            is_local=service.is_local_request(request),
            has_valid_token=rec is not None,
            # 签发者自身角色 → 上限判定的比较基准（本机时为 None = owner 全权）。
            issuer_role=(rec or {}).get("role"),
        )
    except service.PairingError as exc:
        status = {
            "invalid_ttl": 400,
            "unknown_role": 400,
            "role_not_grantable": 403,
        }.get(exc.code, 403)
        raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})
    return result


@router.get("/tokens")
async def list_tokens(request: Request):
    # 本机 / 持 key.manage 的钥匙可读（RBAC §7.4：钥匙列表属管理面）
    is_local = service.is_local_request(request)
    token = _token_from_request(request)
    rec = service.authenticate(token)
    if not is_local and rec is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "pairing_required", "message": "pairing token required"},
        )
    if not is_local and (not has_permission_management() or not service.role_has_capability(rec.get("role"), "key.manage")):
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "key.manage capability required"},
        )
    # 保留 limit 字段兼容老客户端；新版本没有设备钥匙数量上限。
    return {"tokens": service.list_tokens(), "limit": None}


@router.post("/access-token")
async def access_token(request: Request):
    """持长钥匙换短命 access token（前端暖票循环用）。

    只认长钥匙——短票在 tokens.json 里查无哈希，mint 直接拒（防无限续期链）。
    """
    try:
        return service.mint_access_token(_token_from_request(request))
    except service.PairingError as exc:
        raise HTTPException(
            status_code=401 if exc.code == "pairing_required" else 403,
            detail={"code": exc.code, "message": str(exc)},
        )


@router.post("/revoke")
async def revoke(body: RevokeBody, _: None = Depends(_require_local_or_token)):
    if not service.revoke_token(body.id):
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "unknown pairing token id"})
    return {"ok": True}


@router.get("/box")
async def get_box(request: Request):
    # 本机 / 有效 token 皆可读（默认拒绝，无引导期豁免）
    is_local = service.is_local_request(request)
    token = _token_from_request(request)
    rec = service.authenticate(token)
    if not is_local and rec is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "pairing_required", "message": "pairing token required"},
        )
    return service.get_box_info()


@router.put("/box")
async def set_box_name(body: BoxNameBody, request: Request):
    # 改名权限：本机 / 持有效钥匙者（默认拒绝，无引导期豁免）。
    # 来源 IP 在 VPN/代理环境下不可靠（实测会 NAT 成公网 IP），钥匙才是身份。
    is_local = service.is_local_request(request)
    if not is_local and not has_permission_management():
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "local machine required"})
    presented = _token_from_request(request)
    rec = service.authenticate(presented)
    if not is_local and rec is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "pairing_required", "message": "pairing token required"},
        )
    service.set_box_profile(body.box_name, body.advertised_addr)
    # 返回与 GET /box 同构的完整形态（含 addresses），前端整包替换状态
    return service.get_box_info()
