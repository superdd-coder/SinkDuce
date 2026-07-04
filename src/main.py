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


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_services()
    await task_manager.start()
    # Recover stale processing states left by a previous crash/restart
    from src.meeting.service import reset_stale_processing_states
    await reset_stale_processing_states()
    yield
    await task_manager.stop()


app = FastAPI(title="SinkDuce", version="1.0.1", lifespan=lifespan)

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
    try:
        return await call_next(request)
    except Exception as exc:
        status, message = _format_provider_error(exc)
        logger.error("Request %s %s failed: [%d] %s — %s",
                     request.method, request.url.path, status, message, exc)
        return JSONResponse(
            status_code=status,
            content={"error": message, "detail": str(exc)[:500]},
        )

from src.api.routes.query import router as query_router
from src.api.routes.documents import router as documents_router
from src.api.routes.collections import router as collections_router
from src.api.routes.config import router as config_router
from src.api.routes.recall import router as recall_router
from src.api.routes.logs import router as logs_router
from src.api.routes.info import router as info_router
from src.meeting.routes import router as meeting_router
from src.hot_words.routes import router as hot_words_router
from src.notes.routes import router as notes_router
from src.api.routes.visual import router as visual_router
from src.api.routes.sessions import router as sessions_router

app.include_router(sessions_router, prefix="/api")
app.include_router(query_router, prefix="/api")
app.include_router(documents_router, prefix="/api")
app.include_router(collections_router, prefix="/api")
app.include_router(config_router, prefix="/api")
app.include_router(recall_router, prefix="/api")
app.include_router(logs_router, prefix="/api")
app.include_router(info_router, prefix="/api")
app.include_router(meeting_router, prefix="/api")
app.include_router(notes_router, prefix="/api")
app.include_router(visual_router, prefix="/api")
app.include_router(hot_words_router)


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
    return {"status": "ok"}


# Serve React frontend in production
FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="static-assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file = FRONTEND_DIST / full_path
        if file.is_file():
            return FileResponse(file)
        return FileResponse(FRONTEND_DIST / "index.html")


if __name__ == "__main__":
    import uvicorn
    from src.config import get_config
    config = get_config()
    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.api_port,
        reload=False,
    )
