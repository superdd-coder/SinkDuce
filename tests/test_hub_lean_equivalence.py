"""批次 3 等价性测试：**重载荷工具的 lean 转发**。

三个工具（`list_meetings` / `get_meeting` / `list_files`）的 REST 载荷与工具契约
差一个数量级（会议列表 40+ 字段、详情内联整段转录、文件列表缺 mounts/聚合），
批次 3 的解法是把**工具形状做成 REST 的 ``lean=true`` 模式**（用户拍板 A 案(a)：
契约放 REST 侧才能被测试钉住），MCP 本地实现与 REST lean **共用同一份投影**。

三个层次：
1. ``TestLeanHelpers``：投影函数纯逻辑（字段白名单 / tabs 顺序 / scope 校验）。
2. ``TestRestLeanMode``：真机 REST——lean 形状对、**非 lean 路径行为不变**。
3. ``TestLiveEquivalence``：真机 MCP——本地 vs 转发逐字段一致，且**转录全文
   绝不出现在转发结果里**（这是本批存在的全部理由）。

跑法：``pytest tests/test_hub_lean_equivalence.py``
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

import pytest

BASE = "http://127.0.0.1:18900"


# ── 1. 投影函数（离线） ───────────────────────────────────────


class _FakeMeeting:
    """覆盖 meeting_meta_dict 全部分支的最小会议对象。"""

    id = "m1"
    title = "T"
    status = type("S", (), {"value": "completed"})()
    mode = type("M", (), {"value": "upload"})()
    processing_state = "idle"
    summary_gen_state = "done"
    blueprint_gen_state = "done"
    speaker_names = {"S1": "Alice"}
    hot_words_library_id = None
    hot_words_library_ids = []
    tabs = [{"tab_id": "tab_a", "name": "A", "description": "d"}]
    allocated_collections = []
    allocated_file_ids = []
    transcript_path = "x.json"
    summary = "S"
    created_at = type("D", (), {"isoformat": lambda self: "2026-09-18T10:00:00"})()
    updated_at = created_at

    def model_dump(self):
        return {
            "id": self.id, "title": self.title, "status": "completed",
            "mode": "upload", "processing_state": self.processing_state,
            "summary_gen_state": self.summary_gen_state,
            "blueprint_gen_state": self.blueprint_gen_state,
            "speaker_names": self.speaker_names,
            "hot_words_library_id": self.hot_words_library_id,
            "hot_words_library_ids": self.hot_words_library_ids,
            "allocated_collections": [], "allocated_file_ids": [],
            "created_at": "ignored", "updated_at": "ignored",
            # 重字段：白名单必须把它们挡掉
            "permissions": {"can_delete": True},
            "speaker_matches": [1.0],
            "transcript_index_built": True,
        }


class TestLeanHelpers:
    def test_light_dict_whitelists_fields(self):
        from src.meeting.lean import meeting_light_dict

        d = meeting_light_dict(_FakeMeeting())
        assert "permissions" not in d and "speaker_matches" not in d
        assert d["id"] == "m1" and d["created_at"] == "2026-09-18T10:00:00"

    def test_meta_dict_general_tab_first(self, monkeypatch):
        from src.meeting import lean

        class _Store:
            @staticmethod
            def get_sentences(mid):
                return None

            @staticmethod
            def get_notes(mid):
                return "note body"

        monkeypatch.setattr("src.meeting.store", _Store, raising=False)
        # lean.py 里是延迟 import —— 直接打补丁到模块级 store 引用点
        import types

        fake_store_mod = types.SimpleNamespace(get_sentences=_Store.get_sentences,
                                               get_notes=_Store.get_notes)
        import sys

        monkeypatch.setitem(sys.modules, "src.meeting.store", fake_store_mod)
        d = lean.meeting_meta_dict(_FakeMeeting())
        assert d["tabs"][0]["tab_id"] == "general"
        assert d["tabs"][1] == {"tab_id": "tab_a", "name": "A", "description": "d"}
        assert d["has_transcript"] is True  # transcript_path 非空
        assert d["has_summary"] is True
        assert d["has_notes"] is True
        assert d["notes_content"] == "note body"
        assert "transcript" not in d, "元数据形态绝不能带 transcript"

    def test_files_lean_rejects_bad_scope(self, tmp_path, monkeypatch):
        from src.file_mgmt import files as files_mod
        from fastapi import HTTPException

        called = {}

        def _no_call(**kwargs):
            called["n"] = 1
            return []

        monkeypatch.setattr(files_mod, "list_files_with_mounts", _no_call)
        with pytest.raises(HTTPException) as ei:
            files_mod.list_files_lean("col_x", scope="orphans")
        assert ei.value.status_code == 400
        assert "scope must be 'all' or 'root'" in str(ei.value.detail)
        assert not called, "scope 非法不该碰数据库"

    def test_files_lean_envelope_shape(self, monkeypatch):
        from src.file_mgmt import files as files_mod

        monkeypatch.setattr(
            files_mod, "list_files_with_mounts",
            lambda *a, **kw: [
                {"file_id": "f1", "doc_kind": "file"},
                {"file_id": "f2", "doc_kind": "note"},
            ],
        )
        monkeypatch.setattr(files_mod, "unassigned_only_file_ids",
                            lambda c, ids: {"f2"})  # f2 只挂在未分配 → 隐身
        out = files_mod.list_files_lean("col_x", scope="all")
        assert [f["file_id"] for f in out["files"]] == ["f1"], "未分配文件必须被滤掉"
        assert out["total"] == 1
        assert out["summary"]["by_doc_kind"] == {"file": 1}
        assert out["scope"] == "all"


# ── 真机层公共 ────────────────────────────────────────────────


def _has_service() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


pytestmark_live = pytest.mark.skipif(
    not _has_service(), reason="需要 18900 在跑（lean 真机测试）"
)


def _rest(path: str):
    try:
        with urllib.request.urlopen(BASE + path, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        return {"_status": e.code}


class _Mcp:
    def __init__(self):
        import httpx

        self._c = httpx.Client(timeout=90)
        r = self._c.post(f"{BASE}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                   "clientInfo": {"name": "lean-equiv", "version": "0"}}},
                        headers={"Accept": "application/json, text/event-stream"})
        self.sid = r.headers.get("mcp-session-id", "")
        self._c.post(f"{BASE}/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                     headers=self._h())

    def _h(self):
        return {"Accept": "application/json, text/event-stream", "mcp-session-id": self.sid}

    def call(self, name: str, args: dict):
        r = self._c.post(f"{BASE}/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": name, "arguments": args}}, headers=self._h())
        res = {}
        for line in r.text.splitlines():
            if line.startswith("data: "):
                d = json.loads(line[6:])
                if "result" in d:
                    res = d["result"]
        sc = res.get("structuredContent")
        if isinstance(sc, dict) and isinstance(sc.get("result"), str):
            return json.loads(sc["result"])
        return sc or {}


@pytest.fixture
def self_hub():
    original = (_rest("/api/hub/boxes") or {}).get("boxes") or []

    def _put(boxes):
        req = urllib.request.Request(
            f"{BASE}/api/hub/boxes", data=json.dumps({"boxes": boxes}).encode(),
            method="PUT", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()

    _put([
        *[{"box_id": b["box_id"], "box_name": b["box_name"], "addr": b["addr"],
           "last_checked": b.get("last_checked", "")} for b in original],
        {"box_id": "__lean_equiv__", "box_name": "LeanSelf", "addr": BASE, "token": ""},
    ])
    yield
    _put([{"box_id": b["box_id"], "box_name": b["box_name"], "addr": b["addr"],
           "last_checked": b.get("last_checked", "")} for b in original])


@pytestmark_live
class TestRestLeanMode:
    def test_meetings_lean_envelope_and_light_items(self):
        out = _rest("/api/meetings?lean=true&limit=5")
        assert isinstance(out, dict) and {"meetings", "total", "returned"} <= set(out)
        for item in out["meetings"]:
            assert "permissions" not in item
            assert "tabs" not in item
            assert "transcript_index_built" not in item

    def test_meetings_non_lean_unchanged(self):
        out = _rest("/api/meetings")
        assert isinstance(out, list), "非 lean 必须还是裸列表（网页契约不变）"
        # permissions 由 _attach_meeting_permissions 按 actor 附加；无身份的
        # 部署下可能缺席——只断言「仍是老形状」（裸列表、每项是 dict）。
        if out:
            assert isinstance(out[0], dict)

    def test_meeting_detail_lean_has_no_transcript(self):
        meets = _rest("/api/meetings?lean=true&limit=20")["meetings"]
        if not meets:
            pytest.skip("盒子上没有会议")
        mid = meets[0]["id"]
        lean = _rest(f"/api/meetings/{mid}?lean=true")
        assert lean.get("id") == mid
        assert "transcript" not in lean, "lean 详情绝不能内联转录"
        assert "has_transcript" in lean
        full = _rest(f"/api/meetings/{mid}")
        # 非 lean 还是老行为（可能有 transcript 键——有的会议才有）
        assert "tabs" in full or "has_notes" in full or "notes_content" in full

    def test_files_lean_and_scope_validation(self):
        cols = _rest("/api/collections")
        col = next((c["id"] for c in cols), None)
        if not col:
            pytest.skip()
        out = _rest(f"/api/file-mgmt/{col}/files?lean=true")
        assert {"files", "total", "summary", "collection", "scope"} <= set(out)
        bad = _rest(f"/api/file-mgmt/{col}/files?lean=true&scope=bogus")
        assert bad.get("_status") == 400


@pytestmark_live
class TestLiveEquivalence:
    def test_list_meetings_local_vs_forwarded(self, self_hub):
        s = _Mcp()
        s.call("switch_box", {"name": "local"})
        local = s.call("list_meetings", {"limit": 5})
        s.call("switch_box", {"name": "LeanSelf"})
        fwd = s.call("list_meetings", {"limit": 5})
        s.call("switch_box", {"name": "local"})
        assert local == fwd, (local, fwd)

    def test_list_meetings_search_filter_equivalence(self, self_hub):
        s = _Mcp()
        s.call("switch_box", {"name": "local"})
        local = s.call("list_meetings", {"search": "__no_match_zz__"})
        s.call("switch_box", {"name": "LeanSelf"})
        fwd = s.call("list_meetings", {"search": "__no_match_zz__"})
        s.call("switch_box", {"name": "local"})
        assert local.get("total") == fwd.get("total") == 0

    def test_get_meeting_local_vs_forwarded_no_transcript_leak(self, self_hub):
        s = _Mcp()
        meets = s.call("list_meetings", {"limit": 20})
        if not meets.get("meetings"):
            pytest.skip("盒子上没有会议")
        mid = meets["meetings"][0]["id"]
        s.call("switch_box", {"name": "local"})
        local = s.call("get_meeting", {"meeting_id": mid})
        s.call("switch_box", {"name": "LeanSelf"})
        fwd = s.call("get_meeting", {"meeting_id": mid})
        s.call("switch_box", {"name": "local"})
        assert local == fwd, (local, fwd)
        assert "transcript" not in fwd, "转发结果不能带转录全文（lean 的全部意义）"
        # 体积护栏：与**本地同量级**（绝对上限会被合法的大 notes 误伤——
        # notes_content 是契约字段且无界；审查 #7b）。
        lb, fb = len(json.dumps(local, ensure_ascii=False)), len(json.dumps(fwd, ensure_ascii=False))
        assert fb <= lb + 2_000, f"转发 {fb} vs 本地 {lb} 字符——疑似带回了多余载荷"

    def test_get_meeting_missing_is_error(self, self_hub):
        """404 的形状与本地不同文案但同语义（Hub 错误信封带 box 前缀 + code）——
        审查 #2：把这个**现状**钉住，别让它无声漂移；文案里必须保留 "not found"
        （REST 的 detail 原文嵌在里面）。"""
        s = _Mcp()
        s.call("switch_box", {"name": "LeanSelf"})
        out = s.call("get_meeting", {"meeting_id": "no_such_meeting"})
        s.call("switch_box", {"name": "local"})
        assert out.get("error") and "not found" in out["error"].lower(), out
        assert out.get("code") == "hub_call_failed", out

    def test_list_meetings_filters_and_defaults_parity(self, self_hub):
        """逐项过滤参数 + 全默认参数的本地/转发 parity（审查 #5：query lambda
        里重复的字面量默认值是唯一事实源——这条测试把它钉进行为）。"""
        s = _Mcp()
        cases = [
            {},  # 全默认（limit=100 / include_archived=False）
            {"include_archived": True},
            {"include_archived": False},  # 显式 false ≡ 省略
            {"status": "completed"},
            {"status": "bogus_status"},   # 两侧都该是 total=0
            {"limit": 2},
            {"search": "e"},
        ]
        for args in cases:
            s.call("switch_box", {"name": "local"})
            local = s.call("list_meetings", args)
            s.call("switch_box", {"name": "LeanSelf"})
            fwd = s.call("list_meetings", args)
            s.call("switch_box", {"name": "local"})
            assert local == fwd, (args, local, fwd)

    def test_list_files_filters_parity(self, self_hub):
        s = _Mcp()
        cols = _rest("/api/collections")
        col = next((c["id"] for c in cols), None)
        if not col:
            pytest.skip()
        folders = s.call("list_folders", {"collection": col})
        fid = (folders.get("folders") or [{}])[0].get("folder_id")
        cases = [
            {"collection": col},
            {"collection": col, "scope": "root"},
            {"collection": col, "archived": "false"},
            {"collection": col, "is_definitive": "false"},
        ]
        if fid:
            cases.append({"collection": col, "folder_id": fid})
        for args in cases:
            s.call("switch_box", {"name": "local"})
            local = s.call("list_files", args)
            s.call("switch_box", {"name": "LeanSelf"})
            fwd = s.call("list_files", args)
            s.call("switch_box", {"name": "local"})
            assert local == fwd, (args, json.dumps(local)[:150], json.dumps(fwd)[:150])

    def test_list_files_local_vs_forwarded(self, self_hub):
        s = _Mcp()
        cols = _rest("/api/collections")
        col = next((c["id"] for c in cols), None)
        if not col:
            pytest.skip()
        s.call("switch_box", {"name": "local"})
        local = s.call("list_files", {"collection": col})
        s.call("switch_box", {"name": "LeanSelf"})
        fwd = s.call("list_files", {"collection": col})
        s.call("switch_box", {"name": "local"})
        assert local == fwd, (json.dumps(local)[:200], json.dumps(fwd)[:200])

    def test_list_files_bad_scope_same_error(self, self_hub):
        s = _Mcp()
        cols = _rest("/api/collections")
        col = next((c["id"] for c in cols), None)
        if not col:
            pytest.skip()
        s.call("switch_box", {"name": "LeanSelf"})
        out = s.call("list_files", {"collection": col, "scope": "bogus"})
        s.call("switch_box", {"name": "local"})
        assert "scope must be" in (out.get("error") or ""), out

    def test_list_files_fmless_collection_refuses_like_local(self, self_hub):
        """与批次 2 的 todo 同一道闸：本地 MCP 建的库没有 fm DB——
        本地拒绝；转发必须同样拒绝且**不得触发远端建库**。"""
        s = _Mcp()
        created = s.call("create_collection", {"name": "__verify_lean_fmless__"})
        assert not created.get("error"), created
        col = created["id"]
        try:
            s.call("switch_box", {"name": "local"})
            local = s.call("list_files", {"collection": col})
            s.call("switch_box", {"name": "LeanSelf"})
            fwd = s.call("list_files", {"collection": col})
            s.call("switch_box", {"name": "local"})
            assert local.get("error") and fwd.get("error"), (local, fwd)
            assert "File-management DB not available" in fwd["error"], fwd
            after = _rest(f"/api/file-mgmt/{col}/db-status")
            assert after.get("initialized") is False, "拒绝路径竟触发了远端建库"
        finally:
            s.call("delete_collection", {"collection": col})
