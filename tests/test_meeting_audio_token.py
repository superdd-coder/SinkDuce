"""回归：/api/meetings/{id}/audio 的 ?token= 必须能被配对钥匙使用。

背景（2026-09-15 实测）：门卫用 `?token=` 给无法带请求头的通道（`<audio>`、
`<img>`、EventSource、下载链接）传配对钥匙。但 /audio 端点**自己**也有一个叫
`token` 的查询参数（本意是给外部服务如 DashScope 的短时音频令牌），于是配对钥匙
被当成音频令牌去验 → 401 → 连远端盒子时会议音频静默播不出来。

修法：把那个内部令牌参数改名为 `audio_token`，两者不再撞名。
（全仓没有任何地方生成音频令牌，该机制当前无生产者；门卫已负责鉴权。）
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.pairing import service, store


@pytest.fixture(autouse=True)
def _tmp_pairing(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "PAIRING_DIR", tmp_path)
    monkeypatch.setattr(service, "local_entry_port", lambda: 18900)
    yield


@pytest.fixture
def client():
    from src.main import app

    return TestClient(app, base_url="http://192.168.1.30:18901")


class TestAudioQueryTokenIsPairingKey:
    def test_pairing_key_in_token_param_reaches_route(self, client):
        """?token=<配对钥匙> 应通过门卫与路由，而不是被音频令牌校验拒掉。

        会议/音频文件可能不存在（404/400 都算通过），关键是不能是
        401 "Invalid or expired token"（那是被错当成音频令牌的迹象）。
        """
        key = service.issue_token(name="phone", is_local=True)
        resp = client.get(f"/api/meetings/nope/audio?token={key['token']}")
        assert resp.status_code != 401, (
            f"配对钥匙被拒：{resp.status_code} {resp.text[:200]}"
        )

    def test_no_key_is_rejected_by_gate(self, client):
        resp = client.get("/api/meetings/nope/audio")
        assert resp.status_code == 401

    def test_audio_token_param_still_validated(self, client):
        """改名后的音频令牌参数仍在（非法值应 401），能力未被删除。"""
        key = service.issue_token(name="phone", is_local=True)
        resp = client.get(
            f"/api/meetings/nope/audio?token={key['token']}&audio_token=bogus"
        )
        assert resp.status_code == 401
        assert "Invalid or expired token" in resp.text
