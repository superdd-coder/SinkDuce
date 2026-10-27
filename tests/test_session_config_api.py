"""会话配置的 API 契约：随消息保存、随会话返回。

设计：`development_doc/2026-09-15-session-config-design.md`
- 发送消息时把当前配置一并存进该会话（一次请求，不额外发）；
- GET /sessions/{id} 返回 config，供前端恢复。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.db.sessions import SessionStore


@pytest.fixture
def store(tmp_path):
    s = SessionStore(str(tmp_path / "api_sessions.db"))
    yield s
    conn = getattr(s._local, "conn", None)
    if conn:
        conn.close()


@pytest.fixture
def client(store):
    """回环客户端 + 回环 base_url：门卫要求 Host 也是回环（防 DNS rebinding），
    TestClient 默认的 http://testserver 会被判为非本机而 401。"""
    from src.main import app

    return TestClient(app, base_url="http://127.0.0.1:18900", client=("127.0.0.1", 50000))


class TestSessionResponseCarriesConfig:
    def test_create_then_get_returns_config_field(self, client, store):
        with patch("src.api.routes.sessions.services") as svc:
            svc.session_store = store
            sid = client.post("/api/sessions", json={}).json()["id"]
            body = client.get(f"/api/sessions/{sid}").json()
        assert "config" in body, "会话详情必须带上 config，前端才能恢复"
        assert body["config"] == {}, "新会话无配置"

    def test_list_does_not_need_config(self, client, store):
        """列表页不需要 config（省载荷）——只要不报错即可。"""
        with patch("src.api.routes.sessions.services") as svc:
            svc.session_store = store
            client.post("/api/sessions", json={})
            rows = client.get("/api/sessions").json()
        assert isinstance(rows, list) and len(rows) == 1

    def test_stored_config_is_returned(self, client, store):
        with patch("src.api.routes.sessions.services") as svc:
            svc.session_store = store
            sid = client.post("/api/sessions", json={}).json()["id"]
            cfg = {"provider_id": "p1", "model": "m1", "web_search": True, "thinking": False,
                   "collections": ["col_a"]}
            store.update_session(sid, config=cfg)
            body = client.get(f"/api/sessions/{sid}").json()
        assert body["config"] == cfg


class TestSendMessagePersistsConfig:
    def _send(self, client, store, sid, payload):
        """发送消息会返回 SSE 流；这里只关心「路由是否把配置存下来了」。"""
        with patch("src.api.routes.sessions.services") as svc, \
             patch("src.chatbox.stream_handler.ChatStreamHandler"):
            svc.session_store = store
            svc.chatbox_agent = object()
            return client.post(f"/api/sessions/{sid}/messages", json=payload)

    def test_send_stores_config(self, client, store):
        with patch("src.api.routes.sessions.services") as svc:
            svc.session_store = store
            sid = client.post("/api/sessions", json={}).json()["id"]

        self._send(client, store, sid, {
            "content": "hi",
            "thinking": False,
            "provider_id": "p1",
            "model": "m1",
            "web_search_enabled": True,
            "collections": ["col_a"],
        })

        cfg = store.get_session(sid).config
        assert cfg.get("provider_id") == "p1"
        assert cfg.get("model") == "m1"
        assert cfg.get("thinking") is False
        # 请求体字段名是 web_search_enabled；存进会话时统一成 web_search
        assert cfg.get("web_search") is True
        assert cfg.get("collections") == ["col_a"]

    def test_send_without_config_fields_does_not_break(self, client, store):
        with patch("src.api.routes.sessions.services") as svc:
            svc.session_store = store
            sid = client.post("/api/sessions", json={}).json()["id"]

        self._send(client, store, sid, {"content": "hi"})
        # 缺省值也应落盘（thinking 默认 True、web_search 默认 False）
        cfg = store.get_session(sid).config
        assert cfg.get("thinking") is True
        assert cfg.get("web_search") is False

    def test_config_is_wholesale_replaced_per_send(self, client, store):
        with patch("src.api.routes.sessions.services") as svc:
            svc.session_store = store
            sid = client.post("/api/sessions", json={}).json()["id"]

        self._send(client, store, sid, {"content": "a", "model": "m1", "provider_id": "p1"})
        self._send(client, store, sid, {"content": "b", "model": "m2", "provider_id": "p2"})
        cfg = store.get_session(sid).config
        assert cfg.get("model") == "m2"
        assert cfg.get("provider_id") == "p2"

    def test_unknown_session_404(self, client, store):
        with patch("src.api.routes.sessions.services") as svc:
            svc.session_store = store
            svc.chatbox_agent = object()
            resp = client.post("/api/sessions/nope/messages", json={"content": "hi"})
        assert resp.status_code == 404
