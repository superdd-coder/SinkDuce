"""Hub 出站钥匙托管的路由（``/api/hub/boxes``）。

**准入判定只有一个：必须是本机。**（``_require_local``）

为什么这么严 —— 这是本模块的全部安全要点：

- ``boxes.json`` 装的是**这台盒子的出站钥匙明文集合**（我去别人家的钥匙）。
  它不是「关于我自己的信息」，而是**凭据**。
- 它能被读 = 攻击者拿到这台盒子能访问的所有其他盒子的钥匙；
  它能被写 = 攻击者可以往里塞一台恶意盒子，之后本机 Hub 会带着钥匙去访问它
  （或者把已存的地址改指向自己，做中间人）。
- 远端持有**有效 member 钥匙**的设备也不行：那把钥匙表达的是「你能访问我的知识库」，
  **不是**「你能管理我出站到谁」。两者是不同的权限面。

**为什么不做成「本机或 key.manage」**：``/api/pairing/*`` 那个管理面用的是
``key.manage``（发/撤**入站**钥匙）。但「我能去访问谁」比「谁能来访问我」更敏感：
``key.manage`` 是 admin 档能力，而 admin 是**可被签发出去的**角色（``can_grant_role``
允许 admin 授 admin）。若这里也认 ``key.manage``，则一把 admin 钥匙（可能就在别人的
笔记本上）就能把本机 Hub 的出站钥匙全导走。所以这里**只认本机**
（``is_local_request``），不开放给任何钥匙——与「MCP 只经回环可达」是同一条纪律的另一面。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.hub import store
from src.pairing import service as pairing_service

logger = logging.getLogger("hub.routes")

router = APIRouter(prefix="/api/hub", tags=["hub"])


def _require_local(request: Request) -> None:
    """出站钥匙托管只许本机（模块 docstring 解释了为什么比 key.manage 还严）。"""
    if not pairing_service.is_local_request(request):
        # 404 而非 403：与 /mcp 同一条思路——不该让远端知道「这里有个出站钥匙库」。
        raise HTTPException(status_code=404, detail="Not Found")


class BoxItem(BaseModel):
    box_id: str
    box_name: str = ""
    addr: str
    token: str = ""
    last_checked: str = ""


class SyncBody(BaseModel):
    boxes: list[BoxItem] = Field(default_factory=list)


@router.get("/boxes")
def list_boxes(request: Request):
    """列出本机 Hub 持有的远端盒子。

    **返回体故意不含 token 明文**：浏览器那份已经在自己 localStorage 里（它是镜像的
    来源），回传钥匙只会多一次无谓的暴露面；需要钥匙的是 Hub 后端自己，它进程内直接读文件。
    只回一个 ``has_token`` 布尔，够 UI 显示「这台我有没有钥匙」。
    """
    _require_local(request)
    return {
        "boxes": [
            {
                "box_id": b.get("box_id", ""),
                "box_name": b.get("box_name", ""),
                "addr": b.get("addr", ""),
                "last_checked": b.get("last_checked", ""),
                "online": b.get("online"),
                "role": b.get("role", ""),
                "has_token": bool(str(b.get("token") or "").strip()),
            }
            for b in store.list_boxes()
        ]
    }


@router.put("/boxes")
def sync_boxes(body: SyncBody, request: Request):
    """用浏览器那份整体替换（幂等；删除天然被覆盖——见 ``store.replace_boxes``）。"""
    _require_local(request)
    out = store.replace_boxes([b.model_dump() for b in body.boxes])
    return {
        "boxes": [
            {"box_id": b.get("box_id", ""), "addr": b.get("addr", ""), "has_token": bool(b.get("token"))}
            for b in out
        ]
    }
