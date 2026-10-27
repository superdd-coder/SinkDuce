"""批次 5 等价性测试：staging 上传四件套的跨盒子转发。

核心机制：Agent 把字节 stage 到 **Hub 本机**（POST /api/mcp/stage-content），
切到远端后调用上传工具 → Hub 取本机 staging 字节 → multipart 直传远端。
测试法（self-hub 同前）：**本地一条 + 转发一条**，比对信封、核对远端真的
收到了字节；靶子全 `__verify_` 前缀 + 用完即清。

跑法：``pytest tests/test_hub_batch5_equivalence.py``
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
    not _has_service(), reason="需要 18900 在跑（批次 5 真机等价性）"
)


def _rest(path: str, method="GET", body=None, raw: bytes | None = None,
          headers: dict | None = None):
    data = raw if raw is not None else (
        json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if raw is not None:
        req.add_header("Content-Type", "application/octet-stream")
    elif data is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            content = r.read().decode("utf-8", "ignore")
            return json.loads(content) if content.strip() else None
    except urllib.error.HTTPError as e:
        return {"_status": e.code}
    except Exception:
        return None


def _stage(content: bytes, filename: str) -> str:
    """往 **Hub 本机** stage 字节，拿令牌（Agent 的真实操作路径）。"""
    out = _rest("/api/mcp/stage-content", "POST", raw=content,
                headers={"X-Filename": filename})
    assert isinstance(out, dict) and out.get("staging_token"), out
    return out["staging_token"]


class _Mcp:
    def __init__(self):
        import httpx

        self._c = httpx.Client(timeout=300)
        r = self._c.post(f"{BASE}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                   "clientInfo": {"name": "b5-equiv", "version": "0"}}},
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
        {"box_id": "__b5__", "box_name": "B5Self", "addr": BASE, "token": ""},
    ])
    yield
    _put([{"box_id": b["box_id"], "box_name": b["box_name"], "addr": b["addr"],
           "last_checked": b.get("last_checked", "")} for b in original])


@pytest.fixture
def fm_col():
    # 每次唯一名：API 列表按 Qdrant 派生，**在册但 Qdrant 没了的残余**在列表里
    # 看不见、却仍占名字（find_collection_by_name 全表扫描）——固定名会被这种
    # 尸体卡死在 "already exists"（变异校验时实踩）。唯一名 + 按 id 清理。
    import uuid as _uuid

    name = f"__verify_b5_{_uuid.uuid4().hex[:8]}"
    out = _rest("/api/collections", "POST", {"name": name})
    assert out and out.get("id"), out
    yield out["id"]
    _rest(f"/api/collections/{out['id']}", "DELETE")


def _keys(d: dict, *names: str) -> dict:
    return {k: d.get(k) for k in names}


class TestBatch5UploadEquivalence:
    def test_upload_file_local_vs_forwarded(self, self_hub, fm_col):
        """同一份字节上传两次（本地/转发），信封逐字段同形，远端真收到。"""
        s = _Mcp()
        tok_l = _stage(b"__verify b5 local content__", "__verify_b5_local.txt")
        tok_f = _stage(b"__verify b5 fwd content__", "__verify_b5_fwd.txt")

        s.call("switch_box", {"name": "local"})
        local = s.call("upload_file_from_staging",
                       {"collection": fm_col, "staging_token": tok_l})
        s.call("switch_box", {"name": "B5Self"})
        fwd = s.call("upload_file_from_staging",
                     {"collection": fm_col, "staging_token": tok_f})
        s.call("switch_box", {"name": "local"})

        assert not local.get("error") and not fwd.get("error"), (local, fwd)
        assert _keys(local, "message", "collection", "folder_id") == \
               _keys(fwd, "message", "collection", "folder_id"), (local, fwd)
        assert fwd["file_id"] and fwd["file_id"] != local["file_id"]
        assert fwd["file"]["filename"] == "__verify_b5_fwd.txt"
        # 远端真收到了字节（读回正文）
        text = s.call("get_document_text", {"collection": fm_col, "file_id": fwd["file_id"]})
        assert "__verify b5 fwd content__" in (text.get("content") or ""), text

    def test_upload_auto_rename_forwarded(self, self_hub, fm_col):
        """on_name_conflict=auto_rename 必须透传到远端——丢了它会静默变成
        409（REST 默认 error），同一调用两种结局（批次 5 的忠实性要点）。"""
        s = _Mcp()
        tok1 = _stage(b"first", "__verify_b5_dup.txt")
        tok2 = _stage(b"second", "__verify_b5_dup.txt")
        s.call("switch_box", {"name": "B5Self"})
        first = s.call("upload_file_from_staging",
                       {"collection": fm_col, "staging_token": tok1})
        second = s.call("upload_file_from_staging",
                        {"collection": fm_col, "staging_token": tok2,
                         "on_name_conflict": "auto_rename"})
        s.call("switch_box", {"name": "local"})
        assert not first.get("error") and not second.get("error"), (first, second)
        assert second["file_id"] != first["file_id"], "auto_rename 没生效（同名被拒或覆盖）"

    def test_upload_file_version_forwarded(self, self_hub, fm_col):
        s = _Mcp()
        tok1 = _stage(b"v1 content here", "__verify_b5_ver.txt")
        created = s.call("upload_file_from_staging",
                         {"collection": fm_col, "staging_token": tok1})
        assert not created.get("error"), created
        fid = created["file_id"]

        tok2 = _stage(b"v2 content here", "__verify_b5_ver.txt")
        s.call("switch_box", {"name": "B5Self"})
        fwd = s.call("upload_file_version_from_staging",
                     {"collection": fm_col, "file_id": fid,
                      "staging_token": tok2, "commit_message": "v2"})
        s.call("switch_box", {"name": "local"})
        assert not fwd.get("error"), fwd
        assert fwd["message"] == "New version uploaded; ingest may be async"
        assert fwd["file_id"] == fid
        assert fwd.get("task_id") or fwd["file"].get("task_id")

    def test_upload_document_forwarded(self, self_hub, fm_col):
        s = _Mcp()
        tok = _stage(b"__verify b5 document bytes__", "__verify_b5_doc.md")
        s.call("switch_box", {"name": "B5Self"})
        fwd = s.call("upload_document_from_staging",
                     {"collection": fm_col, "staging_token": tok})
        s.call("switch_box", {"name": "local"})
        assert not fwd.get("error"), fwd
        assert _keys(fwd, "message", "filename", "size_bytes", "collection") == {
            "message": "Content queued for processing",
            "filename": "__verify_b5_doc.md",
            "size_bytes": len(b"__verify b5 document bytes__"),
            "collection": fm_col,
        }, fwd
        assert fwd["file_id"] and fwd["task_id"], fwd
        # **落点核对**（审查 #2 的教训：只对信封自述的 collection 做断言
        # 抓不住「传错通道 → 全部落进 default」的谎报——必须读回远端任务
        # 的真实归属）
        task = s.call("get_task_status", {"task_id": fwd["task_id"]})
        assert task.get("collection") == fm_col, (fwd, task)

    def test_upload_meeting_audio_forwarded(self, self_hub):
        s = _Mcp()
        created = s.call("create_meeting", {"title": "__verify_b5_audio__"})
        assert not created.get("error"), created
        mid = created["id"]
        try:
            tok = _stage(b"FAKE_WEBM_AUDIO_BYTES", "__verify_b5_audio.webm")
            s.call("switch_box", {"name": "B5Self"})
            fwd = s.call("upload_meeting_audio_from_staging",
                         {"staging_token": tok, "meeting_id": mid})
            s.call("switch_box", {"name": "local"})
            assert not fwd.get("error"), fwd
            assert fwd["message"] == "Audio uploaded via staging"
            assert fwd["filename"] == "__verify_b5_audio.webm"
            assert fwd["size_bytes"] == len(b"FAKE_WEBM_AUDIO_BYTES")
            assert fwd.get("audio_path"), fwd
            m = s.call("get_meeting", {"meeting_id": mid})
            assert m.get("audio_path") or m.get("has_transcript") is not None
        finally:
            _rest(f"/api/meetings/{mid}", "DELETE")

    def test_bad_token_same_error_both_sides(self, self_hub, fm_col):
        s = _Mcp()
        for tool, args in (
            ("upload_file_from_staging", {"collection": fm_col, "staging_token": "deadbeef"}),
            ("upload_document_from_staging", {"collection": fm_col, "staging_token": "deadbeef"}),
            ("upload_meeting_audio_from_staging", {"staging_token": "deadbeef", "meeting_id": "x"}),
        ):
            s.call("switch_box", {"name": "local"})
            local = s.call(tool, args)
            s.call("switch_box", {"name": "B5Self"})
            fwd = s.call(tool, args)
            s.call("switch_box", {"name": "local"})
            assert local.get("error") and fwd.get("error"), (tool, local, fwd)
            assert "not found or expired" in local["error"]
            assert "not found or expired" in fwd["error"], (tool, fwd)

    def test_bad_filename_rejected_before_transfer(self, self_hub, fm_col):
        """坏文件名（经工具的 filename 覆盖参数——stage 端点自己会拒直传的
        坏名字）在 **Hub 侧**就被拒：不浪费跨盒传输、远端无半成品。"""
        s = _Mcp()
        tok = _stage(b"x", "__verify_ok.txt")
        s.call("switch_box", {"name": "B5Self"})
        fwd = s.call("upload_file_from_staging",
                     {"collection": fm_col, "staging_token": tok,
                      "filename": "../evil.txt"})
        s.call("switch_box", {"name": "local"})
        assert fwd.get("error"), fwd
        assert "Invalid filename" in fwd["error"], fwd
        # 且远端没收到任何东西（集合文件数仍为 0）
        files = _rest(f"/api/file-mgmt/{fm_col}/files?lean=true")
        assert files.get("total") == 0, files

    def test_bad_collection_refused(self, self_hub):
        s = _Mcp()
        tok = _stage(b"x", "__verify_x.txt")
        s.call("switch_box", {"name": "B5Self"})
        fwd = s.call("upload_file_from_staging",
                     {"collection": "col_nope", "staging_token": tok})
        s.call("switch_box", {"name": "local"})
        assert fwd.get("error") and "does not exist" in fwd["error"], fwd


class TestUploadForwardsUnit:
    """离线单测：转发器的本机取字节/校验顺序。"""

    def test_upload_file_forward_validates_conflict_enum_locally(self):
        from src.hub.upload_forwards import upload_file_forward

        class _Api:
            async def post_multipart(self, *a, **k):  # 不该被调到
                raise AssertionError("枚举校验失败还发了传输")

        async def _fake_load(*a):
            return b"x", "a.txt"

        import asyncio

        import src.hub.upload_forwards as uf

        orig = uf._load_bytes
        uf._load_bytes = _fake_load
        try:
            # ⚠️ 必须在补丁作用域内 await——协程体在 asyncio.run 时才执行，
            # 若先建协程、finally 还原补丁再 run，跑的是真 loader（async 陷阱）
            out = asyncio.run(upload_file_forward(
                _Api(), {"collection": "c", "staging_token": "t",
                         "on_name_conflict": "overwrite"}))
        finally:
            uf._load_bytes = orig
        assert "on_name_conflict must be" in out["error"], out
