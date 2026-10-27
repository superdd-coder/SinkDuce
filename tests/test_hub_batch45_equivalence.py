"""批次 4.5 等价性测试：「端点现成」组 13 个工具（58/65 全部非 staging 接完）。

方法同前几批：Hub 指向自己，本地 vs 转发逐字段比对。重创建的用例（总结触发）
走错误路径等价；真读的用真实靶子（只读零破坏）。

跑法：``pytest tests/test_hub_batch45_equivalence.py``
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
    not _has_service(), reason="需要 18900 在跑（批次 4.5 真机等价性）"
)


def _rest(path: str, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = r.read().decode("utf-8", "ignore")
            return json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as e:
        return {"_status": e.code}
    except Exception:
        return None


class _Mcp:
    def __init__(self):
        import httpx

        self._c = httpx.Client(timeout=180)
        r = self._c.post(f"{BASE}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                   "clientInfo": {"name": "b45-equiv", "version": "0"}}},
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
        {"box_id": "__b45__", "box_name": "B45Self", "addr": BASE, "token": ""},
    ])
    yield
    _put([{"box_id": b["box_id"], "box_name": b["box_name"], "addr": b["addr"],
           "last_checked": b.get("last_checked", "")} for b in original])


def _pair(s, name, args):
    s.call("switch_box", {"name": "local"})
    local = s.call(name, args)
    sw = s.call("switch_box", {"name": "B45Self"})
    assert sw.get("reachable"), sw
    forwarded = s.call(name, args)
    s.call("switch_box", {"name": "local"})
    return local, forwarded


def _first_fm_col(s):
    """挑一个有 fm DB 且有文件的库（REST 建+上传太重——用现成的 NORMAL 类库）。"""
    cols = _rest("/api/collections") or []
    for c in cols:
        if c.get("archived"):
            continue
        files = _rest(f"/api/file-mgmt/{c['id']}/files?lean=true")
        if isinstance(files, dict) and files.get("total", 0) > 0:
            return c["id"]
    return (cols or [{}])[0].get("id")


class TestBatch45Equivalence:
    def test_collections_list_and_info(self, self_hub):
        s = _Mcp()
        local, fwd = _pair(s, "list_collections", {})
        assert local == fwd, (json.dumps(local)[:200], json.dumps(fwd)[:200])
        cols = local if isinstance(local, list) else []
        if cols:
            col = next((c for c in cols if not c.get("archived")), cols[0])
            local, fwd = _pair(s, "get_collection", {"collection": col["id"]})
            assert local == fwd, (local, fwd)
            assert "config" in fwd and "sparse_vocab" not in fwd["config"]

    def test_documents_and_summary(self, self_hub):
        s = _Mcp()
        col = _first_fm_col(s)
        if not col:
            pytest.skip()
        local, fwd = _pair(s, "list_documents", {"collection": col})
        assert local == fwd, (local, fwd)
        # doc-summary：坏 source 两侧同错（真实 source 的摘要形状比对留给等价透传）
        local, fwd = _pair(
            s, "get_doc_summary",
            {"collection": col, "source": "__file__:ghost"})
        assert local.get("error") and fwd.get("error"), (local, fwd)
        assert "No summary found" in fwd["error"], fwd

    def test_file_chunks_missing_and_real(self, self_hub):
        s = _Mcp()
        col = _first_fm_col(s)
        if not col:
            pytest.skip()
        # 真文件：拿一个 file_id 比对（含 chunk 字段集）
        files = _rest(f"/api/file-mgmt/{col}/files?lean=true")
        fid = next((f["file_id"] for f in files.get("files") or []), None)
        if fid:
            local, fwd = _pair(
                s, "get_file_chunks",
                {"collection": col, "file_id": fid, "limit": 5})
            assert local == fwd, (
                json.dumps(local)[:200], json.dumps(fwd)[:200])
            # 假值参数的 parity：显式 include_context=False / chunk_type 过滤
            #（query lambda 的 `or None` 假值陷阱防线——批次 4 两例的前科）
            for extra in ({"include_context": False},
                          {"chunk_type": "child"},
                          {"chunk_type": ""},  # 文档化合法值：包含子块
                          {"include_context": False, "chunk_type": "parent"}):
                local, fwd = _pair(
                    s, "get_file_chunks",
                    {"collection": col, "file_id": fid, "limit": 5, **extra})
                assert local == fwd, (extra, json.dumps(local)[:150], json.dumps(fwd)[:150])
        # 幽灵 file_id：工具语义是**成功 + 空 chunks**（身份解析只做规范化，
        # 不是存在性校验）——两侧必须同形
        local, fwd = _pair(
            s, "get_file_chunks",
            {"collection": col, "file_id": "ghost"})
        assert local == fwd, (local, fwd)
        assert local.get("total") == 0, local

    def test_get_file_node_chains(self, self_hub):
        s = _Mcp()
        col = _first_fm_col(s)
        if not col:
            pytest.skip()
        files = _rest(f"/api/file-mgmt/{col}/files?lean=true")
        fid = next((f["file_id"] for f in files.get("files") or []), None)
        if fid:
            local, fwd = _pair(s, "get_file", {"collection": col, "file_id": fid})
            assert local == fwd, (json.dumps(local)[:200], json.dumps(fwd)[:200])
        local, fwd = _pair(s, "get_file", {"collection": col, "file_id": "ghost"})
        assert local.get("error") and fwd.get("error")

        # get_node / list_chains
        local, fwd = _pair(s, "list_chains", {"collection": col})
        assert local == fwd, (local, fwd)
        local, fwd = _pair(s, "get_node", {"collection": col, "node_id": "ghost"})
        assert local.get("error") and fwd.get("error"), (local, fwd)

    def test_meeting_tools(self, self_hub):
        s = _Mcp()
        meets = s.call("list_meetings", {"limit": 20})
        items = meets.get("meetings") or []
        if not items:
            pytest.skip("盒子上没有会议")
        mid = items[0]["id"]
        # transcript：分页窗口
        local, fwd = _pair(
            s, "get_meeting_transcript",
            {"meeting_id": mid, "offset": 0, "limit": 3})
        assert local == fwd, (json.dumps(local)[:200], json.dumps(fwd)[:200])
        # get_section：general（没有总结时两侧同错也算 parity）
        local, fwd = _pair(s, "get_section", {"meeting_id": mid, "tab_id": "general"})
        assert local == fwd or (
            local.get("error") and fwd.get("error")), (local, fwd)
        # 坏会议 id：同错
        local, fwd = _pair(
            s, "get_meeting_transcript", {"meeting_id": "no_such"})
        assert local.get("error") and fwd.get("error")

    def test_meeting_catalog_and_lookup(self, self_hub):
        s = _Mcp()
        local, fwd = _pair(s, "list_meeting_catalog", {})
        assert local == fwd, (json.dumps(local)[:200], json.dumps(fwd)[:200])
        # lookup：无命中查询（形状 parity；命中与否取决于索引）
        local, fwd = _pair(
            s, "lookup_meeting_transcript",
            {"query": "__zz_no_match_zz__"})
        for out in (local, fwd):
            assert not out.get("error") or out.get("error") == (local.get("error")), out

    def test_start_summary_error_paths(self, self_hub):
        """重创建路径走错误等价：坏会议 id + 无转录的会议都不真跑总结。"""
        s = _Mcp()
        local, fwd = _pair(
            s, "start_meeting_summary", {"meeting_id": "no_such"})
        assert local.get("error") and fwd.get("error"), (local, fwd)
        # 找一个无转录的会议（created 状态多半没有）——没有就跳过
        meets = s.call("list_meetings", {"limit": 20, "status": "created"})
        items = meets.get("meetings") or []
        if items:
            mid = items[0]["id"]
            local, fwd = _pair(s, "start_meeting_summary", {"meeting_id": mid})
            assert local == fwd or (
                local.get("error") and fwd.get("error")), (local, fwd)

    def test_unknown_collection_refuses_everywhere(self, self_hub):
        """坏库 id 的统一行为：两侧同错（不会静默成功/自动建库）。"""
        s = _Mcp()
        for name, args in (
            ("get_collection", {"collection": "col_nope"}),
            ("list_documents", {"collection": "col_nope"}),
            ("get_file_chunks", {"collection": "col_nope", "file_id": "x"}),
            ("get_file", {"collection": "col_nope", "file_id": "x"}),
            ("get_node", {"collection": "col_nope", "node_id": "x"}),
            ("list_chains", {"collection": "col_nope"}),
        ):
            local, fwd = _pair(s, name, args)
            assert local.get("error") and fwd.get("error"), (name, local, fwd)


class TestAgentEndpointsSanity:
    def test_collections_endpoints(self):
        out = _rest("/api/agent/collections")
        assert isinstance(out, list) and out, out
        col = out[0]["id"]
        info = _rest(f"/api/agent/collections/{col}")
        assert "config" in info and "points_count" in info, list(info)[:8]

    def test_web_collections_untouched(self):
        out = _rest("/api/collections")
        assert isinstance(out, list)
        if out:
            assert "permissions" in out[0], "网页版必须还是原形状"

    def test_chunks_endpoint_params(self):
        cols = _rest("/api/collections")
        col = next((c["id"] for c in cols if not c.get("archived")), None)
        if not col:
            pytest.skip()
        out = _rest(f"/api/agent/collections/{col}/chunks?file_id=ghost")
        # 幽灵 id：成功 + 空（与工具语义一致，不是错误）
        assert out.get("total") == 0 and out.get("chunks") == [], out

    def test_meeting_catalog_endpoint(self):
        out = _rest("/api/agent/meeting-catalog")
        assert isinstance(out, dict), out
