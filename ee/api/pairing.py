"""Enterprise role assignment for existing device keys."""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Request
from src.pairing import service
from ee.pairing.models import SetRoleBody
from src.pairing.routes import _token_from_request

router = APIRouter(prefix="/api/pairing", tags=["pairing"])

@router.post("/set-role")
async def set_role(body: SetRoleBody, request: Request):
    """改一把已发出钥匙的角色（RBAC）。

    准入：本机 或 持 ``key.manage`` 的钥匙（与列表/吊销同档）。
    上限：复用签发规则——不能把一把钥匙"改成"超出自己能力范围的角色
    （否则「先发 member 再改 admin」就等效于签发 admin）。
    """
    is_local = service.is_local_request(request)
    rec = service.authenticate(_token_from_request(request))
    if not is_local and rec is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "pairing_required", "message": "pairing token required"},
        )
    if not is_local and not service.role_has_capability(rec.get("role"), "key.manage"):
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "key.manage capability required"},
        )
    try:
        updated = service.set_token_role(
            body.id,
            body.role,
            issuer_role=None if is_local else (rec or {}).get("role"),
        )
    except service.PairingError as exc:
        status = {
            "unknown_role": 400,
            "role_not_grantable": 403,
            "not_modifiable": 403,
        }.get(exc.code, 400)
        raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})
    if updated is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "unknown pairing token id"},
        )
    return {"ok": True, "role": updated.get("role")}
