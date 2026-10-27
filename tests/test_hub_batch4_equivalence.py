"""批次 4 等价性测试：**新增端点的 10 个工具**（tree/timeline/chain/versions/
definitive×2/document_text/search×2/propagation）。

方法与批次 2/3 相同：Hub 指向自己，本地 vs 转发逐字段比对。重创建的用例
（上传文件做版本/正文靶子）走**错误路径等价**（同一个坏 id 两侧必须同错），
轻创建的（笔记/传播）真做并用完即清。

跑法：``pytest tests/test_hub_batch4_equivalence.py``
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

BASE = "http://127.0.0.1:18900"


def _has_service() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _has_service(), reason="需要 18900 在跑（批次 4 真机等价性）"
)


def _rest(path: str, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode("utf-8", "ignore")
            return json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as e:
        try:
            return {"_status": e.code, **json.loads(e.read().decode("utf-8", "ignore"))}
        except Exception:
            return {"_status": e.code}
    except Exception:
        return None


class _Mcp:
    def __init__(self):
        import httpx

        self._c = httpx.Client(timeout=180)
        r = self._c.post(f"{BASE}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                   "clientInfo": {"name": "b4-equiv", "version": "0"}}},
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
            try:
                return json.loads(sc["result"])
            except Exception:
                return {"_text": sc["result"]}
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
        {"box_id": "__b4_equiv__", "box_name": "B4Self", "addr": BASE, "token": ""},
    ])
    yield
    _put([{"box_id": b["box_id"], "box_name": b["box_name"], "addr": b["addr"],
           "last_checked": b.get("last_checked", "")} for b in original])


@pytest.fixture
def fm_col():
    """带 fm DB 的临时库（REST 建库自动 init）。"""
    out = _rest("/api/collections", "POST", {"name": "__verify_b4_col__"})
    assert out and out.get("id"), out
    yield out["id"]
    _rest(f"/api/collections/{out['id']}", "DELETE")


def _pair(s, name, args):
    s.call("switch_box", {"name": "local"})
    local = s.call(name, args)
    sw = s.call("switch_box", {"name": "B4Self"})
    assert sw.get("reachable"), sw
    forwarded = s.call(name, args)
    s.call("switch_box", {"name": "local"})
    return local, forwarded


class TestChunkShapeContract:
    """共享 helper 的副作用：本地与转发**一起**漂移时，parity 测试看不见。
    这条把 chunk dict 的字段集（Agent 后续调 get_document_text 的依据）钉死。"""

    def test_chunk_to_dict_fields(self):
        from types import SimpleNamespace

        from src.rag.search_views import _chunk_to_dict

        chunk = SimpleNamespace(
            text="hello", score=0.9,
            metadata={"source": "__file__:f1", "collection": "col_1",
                      "chunk_type": "normal", "id": "c1", "images": []},
        )
        d = _chunk_to_dict(chunk)
        assert {"text", "score", "source", "file_id", "filename",
                "collection", "chunk_type", "context", "id", "images"} <= set(d), sorted(d)
        assert d["file_id"] == "f1"
        assert d["source"] == "__file__:f1"


class TestBatch4Equivalence:
    def test_library_tree_and_timeline(self, self_hub, fm_col):
        s = _Mcp()
        for name, args in (
            ("list_library_tree", {"collection": fm_col}),
            ("list_library_tree", {"collection": fm_col, "max_depth": 1, "fields": "minimal"}),
            ("get_timeline", {"collection": fm_col}),
        ):
            local, fwd = _pair(s, name, args)
            assert local == fwd, (name, local, fwd)
            assert not local.get("error") or local.get("error") == fwd.get("error")

    def test_chain_missing_is_same_error(self, self_hub, fm_col):
        s = _Mcp()
        local, fwd = _pair(s, "get_chain", {"collection": fm_col, "chain_id": "no_such"})
        assert local.get("error") and fwd.get("error"), (local, fwd)

    def test_versions_and_text_missing_ids(self, self_hub, fm_col):
        """坏 file_id：版本/正文两侧必须同错（不会静默成功）。"""
        s = _Mcp()
        for name, args in (
            ("list_file_versions", {"collection": fm_col, "file_id": "ghost"}),
            ("get_document_text", {"collection": fm_col, "file_id": "ghost"}),
        ):
            local, fwd = _pair(s, name, args)
            assert local.get("error") and fwd.get("error"), (name, local, fwd)
            # 错误形态的关键字段必须保留（extract_status 是 Agent 的决策依据）
            if "extract_status" in local:
                assert local.get("extract_status") == fwd.get("extract_status"), (name, local, fwd)

    def test_set_file_definitive_missing_id(self, self_hub, fm_col):
        s = _Mcp()
        local, fwd = _pair(
            s, "set_file_definitive",
            {"collection": fm_col, "file_id": "ghost", "is_definitive": True})
        assert local.get("error") and fwd.get("error"), (local, fwd)

    def test_set_document_definitive_idempotent_semantics(self, self_hub, fm_col):
        """坏 source（__file__:ghost）→ 两侧同错；这是写工具的静默创建防线。"""
        s = _Mcp()
        local, fwd = _pair(
            s, "set_document_definitive",
            {"collection": fm_col, "source": "__file__:ghost", "definitive": True})
        assert local.get("error") and fwd.get("error"), (local, fwd)

    def test_direct_search_bad_collection(self, self_hub):
        s = _Mcp()
        local, fwd = _pair(
            s, "search_direct_chunks",
            {"query": "anything", "collection": "col_does_not_exist"})
        assert local.get("error") and fwd.get("error"), (local, fwd)
        assert "does not exist" in local["error"]
        assert "does not exist" in fwd["error"], fwd

    def test_direct_search_real_collection(self, self_hub):
        """真检索一条（小 top_k）：两侧结果必须一致（同一台盒子、同一索引）。"""
        cols = _rest("/api/collections")
        # 挑非归档库（cols[0] 可能是归档的——归档闸门会正确拦截，但那测的是闸门不是检索）
        col = next((c["id"] for c in cols if not c.get("archived")), None)
        if not col:
            pytest.skip()
        s = _Mcp()
        local, fwd = _pair(
            s, "search_direct_chunks",
            {"query": "project", "collection": col, "top_k": 3})
        assert local.get("query_used") == fwd.get("query_used") == "project"
        assert local.get("total") == fwd.get("total"), (local.get("total"), fwd.get("total"))
        assert len(fwd.get("results") or []) == len(local.get("results") or [])
        # 逐字段形状：首条 chunk 的键集两侧必须一致（filename/file_id 是
        # Agent 后续调 get_document_text 的依据，丢了就是静默降级）
        if local.get("results"):
            assert set(local["results"][0]) == set(fwd["results"][0]), (
                sorted(local["results"][0]), sorted(fwd["results"][0]))

    def test_search_agentic_chunks(self, self_hub):
        cols = _rest("/api/collections")
        if not cols:
            pytest.skip()
        s = _Mcp()
        local, fwd = _pair(
            s, "search_agentic_chunks", {"query": "project overview"})
        # agentic 结果含 LLM 重写，同盒子两次运行也可能不同——钉**形状**而非内容
        for out in (local, fwd):
            assert "results" in out and "query_used" in out and "total" in out, out
            assert not out.get("error"), out

    def test_trigger_propagation_no_refs_is_fast_and_same(self, self_hub, fm_col):
        """无下游引用的笔记 → 传播立即返回空清单（不触发 LLM），两侧同形。"""
        s = _Mcp()
        created = s.call("create_note", {"collection": fm_col, "title": "__verify_b4_note__"})
        assert not created.get("error"), created
        nid = created["id"]
        try:
            local, fwd = _pair(
                s, "trigger_propagation",
                {"collection": fm_col, "note_id": nid, "auto": False})
            assert local == fwd, (local, fwd)
            assert local.get("updated_note_ids") == [], local
        finally:
            s.call("delete_note", {"collection": fm_col, "note_id": nid})


class TestRestEndpointsSanity:
    """新 REST 端点直打：形状 = 工具信封；网页路径不受影响。"""

    def test_library_tree_endpoint(self, fm_col):
        out = _rest(f"/api/file-mgmt/{fm_col}/library-tree")
        assert {"collection", "folders", "summary"} <= set(out), out

    def test_timeline_endpoint(self, fm_col):
        out = _rest(f"/api/file-mgmt/{fm_col}/timeline")
        assert {"collection", "timeline", "chains"} <= set(out), out

    def test_versions_endpoint_unassigned_semantics(self, fm_col):
        out = _rest(f"/api/file-mgmt/{fm_col}/files/ghost/versions")
        assert out.get("error"), out  # 坏 id → 200+error（工具同形）

    def test_search_direct_endpoint(self):
        cols = _rest("/api/collections")
        col = next((c["id"] for c in cols if not c.get("archived")), None)
        if not col:
            pytest.skip()
        out = _rest("/api/search/direct", "POST",
                    {"query": "project", "collection": col, "top_k": 2})
        assert "results" in out and "query_used" in out, out

    def test_search_direct_bad_collection_is_error_dict(self):
        out = _rest("/api/search/direct", "POST",
                    {"query": "x", "collection": "col_nope"})
        assert out.get("error") and "does not exist" in out["error"], out

    def test_document_text_endpoint(self, fm_col):
        out = _rest(f"/api/documents/text?collection={fm_col}&file_id=ghost")
        assert out.get("error") and out.get("extract_status"), out
