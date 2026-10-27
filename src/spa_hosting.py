"""SPA 静态伺服（主 app 与远程模式瘦壳共用）。

从 ``src/main.py`` 原样搬迁的挂载逻辑：/assets 静态目录 + 全路径 catch-all
回落 index.html。搬迁动机见 development_doc/2026-09-21-remote-mode-design.md
§7 —— 瘦壳必须以与主 app 完全相同的方式伺服前端，加载路径零改动。

行为要点（勿改）：

- api/mcp/health 前缀绝不回落 index.html（否则 SPA 拿 HTML 当 JSON 解析）；
- html 响应一律 no-store（桌面 WKWebView 会抱着旧壳不放）；
- 路径穿越用 ``src.paths.confine`` 收口，逃逸时按 SPA 路由处理。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


def mount_spa(app: FastAPI, dist_dir: str | Path | None = None) -> None:
    from src.config import get_frontend_dist

    dist = Path(dist_dir) if dist_dir else get_frontend_dist()
    if not dist.exists():
        return

    app.mount(
        "/assets", StaticFiles(directory=dist / "assets"), name="static-assets"
    )

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
        file = dist / full_path
        try:
            from src.paths import confine

            confined = confine(file, dist)
        except ValueError:
            return FileResponse(dist / "index.html")
        if confined.is_file():
            resp = FileResponse(confined)
            if confined.suffix.lower() in {".html", ".htm"}:
                resp.headers["Cache-Control"] = "no-store, max-age=0"
            return resp
        html = FileResponse(dist / "index.html")
        html.headers["Cache-Control"] = "no-store, max-age=0"
        return html
