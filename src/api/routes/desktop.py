"""桌面专属端点（主 app 与远程模式瘦壳共用的同一路由模块）。

从 ``src/main.py``（sysaudio 三个代理）与 ``src/api/routes/config.py``
（open-url）原样搬迁：路径、鉴权面、错误语义全部保持不变 —— 主 app 包含
本 router 后行为逐字节一致（由既有测试守护）；瘦壳（src/remote_shell）
复用同一份实现，保证远程模式下的录音链路与外链打开和今天完全相同。

鉴权面说明（与搬迁前一致，勿动）：

- ``sysaudio-*`` / ``pcm``：无端点级闸门，只靠 PairingGateMiddleware
  （/api 前缀全站强制）。今天如此，零差异要求不许顺手加闸。
- ``open-url``：今天挂在 config router 上，路由器级依赖
  ``_config_write_gate``（config.write 档）。搬出来必须显式带上同一个
  依赖，否则 member 会凭空多出 open-url 权限。
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from src.api.schemas import DesktopAudioSettingsRequest

router = APIRouter(prefix="/api/desktop", tags=["desktop"])

# 与 config router 同一个闸门（模块 docstring 解释了为什么读+写都收口）。
from src.api.routes.config import _config_write_gate  # noqa: E402


def _sysaudio_base() -> str:
    import os

    from src.config import is_desktop_runtime

    if not is_desktop_runtime():
        raise HTTPException(404, "not a desktop runtime")
    base = (os.environ.get("SINKDUCE_SYS_AUDIO") or "").strip().rstrip("/")
    if not base:
        raise HTTPException(
            503,
            "Desktop audio helper is not running. Quit SinkDuce with Cmd+Q and reopen.",
        )
    return base


@router.post(
    "/open-url", dependencies=[Depends(_config_write_gate)]
)
def desktop_open_url(payload: dict = Body(...)):
    """Open an http(s) URL in the system browser. Desktop runtime only."""
    from src.config import is_desktop_runtime
    from src.desktop_open import allowed_external_url, open_external_url

    if not is_desktop_runtime():
        raise HTTPException(status_code=403, detail="desktop only")
    raw = payload.get("url") if isinstance(payload, dict) else None
    url = allowed_external_url(raw if isinstance(raw, str) else "")
    if not url:
        raise HTTPException(status_code=400, detail="url must be http(s)")
    open_external_url(url)
    return {"ok": True}


@router.post("/sysaudio-warmup")
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


@router.post("/sysaudio-stop")
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


@router.get("/pcm")
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
        raise HTTPException(503, f"Audio helper unreachable: {exc}") from exc
    if r.status_code != 200:
        body = (await r.aread())[:800]
        await r.aclose()
        await client.aclose()
        import json

        detail = body.decode("utf-8", "replace")
        try:
            parsed = json.loads(detail)
            if isinstance(parsed, dict) and isinstance(parsed.get("error"), str):
                detail = {"code": parsed["error"], "message": parsed.get("message", parsed["error"])}
        except (ValueError, TypeError):
            pass
        raise HTTPException(r.status_code, detail)

    async def gen():
        try:
            async for chunk in r.aiter_bytes():
                if chunk:
                    yield chunk
        finally:
            await r.aclose()
            await client.aclose()

    return StreamingResponse(gen(), media_type="application/octet-stream")


def _require_local_desktop(request: Request) -> None:
    from src.config import is_desktop_runtime
    from src.pairing.service import is_local_request

    if not is_desktop_runtime() or not is_local_request(request):
        raise HTTPException(404, "Not Found")


@router.get("/audio-permissions")
async def desktop_audio_permissions(request: Request):
    """Read current native permission state without requesting access."""
    import httpx
    from fastapi.responses import JSONResponse

    _require_local_desktop(request)
    base = _sysaudio_base()
    try:
        async with httpx.AsyncClient(timeout=4) as http:
            response = await http.get(f"{base}/permissions")
        return JSONResponse(status_code=response.status_code, content=response.json())
    except Exception as exc:
        raise HTTPException(503, "Audio helper is not responding") from exc


@router.post("/audio-settings")
def desktop_audio_settings(request: Request, payload: DesktopAudioSettingsRequest):
    """Open one of two fixed privacy panes on this computer."""
    import subprocess
    import sys

    _require_local_desktop(request)
    if sys.platform != "darwin":
        raise HTTPException(404, "macOS only")
    pane = {"microphone": "Privacy_Microphone", "screen_recording": "Privacy_ScreenCapture"}[payload.target]
    subprocess.Popen(["open", f"x-apple.systempreferences:com.apple.preference.security?{pane}"])
    return {"ok": True}
