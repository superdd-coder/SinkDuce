from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from src.services import init_services
from src.tasks import task_manager
from src.config import get_config, get_frontend_dist

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Register task handlers so the same set is shared between the FastAPI HTTP
    # routes and the MCP sub-app mounted at /mcp. Must happen before
    # task_manager.start() so handlers are available when tasks are dequeued.
    from src.tasks.handlers import (
        consolidate_handler,
        doc_summary_handler,
        meeting_extract_handler,
        meeting_summary_handler,
        sparse_recalc_handler,
        upload_handler,
    )
    task_manager.register_handler("upload", upload_handler)
    task_manager.register_handler("consolidate", consolidate_handler)
    task_manager.register_handler("doc_summary", doc_summary_handler)
    task_manager.register_handler("sparse_recalc", sparse_recalc_handler)
    task_manager.register_handler("meeting_summary", meeting_summary_handler)
    task_manager.register_handler("meeting_extract", meeting_extract_handler)

    from src.meeting.transcription.onnx.threads import configure_host_math_threads

    configure_host_math_threads()
    init_services()
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
    await reset_stale_processing_states()

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


app = FastAPI(title="SinkDuce", version="1.1.0", lifespan=lifespan)

# CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
from src.meeting.routes import router as meeting_router
from src.speakers.routes import router as speakers_router
from src.hot_words.routes import router as hot_words_router
from src.notes.routes import router as notes_router
from src.api.routes.sessions import router as sessions_router
from src.mcp.staging_routes import router as mcp_staging_router

app.include_router(mcp_staging_router, prefix="/api")
app.include_router(sessions_router, prefix="/api")
app.include_router(query_router, prefix="/api")
app.include_router(documents_router, prefix="/api")
app.include_router(collections_router, prefix="/api")
app.include_router(config_router, prefix="/api")
app.include_router(recall_router, prefix="/api")
app.include_router(logs_router, prefix="/api")
app.include_router(info_router, prefix="/api")
app.include_router(meeting_router, prefix="/api")
app.include_router(speakers_router, prefix="/api")
app.include_router(notes_router, prefix="/api")
app.include_router(hot_words_router)
from src.file_mgmt.routes import router as file_mgmt_router
app.include_router(file_mgmt_router, prefix="/api/file-mgmt")


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
def _sysaudio_base() -> str:
    import os

    from src.config import is_desktop_runtime

    if not is_desktop_runtime():
        from fastapi import HTTPException

        raise HTTPException(404, "not a desktop runtime")
    base = (os.environ.get("SINKDUCE_SYS_AUDIO") or "").strip().rstrip("/")
    if not base:
        from fastapi import HTTPException

        raise HTTPException(
            503,
            "Desktop audio helper is not running. Quit SinkDuce with Cmd+Q and reopen.",
        )
    return base


@app.post("/api/desktop/sysaudio-warmup")
async def desktop_sysaudio_warmup():
    import httpx
    from fastapi.responses import JSONResponse

    base = _sysaudio_base()
    try:
        async with httpx.AsyncClient(timeout=8) as http:
            r = await http.post(f"{base}/warmup")
        return JSONResponse(status_code=r.status_code, content={"ok": r.is_success})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)


@app.post("/api/desktop/sysaudio-stop")
async def desktop_sysaudio_stop():
    import httpx
    from fastapi.responses import JSONResponse

    base = _sysaudio_base()
    try:
        async with httpx.AsyncClient(timeout=4) as http:
            await http.post(f"{base}/stop")
        return {"ok": True}
    except Exception:
        return JSONResponse({"ok": False}, status_code=200)


@app.get("/api/desktop/pcm")
async def desktop_pcm():
    """Same-origin PCM proxy. WKWebView cannot fetch :18950 (TypeError: Load failed)."""
    import httpx
    from fastapi.responses import StreamingResponse

    base = _sysaudio_base()
    client = httpx.AsyncClient(timeout=None)
    try:
        req = client.build_request("GET", f"{base}/pcm")
        r = await client.send(req, stream=True)
    except Exception as exc:
        await client.aclose()
        from fastapi import HTTPException

        raise HTTPException(503, f"Audio helper unreachable: {exc}") from exc
    if r.status_code != 200:
        body = (await r.aread())[:800]
        await r.aclose()
        await client.aclose()
        from fastapi import HTTPException

        raise HTTPException(r.status_code, body.decode("utf-8", "replace"))

    async def gen():
        try:
            async for chunk in r.aiter_bytes():
                if chunk:
                    yield chunk
        finally:
            await r.aclose()
            await client.aclose()

    return StreamingResponse(gen(), media_type="application/octet-stream")


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


# Serve React frontend in production
FRONTEND_DIST = get_frontend_dist()

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="static-assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Never return index.html for API/MCP — that yields "got HTML" JSON parse
        # errors in the SPA when a route is missing or the path is wrong.
        if (
            full_path == "api"
            or full_path.startswith("api/")
            or full_path == "mcp"
            or full_path.startswith("mcp/")
            or full_path == "health"
            or full_path.startswith("health/")
        ):
            return JSONResponse({"detail": f"Not Found: /{full_path}"}, status_code=404)
        file = FRONTEND_DIST / full_path
        try:
            from src.paths import confine

            confined = confine(file, FRONTEND_DIST)
        except ValueError:
            return FileResponse(FRONTEND_DIST / "index.html")
        if confined.is_file():
            resp = FileResponse(confined)
            if confined.suffix.lower() in {".html", ".htm"}:
                resp.headers["Cache-Control"] = "no-store, max-age=0"
            return resp
        html = FileResponse(FRONTEND_DIST / "index.html")
        html.headers["Cache-Control"] = "no-store, max-age=0"
        return html


if __name__ == "__main__":
    import uvicorn
    from src.config import get_config
    from src.config import resolve_bind_host, resolve_bind_port

    config = get_config()
    uvicorn.run(
        app,
        host=resolve_bind_host(config.server.host),
        port=resolve_bind_port(config.server.api_port),
        reload=False,
    )
