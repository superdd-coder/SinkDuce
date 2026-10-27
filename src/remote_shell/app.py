"""远程模式瘦壳 app。

端点白名单（其余一律 404，见 tests/test_remote_shell_app.py）：

- ``/`` + 静态资源 —— SPA（与主 app 同一份 mount_spa）
- ``/health`` —— health_payload()，瘦壳运行时带 desktop + remote_shell 标志
- ``/api/desktop/*`` —— 录音代理 + open-url（与主 app 同一份 router）
- ``/api/hub/boxes`` —— 前端连接列表镜像（MCP Hub 转发的数据源）
- ``/api/shell-settings`` —— 受限读写：仅 locale / remote_mode
- ``/mcp`` —— 整个 MCP app（62 工具注册 + Hub 转发全保留）

刻意**不做**的事（远程模式铁律，见设计文档 §3/§7）：不 ``init_services``、
不启动任务队列、不预热 OCR、不加载任何本地模型、不伺服任何业务路由。
``services.db is None`` 由测试钉住 —— 瘦壳进程里它必须保持 None。

安全姿态：桌面壳把绑定地址强制为 127.0.0.1（main.rs + 本模块 ``__main__``
双保险）；PairingGateMiddleware 照挂（/mcp 只许本机回环），纵深防御。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.routing import Route

from src.api.routes.desktop import router as desktop_router
from src.hub.routes import router as hub_router
from src.pairing.middleware import PairingGateMiddleware, assert_gate_route_coverage
from src.spa_hosting import mount_spa

# /api/shell-settings 的全部可写面。加键 = 加产品的壳级设置，必须同步
# tests/test_remote_shell_app.py 的白名单断言。provider/密钥类配置永远
# 不许出现在这里 —— 该端点部署在没有身份体系的瘦壳进程里。
_SHELL_SETTING_KEYS = ("locale", "remote_mode")
_SHELL_LOCALES = ("en", "zh-CN")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # sysaudio 看门狗：壳负责首次拉起，本进程负责崩溃拉起（与主 app 同一份
    # 实现；依赖 SINKDUCE_DESKTOP=1 + SINKDUCE_SYS_AUDIO，桌面壳都会注入）。
    from src.desktop_sysaudio import start_sysaudio_watchdog, stop_sysaudio_watchdog

    start_sysaudio_watchdog()
    # MCP Streamable HTTP 的 per-session task group —— 与主 app 同一份
    # session manager，跨盒转发/钥匙托管零差别。
    from src.mcp.server import session_lifespan as _mcp_lifespan

    async with _mcp_lifespan():
        yield
    stop_sysaudio_watchdog()


def _shell_settings_view() -> dict:
    from src.config import get_config

    cfg = get_config()
    return {"locale": cfg.locale, "remote_mode": cfg.remote_mode}


def _require_local_shell_settings(request) -> None:
    """shell-settings 只许本机（与 /api/hub/boxes 同一条纪律）。

    这里的键不是「关于自己的信息」就是**能改启动形态的开关**——有效钥匙
    （局域网 member）也不行。桌面形态本来就只绑回环；容器形态靠 is_local
    的「本机入口端口」规则。404 而非 403：不让远端知道这个面存在。
    """
    from src.pairing import service as pairing_service

    if not pairing_service.is_local_request(request):
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Not Found")


def create_app() -> FastAPI:
    app = FastAPI(
        title="SinkDuce",
        version="1.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # 与主 app 相同的中间件序：先门卫后 CORS（CORS 保持最外层）。
    app.add_middleware(PairingGateMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health():
        from src.config import health_payload

        return health_payload()

    @app.get("/api/shell-settings")
    def get_shell_settings(request: Request):
        _require_local_shell_settings(request)
        return _shell_settings_view()

    @app.patch("/api/shell-settings")
    def patch_shell_settings(request: Request, payload: dict = Body(...)):
        _require_local_shell_settings(request)
        from src.config import get_config, save_config

        unknown = sorted(set(payload.keys()) - set(_SHELL_SETTING_KEYS))
        if unknown:
            raise HTTPException(400, f"unknown shell setting keys: {unknown}")
        updates: dict = {}
        if "locale" in payload:
            if payload["locale"] not in _SHELL_LOCALES:
                raise HTTPException(400, "locale must be one of: en, zh-CN")
            updates["locale"] = payload["locale"]
        if "remote_mode" in payload:
            if not isinstance(payload["remote_mode"], bool):
                raise HTTPException(400, "remote_mode must be a boolean")
            updates["remote_mode"] = payload["remote_mode"]

        cfg = get_config()
        for key, value in updates.items():
            setattr(cfg, key, value)
        save_config(cfg)
        return _shell_settings_view()

    app.include_router(desktop_router)
    app.include_router(hub_router)

    # MCP sub-app —— 与主 app 相同的注册方式（add_route 而非 mount，
    # 保证裸路径 /mcp 可达；见 src/main.py 同段注释）。
    from src.mcp.server import get_http_app

    _mcp_handler = get_http_app()
    app.router.routes.append(
        Route("/mcp", _mcp_handler, methods=["GET", "POST", "DELETE"])
    )
    app.router.routes.append(
        Route("/mcp/{path:path}", _mcp_handler, methods=["GET", "POST", "DELETE"])
    )

    mount_spa(app)
    # 与主 app 同一条启动期不变式：受保护路由必须落在门卫覆盖的前缀下。
    assert_gate_route_coverage(app)
    return app


def resolve_shell_bind() -> tuple[str, int]:
    """瘦壳监听地址。

    桌面壳：强制回环（本机无内容可分享，LAN 不应触达；SINKDUCE_HOST 被误设
    也改写——main.rs 已强制，这里是第二道保险）。

    容器（Docker 远程模式）：**绝不能强绑回环**——容器里绑 127.0.0.1 会让宿
    主机的端口映射转发不进去（accept=0，本机入口直接失联），必须按 config/
    环境正常解析（compose 默认 0.0.0.0）。LAN 的隔离由两层承担：compose 只把
    18900 发布到宿主机回环；门卫对非本机请求要钥匙。
    """
    import os

    from src.config import get_config, is_desktop_runtime, resolve_bind_port

    port = resolve_bind_port(get_config().server.api_port)
    if is_desktop_runtime():
        return "127.0.0.1", port
    return get_config().server.host or "0.0.0.0", port


def main() -> None:
    """``python -m src.remote_shell`` 入口。

    远程模式瘦壳永远只绑回环：即使 SINKDUCE_HOST 被误设成对外地址也在
    这里强制改写（main.rs 侧已强制，这里是第二道保险 —— 设计文档 §10）。
    """
    import logging
    import os

    import uvicorn

    logging.getLogger("src").setLevel(logging.INFO)
    logger = logging.getLogger("remote_shell")

    forced_host, port = resolve_shell_bind()
    requested = (os.environ.get("SINKDUCE_HOST") or "").strip()
    if requested and requested != forced_host:
        logger.warning(
            "Remote shell bind resolved to %s (ignoring SINKDUCE_HOST=%s)",
            forced_host,
            requested,
        )
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host=forced_host, port=port, reload=False)
    )
    logger.info("Remote shell listening on %s:%s", forced_host, port)
    server.run()


if __name__ == "__main__":
    main()
