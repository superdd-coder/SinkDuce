"""远程模式瘦壳下的 Hub 语义（design §8）。

瘦壳里没有「本机内容」这回事，所以：

- ``box_targets()``（search_all_boxes 的扇出清单）跳过本机段；
- ``switch_box("local")`` 是非法目标（没有本机可回退），必须明确报错
  而不是假装「所有工具都作用在本机」；
- 未切盒就调用内容类工具 → 明确报错「先 switch_box 到远端盒子」，
  绝不能落进本机执行（本机 services 压根没初始化，落下去了只会得到
  莫名其妙的错误，更糟的是让人以为"本机就是空的"）。

正常模式（无 SINKDUCE_REMOTE_SHELL）的行为逐条不变 —— 每个测试都成对断言。
"""

from __future__ import annotations

import asyncio
import json

import pytest


@pytest.fixture(autouse=True)
def _tmp_hub(tmp_path, monkeypatch):
    from src.hub import state, store

    monkeypatch.setattr(store, "HUB_DIR", tmp_path / "hub")
    store.clear()
    state._states.clear()
    store.replace_boxes(
        [
            {
                "box_id": "rb1",
                "box_name": "RemoteBox",
                "addr": "http://10.0.0.5:18900",
                "token": "sd1_t",
            }
        ]
    )
    yield
    state._states.clear()


class FakeSession:
    pass


class FakeCtx:
    def __init__(self, session=None):
        self.session = session


def _set_shell(monkeypatch, on: bool):
    if on:
        monkeypatch.setenv("SINKDUCE_REMOTE_SHELL", "1")
    else:
        monkeypatch.delenv("SINKDUCE_REMOTE_SHELL", raising=False)


# ── 联邦检索目标 ───────────────────────────────────────────


def test_box_targets_skip_local_in_remote_shell(monkeypatch):
    from src.hub import federated

    _set_shell(monkeypatch, True)
    targets = federated.box_targets()
    assert [t["box_id"] for t in targets] == ["rb1"]
    assert all(not t["is_local"] for t in targets)


def test_box_targets_keep_local_in_normal_mode(monkeypatch):
    from src.hub import federated

    _set_shell(monkeypatch, False)
    targets = federated.box_targets()
    assert targets[0]["is_local"] is True
    assert [t["box_id"] for t in targets[1:]] == ["rb1"]


# ── switch_box("local") ────────────────────────────────────


def test_switch_box_local_rejected_in_remote_shell(monkeypatch):
    from src.hub import state
    from src.hub.tools import switch_box

    _set_shell(monkeypatch, True)
    out = json.loads(asyncio.run(switch_box("local")))
    assert out.get("error"), "远程模式切到 local 必须报错而不是假装切换成功"
    # connected 列显示名（与 unknown_box 分支同款，给模型看的可选项）
    assert "RemoteBox" in json.dumps(out.get("connected", []))
    # 没有产生「当前盒子=本机」的状态变更
    assert state.current_box_id(FakeSession()) == ""


def test_switch_box_local_still_works_in_normal_mode(monkeypatch):
    from src.hub.tools import switch_box

    _set_shell(monkeypatch, False)
    out = json.loads(asyncio.run(switch_box("local")))
    assert out.get("current", "x") is None
    assert "local" in json.dumps(out)


# ── 未切盒调用内容工具 ─────────────────────────────────────


def test_unswitched_content_tool_refused_in_remote_shell(monkeypatch):
    from src.hub import state
    from src.mcp import server as srv

    _set_shell(monkeypatch, True)
    sess = FakeSession()
    assert state.current_box_id(sess) == ""  # 默认=本机目标

    called = {"n": 0}

    async def spy(name, arguments):
        called["n"] += 1
        return "REAL-RAN"

    monkeypatch.setattr(srv, "_original_call_tool", spy)
    monkeypatch.setattr(srv.mcp, "get_context", lambda: FakeCtx(sess))

    out = asyncio.run(srv.mcp.call_tool("some_tool_added_next_year", {}))
    text = out.content[0].text
    assert called["n"] == 0, "瘦壳未切盒调用内容工具落到了本机执行"
    assert "remote shell" in text.lower()
    assert "switch_box" in text
    assert out.isError is True


def test_unswitched_content_tool_falls_through_in_normal_mode(monkeypatch):
    """正常模式（无 env）行为零变化：未切盒直通本机执行。"""
    from src.hub import state
    from src.mcp import server as srv

    _set_shell(monkeypatch, False)
    sess = FakeSession()
    assert state.current_box_id(sess) == ""

    called = {"n": 0}

    async def spy(name, arguments):
        called["n"] += 1
        return "REAL-RAN"

    monkeypatch.setattr(srv, "_original_call_tool", spy)
    monkeypatch.setattr(srv.mcp, "get_context", lambda: FakeCtx(sess))

    out = asyncio.run(srv.mcp.call_tool("some_tool_added_next_year", {}))
    assert called["n"] == 1
    assert out == "REAL-RAN"


def test_hub_native_tools_still_work_unswitched_in_remote_shell(monkeypatch):
    """list_boxes 等原语不属于任何盒子：瘦壳未切盒也必须照常可用。"""
    from src.hub import state
    from src.mcp import server as srv

    _set_shell(monkeypatch, True)
    sess = FakeSession()

    called = {"n": 0}

    async def spy(name, arguments):
        called["n"] += 1
        return "HUB-RAN"

    monkeypatch.setattr(srv, "_original_call_tool", spy)
    monkeypatch.setattr(srv.mcp, "get_context", lambda: FakeCtx(sess))

    for tool in ("list_boxes", "switch_box", "search_all_boxes"):
        out = asyncio.run(srv.mcp.call_tool(tool, {}))
        assert out == "HUB-RAN", f"{tool} 在瘦壳未切盒时被守卫误拒"
    assert called["n"] == 3
