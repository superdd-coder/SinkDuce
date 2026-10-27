from __future__ import annotations

from pydantic import BaseModel


class PairingTokenRecord(BaseModel):
    id: str
    name: str
    token_hash: str
    token_hint: str
    created_at: str
    expires_at: str | None = None
    revoked_at: str | None = None


class IssueBody(BaseModel):
    name: str
    ttl_days: int | None = None
    # RBAC：这把钥匙的角色。默认 member（最小惊讶：新钥匙不该比成员更大）。
    role: str = "member"
    # 滑动钥匙：活跃使用自动顺延有效期（账号层将来用，UI 暂不暴露）
    sliding: bool = False


class RevokeBody(BaseModel):
    id: str


class BoxNameBody(BaseModel):
    box_name: str
    # 对外（局域网）地址：容器部署时自动检测不可信，由用户自查 IP 后填写。
    # None = 本次不修改；空串 = 清除。
    advertised_addr: str | None = None
