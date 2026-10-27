"""远程模式瘦壳 app（``src/remote_shell``）。

对应 development_doc/2026-09-21-remote-mode-design.md §7：

- 端点白名单：静态 SPA、/health、/api/desktop/*、/api/hub/boxes、
  /api/shell-settings、/mcp；**其余一律 404**（瘦壳绝不代理业务面）。
- lifespan 只启动 MCP session manager + sysaudio 看门狗；
  ``init_services`` 绝不运行（``services.db is None`` 可观察）。
- /api/shell-settings 是受限读写面：白名单键仅 locale/remote_mode，
  写穿到 config.yaml（测试里重定向 CONFIG_PATH，绝不碰真实 data/）。

隔离注意：``src.hub.store.HUB_DIR`` 是 import 期常量，fixture 必须
monkeypatch 到 tmp —— 否则会写进开发者真实的 data/hub/boxes.json。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def shell_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1);", encoding="utf-8")

    monkeypatch.setenv("SINKDUCE_DATA", str(data))
    monkeypatch.setenv("SINKDUCE_REMOTE_SHELL", "1")
    monkeypatch.setenv("SINKDUCE_DESKTOP", "1")
    monkeypatch.setenv("SINKDUCE_HOST", "127.0.0.1")
    monkeypatch.setenv("SINKDUCE_PORT", "18911")
    monkeypatch.delenv("SINKDUCE_SYS_AUDIO", raising=False)
    monkeypatch.setenv("SINKDUCE_FRONTEND_DIST", str(dist))
    monkeypatch.setattr("src.config.CONFIG_PATH", data / "config.yaml")
    monkeypatch.setattr("src.hub.store.HUB_DIR", data / "hub")
    return data


@pytest.fixture()
def client(shell_env):
    from src.remote_shell.app import create_app

    # 刻意不用 `with`：不触发 lifespan。MCP 的 StreamableHTTPSessionManager
    # 是进程级单例（.run() 只许一次），每个测试都跑 lifespan 会在第二个测试
    # 就撞死。需要 lifespan 的测试单独写（见 test_lifespan_never_initializes_services）。
    # client=("127.0.0.1", …)：门卫的桌面形态本机判定要求回环客户端地址
    # （TestClient 默认是 "testclient"，会被当局域网外人 → 401）。
    return TestClient(
        create_app(), base_url="http://127.0.0.1:18911", client=("127.0.0.1", 50123)
    )


# ── health 与模式标志 ──────────────────────────────────────


def test_health_reports_desktop_and_remote_shell(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["desktop"] is True
    assert body["remote_shell"] is True


# ── 端点白名单：业务面一律 404 ─────────────────────────────


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/collections"),
        ("GET", "/api/config"),
        ("GET", "/api/meetings"),
        ("GET", "/api/logs"),
        ("POST", "/api/query"),
        ("POST", "/api/documents/upload"),
        ("GET", "/api/pairing/tokens"),
        ("GET", "/api/pairing/local-key"),
    ],
)
def test_business_routes_are_404(client, method, path):
    res = client.request(method, path)
    # 405 与主 app 行为一致：SPA 的 GET catch-all 让「路径不存在但方法不同」
    # 变成 405（src/spa_hosting.py 与主 app 同一份逻辑）。关键不变式是
    # 「绝不 200 / 绝不吐 HTML」——瘦壳绝不伺服业务面。
    assert res.status_code in (404, 405), f"{method} {path} must not exist on the shell"
    assert "text/html" not in res.headers.get("content-type", "")


# ── SPA 伺服 ───────────────────────────────────────────────


def test_spa_serves_index_and_assets(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "spa" in res.text
    assert "no-store" in res.headers.get("cache-control", "")

    asset = client.get("/assets/app.js")
    assert asset.status_code == 200
    assert "console.log" in asset.text


def test_spa_fallback_returns_index_for_client_routes(client):
    res = client.get("/settings")
    assert res.status_code == 200
    assert "spa" in res.text


# ── /api/shell-settings：受限读写面 ────────────────────────


def test_shell_settings_get_returns_whitelist_only(client):
    res = client.get("/api/shell-settings")
    assert res.status_code == 200
    assert set(res.json().keys()) == {"locale", "remote_mode"}
    assert res.json() == {"locale": "en", "remote_mode": False}


def test_shell_settings_patch_roundtrip_writes_config(client, shell_env):
    res = client.patch(
        "/api/shell-settings", json={"locale": "zh-CN", "remote_mode": True}
    )
    assert res.status_code == 200
    assert res.json() == {"locale": "zh-CN", "remote_mode": True}

    # 落盘（瘦壳重启后壳据此决定启动形态）
    on_disk = (shell_env / "config.yaml").read_text(encoding="utf-8")
    assert "remote_mode: true" in on_disk
    assert "zh-CN" in on_disk

    assert client.get("/api/shell-settings").json() == {
        "locale": "zh-CN",
        "remote_mode": True,
    }


def test_shell_settings_patch_rejects_unknown_keys(client):
    res = client.patch("/api/shell-settings", json={"developer_mode": True})
    assert res.status_code == 400
    # 拒绝即不生效
    assert client.get("/api/shell-settings").json()["remote_mode"] is False


def test_shell_settings_patch_rejects_invalid_locale(client):
    res = client.patch("/api/shell-settings", json={"locale": "fr"})
    assert res.status_code == 400


def test_shell_settings_get_never_leaks_provider_config(client, shell_env):
    """config.yaml 里有 provider 密钥也不许从设置面吐出来。"""
    (shell_env / "config.yaml").write_text(
        "remote_mode: false\nlocale: en\nllm:\n  providers:\n"
        "    - id: p1\n      name: secret-provider\n      api_key: sk-secret\n",
        encoding="utf-8",
    )
    body = client.get("/api/shell-settings").json()
    assert set(body.keys()) == {"locale", "remote_mode"}
    assert "sk-secret" not in str(body)


# ── hub 镜像路由（MCP 转发的数据源）────────────────────────


def test_hub_boxes_sync_roundtrip(client):
    put = client.put(
        "/api/hub/boxes",
        json={
            "boxes": [
                {
                    "box_id": "box_remote1",
                    "box_name": "Studio Box",
                    "addr": "http://10.0.0.5:18900",
                    "token": "sd1_token",
                }
            ]
        },
    )
    assert put.status_code == 200

    listing = client.get("/api/hub/boxes").json()["boxes"]
    assert len(listing) == 1
    assert listing[0]["box_id"] == "box_remote1"
    assert listing[0]["has_token"] is True
    assert "token" not in listing[0]  # 明文绝不出门


# ── desktop 代理与 open-url（与主 app 共用同一路由模块）────


def test_sysaudio_warmup_503_without_helper(client):
    res = client.post("/api/desktop/sysaudio-warmup")
    assert res.status_code == 503


def test_open_url_works_on_shell(client, monkeypatch):
    monkeypatch.setattr("src.desktop_open.open_external_url", lambda url: None)
    res = client.post("/api/desktop/open-url", json={"url": "https://example.com"})
    assert res.status_code == 200
    assert res.json() == {"ok": True}


# ── MCP 挂载 ───────────────────────────────────────────────


def test_mcp_endpoint_present_and_local_only(shell_env):
    from src.remote_shell.app import create_app

    app = create_app()
    paths = [getattr(r, "path", None) for r in app.router.routes]
    assert "/mcp" in paths
    assert "/mcp/{path:path}" in paths

    # 门卫在请求到达 MCP 处理器之前就拦截：非本机来源 → 假 404
    # （不暴露 MCP 面存在，与主 app 同一纪律；不触发 session manager，
    # 因此无需 lifespan）。
    from fastapi.testclient import TestClient as _TC

    client = _TC(
        app, base_url="http://127.0.0.1:18911", client=("127.0.0.1", 50123)
    )
    spoofed = client.get(
        "/mcp",
        headers={"Host": "evil.example.com", "Origin": "http://evil.example.com"},
    )
    assert spoofed.status_code == 404


# ── lifespan 不初始化 services ─────────────────────────────


def test_lifespan_never_initializes_services(shell_env, monkeypatch):
    """瘦壳 lifespan 绝不能跑 init_services（否则 Qdrant/模型全被拉起来）。

    直接调 lifespan 函数并替换掉两个重副作用（init_services 换成记录器、
    MCP session manager 换成 no-op）——不经过 TestClient，避免与 MCP
    单例 session manager 冲突，也与测试顺序无关。
    """
    import asyncio
    from contextlib import asynccontextmanager

    calls: list[int] = []
    monkeypatch.setattr("src.services.init_services", lambda *a, **k: calls.append(1))

    @asynccontextmanager
    async def _noop_session_lifespan():
        yield

    monkeypatch.setattr(
        "src.mcp.server.session_lifespan", _noop_session_lifespan
    )
    monkeypatch.delenv("SINKDUCE_SYS_AUDIO", raising=False)

    from src.remote_shell.app import lifespan

    async def _run():
        async with lifespan(None):
            pass

    asyncio.run(_run())
    assert calls == []


# ── Docker 远程模式：bind 解析与 shell-settings 本机闸 ─────────────


def test_shell_bind_forces_loopback_only_on_desktop(monkeypatch, tmp_path):
    """桌面壳：无视 SINKDUCE_HOST 强制回环（本机无内容可分享）。"""
    from src.remote_shell.app import resolve_shell_bind

    monkeypatch.setenv("SINKDUCE_DESKTOP", "1")
    monkeypatch.setenv("SINKDUCE_HOST", "0.0.0.0")
    monkeypatch.setenv("SINKDUCE_PORT", "18910")
    monkeypatch.setattr("src.config.CONFIG_PATH", tmp_path / "config.yaml")
    assert resolve_shell_bind() == ("127.0.0.1", 18910)


def test_shell_bind_follows_config_in_container(monkeypatch, tmp_path):
    """容器：绝不能强绑回环（宿主机端口映射转发不进去），按 config/env 解析。"""
    from src.remote_shell.app import resolve_shell_bind

    monkeypatch.delenv("SINKDUCE_DESKTOP", raising=False)
    monkeypatch.delenv("SINKDUCE_HOST", raising=False)
    monkeypatch.delenv("SINKDUCE_PORT", raising=False)
    monkeypatch.setattr("src.config.CONFIG_PATH", tmp_path / "config.yaml")
    host, port = resolve_shell_bind()
    assert host == "0.0.0.0"  # ServerConfig 默认
    assert port == 18900

    monkeypatch.setenv("SINKDUCE_HOST", "0.0.0.0")
    monkeypatch.setenv("SINKDUCE_PORT", "18900")
    assert resolve_shell_bind() == ("0.0.0.0", 18900)


def test_shell_settings_rejects_non_local_even_with_valid_key(shell_env, monkeypatch, tmp_path):
    """shell-settings 与 hub/boxes 同一纪律：只许本机，有效钥匙也不行（404 假装不存在）。"""
    from src.pairing import store as pairing_store
    from src.pairing import service as pairing_service

    monkeypatch.setattr(pairing_store, "PAIRING_DIR", tmp_path / "pairing")
    # is_local=True 描述签发语境（测试即本机在签）；签出来的是 member 钥匙
    key = pairing_service.issue_token(name="phone", is_local=True)

    from src.remote_shell.app import create_app

    client = TestClient(
        create_app(),
        base_url="http://192.168.1.30:18911",
        client=("192.168.1.9", 50123),  # 局域网客户端地址 → 非本机
    )
    headers = {"X-Pairing-Token": key["token"]}
    assert client.get("/api/shell-settings", headers=headers).status_code == 404
    assert (
        client.patch(
            "/api/shell-settings",
            headers=headers,
            json={"remote_mode": False},
        ).status_code
        == 404
    )
