"""Hub 出站钥匙托管（第 3 步）：存储、镜像替换、端点隔离。

**这个模块的全部要害是「谁能读写它」**——`data/hub/boxes.json` 装的是这台盒子
去访问**别人**的钥匙明文（凭据），不是关于自己的信息。所以本文件的重点是隔离，
不是 CRUD 的快乐路径。

跑法：``pytest tests/test_hub_key_custody.py``
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def _tmp_hub(tmp_path, monkeypatch):
    """把存储与配对判定都指到 tmp，避免碰真实 data/。"""
    from src.hub import store as hub_store
    from src.pairing import service as pairing_service, store as pairing_store

    monkeypatch.setattr(hub_store, "HUB_DIR", tmp_path / "hub")
    monkeypatch.setattr(pairing_store, "PAIRING_DIR", tmp_path / "pairing")
    monkeypatch.setattr(pairing_service, "local_entry_port", lambda: 18900)
    return tmp_path


# ── 存储层 ───────────────────────────────────────────────────


class TestStore:
    def test_replace_writes_all_fields(self):
        from src.hub import store

        out = store.replace_boxes(
            [
                {
                    "box_id": "b1",
                    "box_name": "Office",
                    "addr": "http://192.168.1.9:18901",
                    "token": "sd1_secret",
                    "last_checked": "2026-09-18T10:00:00Z",
                }
            ]
        )
        assert len(out) == 1
        # 真落盘了（不是只在内存里）
        on_disk = json.loads(store._boxes_path().read_text())["boxes"]
        assert on_disk[0]["token"] == "sd1_secret"
        assert on_disk[0]["addr"] == "http://192.168.1.9:18901"

    def test_addr_trailing_slash_normalized(self):
        """`http://h:1/` 与 `http://h:1` 是同一台 —— 归一化避免拼出 `//api`。"""
        from src.hub import store

        out = store.replace_boxes([{"box_id": "b1", "addr": "http://h:1///", "token": "t"}])
        assert out[0]["addr"] == "http://h:1"

    def test_replace_is_whole_list_delete_is_covered(self):
        """**核心**：删除靠「整体替换」天然覆盖，不需要额外接口。"""
        from src.hub import store

        store.replace_boxes([{"box_id": "b1", "addr": "http://h:1", "token": "t"}])
        store.replace_boxes([{"box_id": "b2", "addr": "http://h:2", "token": "t"}])
        ids = [b["box_id"] for b in store.list_boxes()]
        assert ids == ["b2"], "旧的 b1 必须消失"

    def test_records_without_id_or_addr_dropped(self):
        """没有地址的记录无法作为调用目标；没有 id 无法被选中。宁缺勿留。"""
        from src.hub import store

        out = store.replace_boxes(
            [
                {"box_id": "", "addr": "http://h:1", "token": "t"},
                {"box_id": "b1", "addr": "", "token": "t"},
                {"box_id": "b2", "addr": "http://h:2", "token": "t"},
            ]
        )
        assert [b["box_id"] for b in out] == ["b2"]

    def test_duplicate_box_id_keeps_first(self):
        from src.hub import store

        out = store.replace_boxes(
            [
                {"box_id": "b1", "addr": "http://first", "token": "t1"},
                {"box_id": "b1", "addr": "http://second", "token": "t2"},
            ]
        )
        assert len(out) == 1 and out[0]["addr"] == "http://first"

    def test_probe_state_survives_resync(self):
        """重同步不该抹掉后端自己的观察结果（否则在线指示会闪）。"""
        from src.hub import store

        store.replace_boxes([{"box_id": "b1", "addr": "http://h:1", "token": "t"}])
        # 模拟探测写过状态
        boxes = store.list_boxes()
        boxes[0]["online"] = True
        boxes[0]["role"] = "member"
        store.save(boxes)

        store.replace_boxes(
            [{"box_id": "b1", "box_name": "renamed", "addr": "http://h:1", "token": "t"}]
        )
        got = store.get_box("b1")
        assert got["online"] is True, "探测状态被重同步抹掉了"
        assert got["role"] == "member"
        assert got["box_name"] == "renamed", "浏览器同步的字段仍应更新"

    def test_token_preserved_when_incoming_empty(self):
        """同步里没带钥匙时**不得**抹掉已存的——那是静默吊销（见 store.replace_boxes）：
        用户在 UI 上仍看到「已连接」，后端却已经失去访问能力，无从发现。"""
        from src.hub import store

        store.replace_boxes([{"box_id": "b1", "addr": "http://h:1", "token": "keepme"}])
        store.replace_boxes([{"box_id": "b1", "addr": "http://h:1", "token": ""}])
        assert store.get_box("b1")["token"] == "keepme"

    def test_removal_is_by_absence_not_by_empty_token(self):
        """真要移除一整台盒子走「不在列表里」，而不是传空钥匙——两条语义不能混。"""
        from src.hub import store

        store.replace_boxes([{"box_id": "b1", "addr": "http://h:1", "token": "t"}])
        store.replace_boxes([])
        assert store.list_boxes() == []

    def test_corrupt_file_treated_as_empty_not_crash(self):
        """文件坏了按「没有盒子」处理：Hub 是附加能力，不该拖垮 MCP 面。"""
        from src.hub import store

        store._boxes_path().parent.mkdir(parents=True, exist_ok=True)
        store._boxes_path().write_text("{ not json")
        assert store.list_boxes() == []

    def test_get_box_by_name_exact_then_casefold(self):
        from src.hub import store

        store.replace_boxes([{"box_id": "b1", "box_name": "Office Box", "addr": "http://h:1", "token": "t"}])
        assert store.get_box_by_name("Office Box")["box_id"] == "b1"
        assert store.get_box_by_name("office box")["box_id"] == "b1"
        assert store.get_box_by_name("nope") is None
        assert store.get_box_by_name("") is None

    def test_chinese_box_name_matches(self):
        from src.hub import store

        store.replace_boxes([{"box_id": "b1", "box_name": "办公室", "addr": "http://h:1", "token": "t"}])
        assert store.get_box_by_name("办公室")["box_id"] == "b1"


# ── ★ 隔离：端点只许本机 ─────────────────────────────────────


class TestEndpointIsLocalOnly:
    """`boxes.json` 是凭据库。远端哪怕持有效钥匙也必须读不到、写不进。"""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from src.main import app

        return TestClient(app, base_url="http://192.168.1.30:18901")

    def test_lan_without_key_blocked_by_middleware(self, client):
        """无钥匙的远端请求被**门卫**拦在路由之前（401）——那是所有 /api 的统一行为，
        所以它不泄露「这个路径存在」：任何 /api 路径在这里都是同一个 401。"""
        resp = client.get("/api/hub/boxes")
        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "pairing_required"

    def test_lan_with_valid_key_cannot_read(self, client):
        """**核心回归**：有效 member 钥匙也不行——那把钥匙表达的是「能访问我的知识库」，
        不是「能管理我出站到谁」。穿过门卫后由路由的本地判定拒绝，且用 404 不暴露存在。"""
        from src.pairing import service

        key = service.ensure_local_key()
        resp = client.get("/api/hub/boxes", headers={"X-Pairing-Token": key["token"]})
        assert resp.status_code == 404

    def test_lan_with_valid_key_cannot_write(self, client):
        """**核心回归**：能写 = 能往里塞一台恶意盒子 / 把地址改指向自己。"""
        from src.pairing import service

        key = service.ensure_local_key()
        resp = client.put(
            "/api/hub/boxes",
            headers={"X-Pairing-Token": key["token"], "Content-Type": "application/json"},
            json={"boxes": [{"box_id": "evil", "addr": "http://attacker:1", "token": "x"}]},
        )
        assert resp.status_code == 404
        # 而且**真的没写进去**
        from src.hub import store

        assert store.list_boxes() == []

    def test_loopback_can_read_and_write(self):
        """对照：本机照常可用（否则 Hub 功能整个不能工作）。"""
        from fastapi.testclient import TestClient

        from src.main import app

        client = TestClient(
            app, base_url="http://127.0.0.1:18900", client=("127.0.0.1", 51000)
        )
        put = client.put(
            "/api/hub/boxes",
            headers={"Content-Type": "application/json"},
            json={
                "boxes": [
                    {"box_id": "b1", "box_name": "Office", "addr": "http://h:1", "token": "sd1_x"}
                ]
            },
        )
        assert put.status_code == 200, put.text
        got = client.get("/api/hub/boxes")
        assert got.status_code == 200
        assert [b["box_id"] for b in got.json()["boxes"]] == ["b1"]


# ── 读接口不回传钥匙明文 ─────────────────────────────────────


class TestNoTokenInResponse:
    def test_list_returns_has_token_but_not_token(self):
        from fastapi.testclient import TestClient

        from src.main import app

        client = TestClient(
            app, base_url="http://127.0.0.1:18900", client=("127.0.0.1", 51000)
        )
        client.put(
            "/api/hub/boxes",
            headers={"Content-Type": "application/json"},
            json={"boxes": [{"box_id": "b1", "addr": "http://h:1", "token": "sd1_super_secret"}]},
        )
        body = client.get("/api/hub/boxes").text
        assert "sd1_super_secret" not in body, "钥匙明文不该回传给浏览器"
        box = client.get("/api/hub/boxes").json()["boxes"][0]
        assert box["has_token"] is True

    def test_put_response_has_no_token(self):
        from fastapi.testclient import TestClient

        from src.main import app

        client = TestClient(
            app, base_url="http://127.0.0.1:18900", client=("127.0.0.1", 51000)
        )
        resp = client.put(
            "/api/hub/boxes",
            headers={"Content-Type": "application/json"},
            json={"boxes": [{"box_id": "b1", "addr": "http://h:1", "token": "sd1_super_secret"}]},
        )
        assert "sd1_super_secret" not in resp.text
