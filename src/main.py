from __future__ import annotations

import logging
import socket
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.services import init_services
from src.tasks import task_manager
from src.config import get_config

logging.getLogger("src").setLevel(logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("meeting").setLevel(logging.INFO)
logging.getLogger("src.meeting").setLevel(logging.INFO)
logging.getLogger("task_manager").setLevel(logging.INFO)
logging.getLogger("api").setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("qdrant_client").setLevel(logging.WARNING)

# Ensure loggers used in this project have a visible handler in the
# container. Uvicorn only configures its own loggers; without this, any
# logger named under "src.*" or "meeting" (e.g. "src.meeting.transcription
# .dashscope") has no destination and falls back to lastResort, which
# only emits WARNING+. Attach a single StreamHandler to the root and let
# propagation carry it to children.
#
# Guard against double-import: when launched via "python -m src.main",
# this module runs once as __main__ and again when uvicorn imports
# "src.main:app". Without the guard, we'd add duplicate handlers.
_root_handler = logging.StreamHandler()
_root_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
)
logging.getLogger().addHandler(_root_handler)

# Suppress expected FunASR internal warnings when using SenseVoiceSmall
# without passing punc_model to the main AutoModel (we use separate post-processing).
class _FunASRDiarizationFilter(logging.Filter):
    _SUPPRESSED = (
        "punc_model is missing, falling back to vad_segment mode",
        "No timestamp found in ASR result",
    )
    def filter(self, record: logging.LogRecord) -> bool:
        return not any(msg in record.getMessage() for msg in self._SUPPRESSED)

logging.getLogger().addFilter(_FunASRDiarizationFilter())


class _QueryTokenScrubFilter(logging.Filter):
    """把访问日志请求行里的 ``token=<明文>`` 换成 ``token=***``。

    为什么必须做：SSE/WS（EventSource、浏览器 WebSocket 带不了请求头）只能
    把配对钥匙放在 **query string**，而 uvicorn access log 会整行落盘——
    长钥匙 ``sd1_…`` 每次重连都会进日志文件，拿到日志文件/日志流的任何人都
    能捞走它（2026-09-19 生产就绪审查 P0）。只抹 query 里带 ``token=`` 的
    值，其余访问日志原样保留（排障工作流不受影响）。
    """

    import re as _re

    _TOKEN_RE = _re.compile(r"([?&]token=)[^&\s\"']+")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            if "token=" in message:
                scrubbed = self._TOKEN_RE.sub(r"\1***", message)
                if scrubbed != message:
                    record.msg = scrubbed
                    record.args = ()
        except Exception:
            pass  # 日志脱敏永不干扰请求处理
        return True


# 同时装到 uvicorn.access（访问日志）和根 logger（BroadcastHandler 会把
# uvicorn.access 的记录再广播一轮，见 api/routes/logs.py）。
_query_scrub = _QueryTokenScrubFilter()
for _name in ("uvicorn.access", ""):
    logging.getLogger(_name).addFilter(_query_scrub)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from src.edition import assert_data_compatible
    from src.config import DATA_DIR

    assert_data_compatible(DATA_DIR)
    # Register task handlers so the same set is shared between the FastAPI HTTP
    # routes and the MCP sub-app mounted at /mcp. Must happen before
    # task_manager.start() so handlers are available when tasks are dequeued.
    from src.tasks.handlers import (
        consolidate_handler,
        doc_summary_handler,
        meeting_extract_handler,
        meeting_summary_handler,
        meeting_transcript_index_handler,
        sparse_recalc_handler,
        upload_handler,
    )
    task_manager.register_handler("upload", upload_handler)
    task_manager.register_handler("consolidate", consolidate_handler)
    task_manager.register_handler("doc_summary", doc_summary_handler)
    task_manager.register_handler("sparse_recalc", sparse_recalc_handler)
    task_manager.register_handler("meeting_summary", meeting_summary_handler)
    task_manager.register_handler("meeting_extract", meeting_extract_handler)
    task_manager.register_handler("meeting_transcript_index", meeting_transcript_index_handler)

    from src.meeting.transcription.onnx.threads import configure_host_math_threads

    configure_host_math_threads()
    init_services()
    # A quit mid-rebuild leaves sparse_lock stuck True and every later
    # rebuild times out waiting for a dead holder — startup is the one
    # moment nothing can be holding it, so clear stale locks here.
    try:
        from src.services import services
        if services.db is not None:
            cleared = services.db.clear_sparse_locks()
            if cleared:
                logger.info("Cleared %d stale sparse rebuild lock(s)", cleared)
    except Exception:
        logger.warning("Sparse lock clear at startup failed", exc_info=True)
    from src.desktop_sysaudio import start_sysaudio_watchdog, stop_sysaudio_watchdog

    start_sysaudio_watchdog()
    await task_manager.start()
    # Load RapidOCR weights in the background so the first upload is not
    # blocked by ONNX session init. Failure is non-fatal (retried on ingest).
    import asyncio
    from src.parsers.rapid_ocr import warmup as warmup_rapidocr

    asyncio.get_running_loop().run_in_executor(None, warmup_rapidocr)
    # Recover stale processing states left by a previous crash/restart
    from src.meeting.service import reset_stale_processing_states
    # Ensure the local auto key exists so the box's own browser can pick it up
    # silently (the loopback-only port serves it). Never blocks startup.
    try:
        from src.pairing import service as _pairing_service

        _pairing_service.ensure_local_key()
    except Exception:
        logger.exception("ensure_local_key failed")
    await reset_stale_processing_states()

    # Re-enqueue upload tasks persisted by a previous crash so uploaded
    # files never sit un-ingested after a restart. Background + strong ref:
    # the settle delay lets Qdrant/models come up before the first embed.
    async def _recover_upload_tasks():
        # First fail the history rows the dead process left non-terminal —
        # except the ones recovery is about to re-enqueue (same task ids),
        # which stay queued and resume their lifecycle.
        try:
            from src.tasks import task_persistence, history_store
            pending_ids = {
                row["task_id"] for row in task_persistence.pending_upload_tasks()
            }
            marked = history_store.mark_interrupted(exclude_task_ids=pending_ids)
            if marked:
                logger.info("Marked %d ingest history row(s) interrupted", marked)
        except Exception:
            logger.warning("Ingest history interrupt-marking failed", exc_info=True)
        await task_manager.recover_upload_tasks()

    task_manager._recovery_task = asyncio.get_running_loop().create_task(
        _recover_upload_tasks()
    )

    # Start the MCP staging store (content side-channel for upload_document_from_staging)
    from src.mcp.staging import staging_store as _staging_store
    await _staging_store.start()

    # Start the FastMCP session manager in parallel so MCP Streamable HTTP
    # requests can spawn per-session background tasks. Sharing the FastAPI
    # lifespan keeps a single process for HTTP API + MCP.
    from src.mcp.server import session_lifespan as _mcp_lifespan
    async with _mcp_lifespan():
        yield

    await _staging_store.stop()
    await task_manager.stop()
    stop_sysaudio_watchdog()


# OpenAPI 文档面（/docs、/openapi.json、/redoc）不在门卫覆盖前缀内——开着
# 等于把全部端点清单公开给局域网任何设备（启动期路由覆盖断言抓出来的）。
# 默认关闭；需要调试时设 SINKDUCE_ENABLE_DOCS=1。
import os as _os

_docs_enabled = (_os.environ.get("SINKDUCE_ENABLE_DOCS") or "").strip() in {
    "1",
    "true",
    "yes",
}
if _docs_enabled:
    app = FastAPI(title="SinkDuce", version="1.1.0", lifespan=lifespan)
else:
    app = FastAPI(
        title="SinkDuce",
        version="1.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

# CONN-6: 配对门卫 — /api 全站强制（先于 CORS 注册，让 CORS 保持最外层）
from src.pairing.middleware import PairingGateMiddleware
app.add_middleware(PairingGateMiddleware)

# CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 请求体体积上限（2026-09-19 P2 背压）──────────────────────────
# 仓库里此前只有 staging 自己查大小（且在读完 body 之后）。一个失控客户端
# 可以用超大 Content-Length 把服务器拖进缓冲/OOM。这里在**读 body 之前**
# 按 Content-Length 拒绝。默认 2 GB（覆盖大 PDF/会议音频；staging 自身仍是
# 更严的 500 MB），可用 SINKDUCE_MAX_BODY_MB 调整，0 = 不限制。
from starlette.middleware.base import BaseHTTPMiddleware


class _BodySizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_bytes: int):
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        if self.max_bytes > 0:
            declared = request.headers.get("content-length")
            try:
                if declared and int(declared) > self.max_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "error": f"Payload too large "
                            f"(max {self.max_bytes // (1024 * 1024)} MB)"
                        },
                    )
            except ValueError:
                pass
        return await call_next(request)


_max_body_mb = 2048
try:
    _env_mb = int((_os.environ.get("SINKDUCE_MAX_BODY_MB") or "").strip() or 2048)
    _max_body_mb = max(0, _env_mb)
except ValueError:
    pass
if _max_body_mb > 0:
    app.add_middleware(_BodySizeLimitMiddleware, max_bytes=_max_body_mb * 1024 * 1024)


# ── Global error handling for provider/API errors ──────────

def _format_provider_error(exc: Exception) -> tuple[int, str]:
    """Map provider exceptions to (status_code, user_message)."""
    import httpx

    if isinstance(exc, httpx.ConnectError):
        return 503, "无法连接到远程服务，请检查 base_url 和网络"
    if isinstance(exc, httpx.TimeoutException):
        return 504, "远程服务响应超时，请稍后重试"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 401:
            return 401, "API Key 无效或已过期"
        if code == 403:
            return 403, "API 访问被拒绝，请检查权限"
        if code == 429:
            return 429, "请求频率超限，请稍后重试"
        if code >= 500:
            return 502, f"远程服务错误 (HTTP {code})"
        return code, f"远程服务返回错误: HTTP {code}"
    if isinstance(exc, ValueError):
        return 400, str(exc)
    return 500, f"服务内部错误: {type(exc).__name__}"


@app.middleware("http")
async def provider_error_middleware(request: Request, call_next):
    path = request.url.path
    # /api/foo/ → /api/foo so SPA catch-all never serves index.html for API slashes
    if (
        request.method in ("GET", "HEAD")
        and path.startswith("/api/")
        and path.endswith("/")
        and len(path) > 5
    ):
        from fastapi.responses import RedirectResponse

        q = request.url.query
        dest = path.rstrip("/") + (f"?{q}" if q else "")
        return RedirectResponse(url=dest, status_code=307)

    try:
        response = await call_next(request)
    except Exception as exc:
        status, message = _format_provider_error(exc)
        logger.error("Request %s %s failed: [%d] %s — %s",
                     request.method, request.url.path, status, message, exc)
        return JSONResponse(
            status_code=status,
            content={"error": message, "detail": str(exc)[:500]},
        )
    # Prevent CDN/browser from caching API JSON as HTML (or vice versa)
    if path.startswith("/api/") or path == "/health":
        response.headers["Cache-Control"] = "no-store"
    # Desktop WKWebView otherwise keeps a stale SPA shell (toolbar / fonts look unchanged)
    if path == "/" or path.endswith(".html"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response

from src.api.routes.query import router as query_router
from src.api.routes.documents import router as documents_router
from src.api.routes.collections import router as collections_router
from src.api.routes.config import router as config_router
from src.api.routes.recall import router as recall_router
from src.api.routes.logs import router as logs_router
from src.api.routes.info import router as info_router
from src.events.routes import router as events_router
from src.meeting.routes import router as meeting_router
from src.speakers.routes import router as speakers_router
from src.hot_words.routes import router as hot_words_router
from src.pairing.routes import router as pairing_router
from src.notes.routes import router as notes_router
from src.api.routes.sessions import router as sessions_router
from src.api.routes.chat_attachments import router as chat_attachments_router
from src.reports.routes import router as chat_reports_router
from src.mcp.staging_routes import router as mcp_staging_router

app.include_router(mcp_staging_router, prefix="/api")
app.include_router(sessions_router, prefix="/api")
app.include_router(chat_attachments_router, prefix="/api")
app.include_router(chat_reports_router, prefix="/api")
app.include_router(query_router, prefix="/api")
# ⚠️ documents 的 agent 端点必须**先于**主 router 挂载：/documents/text 会被
# 主 router 的 /documents/{collection} 参数路由抢先匹配（FastAPI 按注册顺序）。
from src.api.routes.documents import agent_router as documents_agent_router  # noqa: E402

app.include_router(documents_agent_router, prefix="/api")
app.include_router(documents_router, prefix="/api")
app.include_router(collections_router, prefix="/api")
app.include_router(config_router, prefix="/api")
app.include_router(recall_router, prefix="/api")
from src.api.routes.agent import agent_router, meeting_router as agent_meeting_router  # noqa: E402
from src.api.routes.search import router as agent_search_router  # noqa: E402
app.include_router(agent_search_router, prefix="/api")
app.include_router(agent_router, prefix="/api")
app.include_router(agent_meeting_router, prefix="/api")
app.include_router(logs_router, prefix="/api")
app.include_router(events_router, prefix="/api")
app.include_router(info_router, prefix="/api")
app.include_router(meeting_router, prefix="/api")
app.include_router(speakers_router, prefix="/api")
app.include_router(notes_router, prefix="/api")
app.include_router(hot_words_router)
app.include_router(pairing_router)

# RBAC：身份与权限管理（角色 / 领域 ACL / 每库 ACL）
from src.api.routes.identity import router as identity_router

app.include_router(identity_router)
from src.edition import has_permission_management
if has_permission_management():
    from ee.api.identity import router as ee_identity_router
    from ee.api.pairing import router as ee_pairing_router

    app.include_router(ee_identity_router)
    app.include_router(ee_pairing_router)
from src.file_mgmt.routes import agent_router as file_mgmt_agent_router
from src.file_mgmt.routes import router as file_mgmt_router
app.include_router(file_mgmt_router, prefix="/api/file-mgmt")
app.include_router(file_mgmt_agent_router, prefix="/api/file-mgmt")

# Hub（出站）：本机盒子拿哪把钥匙去访问别的盒子。**只许本机**——见
# src/hub/routes.py 的模块 docstring（它持有的是凭据，不是关于自己的信息）。
from src.hub.routes import router as hub_router
app.include_router(hub_router)

# 桌面端点（sysaudio 代理 + open-url）：与远程模式瘦壳共用同一份实现
# （src/api/routes/desktop.py），路径与鉴权面保持搬迁前一致。
from src.api.routes.desktop import router as desktop_router
app.include_router(desktop_router)


# ── MCP sub-app (mounted at /mcp) ─────────────────────────────
# The MCP server is mounted as an ASGI sub-app so it shares the same process,
# services singletons, and task_manager as the HTTP API. Clients connect via
# Streamable HTTP JSON-RPC at http://host:port/mcp.
#
# We register the sub-app via app.add_route rather than app.mount because
# Starlette.Mount builds a path regex of "<prefix>/{path:path}", which never
# matches the bare mount path (e.g. "/mcp" with no trailing slash). Using
# add_route with both "/mcp" and "/mcp/{path:path}" keeps the URL clean.
from starlette.routing import Route

from src.mcp.server import get_http_app

_mcp_handler = get_http_app()
app.router.routes.append(
    Route("/mcp", _mcp_handler, methods=["GET", "POST", "DELETE"])
)
app.router.routes.append(
    Route("/mcp/{path:path}", _mcp_handler, methods=["GET", "POST", "DELETE"])
)


# OpenRouter CORS proxy — registered directly on app so it's guaranteed
# to match before the SPA catch-all below.
@app.get("/api/proxy/openrouter-models")
async def proxy_openrouter_models():
    import httpx
    from fastapi.responses import JSONResponse
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            llm_r = await http.get("https://openrouter.ai/api/v1/models")
            emb_r = await http.get("https://openrouter.ai/api/v1/embeddings/models")
        llm = llm_r.json().get("data", []) if llm_r.status_code == 200 else []
        emb = emb_r.json().get("data", []) if emb_r.status_code == 200 else []
        return {"llm": llm, "embedding": emb}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/health")
def health():
    from src.config import health_payload

    return health_payload()


# Serve React frontend in production（远程模式瘦壳共用同一份挂载逻辑）
from src.spa_hosting import mount_spa

mount_spa(app)


# ── 启动期不变式：所有受保护路由必须挂在门卫覆盖的前缀下 ────────────
# 实现（与断言逻辑）已抽到 src/pairing/middleware.py 供瘦壳共用。
from src.pairing.middleware import assert_gate_route_coverage

assert_gate_route_coverage(app)


def plan_listeners(
    host: str, port: int, external_port: int, *, add_external: bool
) -> list[tuple[str, int]]:
    """需要绑定的 (host, port) 列表。

    主端口 = 本机入口（容器里宿主机只发布到回环）。容器形态额外绑一个对外端口供
    局域网访问（需钥匙）；桌面/裸机形态不开第二个口——它们的客户端来源就是回环，
    天然是本机（add_external=False）。
    """
    from src.pairing.service import is_loopback_host

    plan: list[tuple[str, int]] = [(host, port)]
    if add_external and external_port and external_port != port and not is_loopback_host(host):
        plan.append((host, external_port))
    return plan


def build_listeners(plan: list[tuple[str, int]]) -> list[socket.socket]:
    """按计划建立监听 socket。主监听失败是致命错误；本机口失败只降级。"""
    socks: list[socket.socket] = []
    for host, port in plan:
        try:
            family = socket.AF_INET6 if ":" in host else socket.AF_INET
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            sock.set_inheritable(True)
        except OSError:
            if not socks:
                raise
            logger.warning("Listener %s:%s unavailable; continuing without it", host, port)
            continue
        socks.append(sock)
    return socks


if __name__ == "__main__":
    import uvicorn

    from src.config import (
        get_config,
        in_container,
        resolve_bind_host,
        resolve_bind_port,
        resolve_external_port,
    )

    config = get_config()
    host = resolve_bind_host(config.server.host)
    port = resolve_bind_port(config.server.api_port)
    external_port = resolve_external_port(config.server.external_port)
    # ★ 条件性 fail-open 的启动校验（2026-09-19）：容器里 is_local 依据「请求
    # 打在本机入口端口」判定；若 external_port == api_port，plan_listeners 会
    # 静默跳过第二监听 → 对外口**就是**本机入口口 → 局域网任何人无钥匙即
    # owner。配置错了宁可拒绝启动，也不能带着洞上线。
    if in_container() and external_port and external_port == port:
        raise SystemExit(
            f"Refusing to start: external port ({external_port}) equals the local "
            f"entry port ({port}) — in a container this would make every LAN "
            "client count as local (owner). Set SINKDUCE_EXTERNAL_PORT to a "
            "different value."
        )
    # 只有容器需要第二个（对外）监听：它靠宿主机把主端口只发布到回环来认定本机。
    plan = plan_listeners(host, port, external_port, add_external=in_container())
    socks = build_listeners(plan)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, reload=False))
    if len(socks) > 1:
        logger.info(
            "Dual entry: local=%s:%s external=%s:%s", host, port, host, external_port
        )
    server.run(sockets=socks)
