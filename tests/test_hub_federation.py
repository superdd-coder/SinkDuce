"""Hub 联邦原语（第 4 步）：``list_boxes`` / ``switch_box`` / 转发层。

**本文件的重点是「诚实」和「不串味」**，不是快乐路径：

1. ``list_boxes`` / ``switch_box`` 必须**如实**告诉模型哪些工具会被转发。转发能力
   从真实注册表算出来（不是写死的清单）——写死的清单会随工具增减过时，而过时的清单
   等于**对模型说谎**（它据此以为某个工具已经切到别的盒子上）。
2. 会话级状态：两个会话各自的「当前盒子」不能互相干扰。
3. 转发只对**已确认等价**的工具生效；其余工具在切盒子后仍打本机（且被如实列出）。
4. 调用失败必须变成结构化 error，不能让异常冒到 MCP 协议层（模型看到 traceback
   而不是「盒子连不上」）。

跑法：``pytest tests/test_hub_federation.py``
"""

from __future__ import annotations

import asyncio
import json

import pytest

from mcp.server.fastmcp import Context as _AnnoContext


# 模块级定义：注解才能在**定义处的全局命名空间**求值（本文件有
# `from __future__ import annotations`，注解是延迟求值的字符串）。
async def _anno_a(x: str, ctx: _AnnoContext | None = None) -> str: ...


async def _anno_b(x: str, whatever: _AnnoContext = None) -> str: ...


@pytest.fixture(autouse=True)
def _tmp_hub(tmp_path, monkeypatch):
    from src.hub import state, store

    monkeypatch.setattr(store, "HUB_DIR", tmp_path / "hub")
    store.clear()
    yield

    # 会话状态是 WeakKeyDictionary：显式清掉，避免跨测试串味
    state._states.clear()


@pytest.fixture
def hub_box():
    """装一台「盒子」。默认地址连不上（用于离线分支）。"""
    from src.hub import store

    def _install(box_id="rb1", name="RemoteBox", addr="http://127.0.0.1:9", token="sd1_x"):
        store.replace_boxes(
            [{"box_id": box_id, "box_name": name, "addr": addr, "token": token}]
        )
        return store.get_box(box_id)

    return _install


class FakeSession:
    """两个会话 = 两个对象（WeakKeyDictionary 靠身份区分）。"""


class FakeCtx:
    def __init__(self, session=None):
        self.session = session


def _loads(raw: str) -> dict:
    return json.loads(raw)


# ── list_boxes ───────────────────────────────────────────────


class TestListBoxes:
    def test_no_boxes(self):
        from src.hub.tools import list_boxes

        out = _loads(asyncio.run(list_boxes(FakeCtx(FakeSession()))))
        assert out["boxes"] == []
        assert out["current"] is None

    def test_lists_boxes_without_tokens(self, hub_box):
        """**核心**：钥匙绝不出现在给模型的返回值里（那是凭据）。"""
        from src.hub.tools import list_boxes

        hub_box()
        raw = asyncio.run(list_boxes(FakeCtx(FakeSession())))
        assert "sd1_x" not in raw, "钥匙泄漏到模型上下文"
        out = _loads(raw)
        assert [b["name"] for b in out["boxes"]] == ["RemoteBox"]
        assert out["boxes"][0]["has_key"] is True

    def test_reports_routing_honestly(self, hub_box):
        """**核心回归**：``tool_routing`` 必须来自真实注册表。

        routed_tools 必须与注册表逐项一致；local_only 必须恰好是「全部工具 −
        已转发 − Hub 原语 − 不可远端化」——模型据此判断哪些工具切了盒子仍打本机。
        （写类工具随批次 2 接入，这里的断言是**动态推导**的，不随批次演进失效；
        钉死的旧断言「delete_collection 未转发」在批次 2 接上它之后成了假失败。）
        """
        from src.hub.routing import HUB_NATIVE_TOOLS, ROUTES, routed_tool_names
        from src.hub.tools import list_boxes
        from src.mcp.server import MCP_TOOL_NAMES

        hub_box()
        out = _loads(asyncio.run(list_boxes(FakeCtx(FakeSession()))))
        assert out["routed_tools"] == routed_tool_names()
        assert out["tool_routing"] is True
        # 与生产端同公式：全部 − 已转发 − Hub 原语。不可远端化的 staging 工具
        # **留在** local_only（它们切盒子后确实仍打本机——被守卫拒绝，不是转发）。
        expected_local_only = sorted(
            n for n in MCP_TOOL_NAMES
            if n not in set(ROUTES) | set(HUB_NATIVE_TOOLS)
        )
        assert out["local_only_tools"] == expected_local_only
        # 批次 5 后**全部真实工具都已接转发**——local_only 必须为空（Hub 原语
        # 除外）。这条钉住「没有工具被悄悄落下」。
        assert out["local_only_tools"] == [], out["local_only_tools"]
        assert "list_boxes" not in out["local_only_tools"], "Hub 原语不该列进「仍打本机」"
        assert "switch_box" not in out["local_only_tools"]
        # 已转发的写工具绝不能再列进 local_only（那是「说谎」的方向）
        assert "delete_collection" not in out["local_only_tools"]
        assert "trigger_propagation" not in out["local_only_tools"]
        assert "list_documents" not in out["local_only_tools"]

    def test_routing_flag_false_when_nothing_routed(self, hub_box, monkeypatch):
        """没有任何工具接入时不能吹牛说「已转发」。"""
        from src.hub import routing
        from src.hub.tools import list_boxes

        hub_box()
        monkeypatch.setattr(routing, "ROUTES", {})
        out = _loads(asyncio.run(list_boxes(FakeCtx(FakeSession()))))
        assert out["tool_routing"] is False
        assert out["routed_tools"] == []
        assert "list_notes" in out["local_only_tools"]

    def test_selected_box_marked_current(self, hub_box):
        from src.hub import state
        from src.hub.tools import list_boxes

        hub_box()
        sess = FakeSession()
        state.set_current_box_id(sess, "rb1")
        out = _loads(asyncio.run(list_boxes(FakeCtx(sess))))
        assert out["boxes"][0]["current"] is True
        assert out["current"]["box_id"] == "rb1"

    def test_disconnected_box_is_reported(self, hub_box):
        """选中的盒子被移除后不能假装还在——要明说并给出回头路。"""
        from src.hub import state, store
        from src.hub.tools import list_boxes

        hub_box()
        sess = FakeSession()
        state.set_current_box_id(sess, "rb1")
        store.clear()
        out = _loads(asyncio.run(list_boxes(FakeCtx(sess))))
        assert out["current"]["disconnected"] is True
        assert "switch_box" in out["hint"]


# ── switch_box ───────────────────────────────────────────────


class TestSwitchBox:
    def test_local_resets(self, hub_box):
        from src.hub import state
        from src.hub.tools import switch_box

        hub_box()
        sess = FakeSession()
        state.set_current_box_id(sess, "rb1")
        out = _loads(asyncio.run(switch_box("local", FakeCtx(sess))))
        assert out["switched_to"] == "local"
        assert state.current_box_id(sess) == ""

    def test_local_is_case_insensitive(self, hub_box):
        from src.hub.tools import switch_box

        out = _loads(asyncio.run(switch_box("LOCAL", FakeCtx(FakeSession()))))
        assert out["switched_to"] == "local"

    def test_unknown_box_lists_connected(self, hub_box):
        from src.hub.tools import switch_box

        hub_box()
        out = _loads(asyncio.run(switch_box("Nope", FakeCtx(FakeSession()))))
        assert out.get("code") == "unknown_box"
        assert out["connected"] == ["RemoteBox"]

    def test_empty_name_rejected(self):
        from src.hub.tools import switch_box

        out = _loads(asyncio.run(switch_box("   ", FakeCtx(FakeSession()))))
        assert "error" in out

    def test_switch_by_box_id_also_works(self, hub_box):
        from src.hub.tools import switch_box

        hub_box()
        out = _loads(asyncio.run(switch_box("rb1", FakeCtx(FakeSession()))))
        assert out["switched_to"] == "RemoteBox"

    def test_offline_box_still_switches_but_says_so(self, hub_box):
        """**核心**：盒子离线时必须**切成功但明说不可达**。

        若因为探针失败就拒绝切换，用户会因为「那台正好离线」而无法切过去查看、
        也无法切走（会卡在旧盒子上）。失败信息要能指导下一步。
        """
        from src.hub import state
        from src.hub.tools import switch_box

        hub_box(addr="http://127.0.0.1:9")  # 无监听 → 连不上
        sess = FakeSession()
        out = _loads(asyncio.run(switch_box("RemoteBox", FakeCtx(sess))))
        assert out["reachable"] is False
        assert state.current_box_id(sess) == "rb1", "离线也必须切过去"
        assert "reason" in out and out["reason"]
        assert "switch_box" in out["note"]

    def test_switch_uses_session_scope_not_global(self, hub_box):
        """**核心回归**：两个会话的「当前盒子」互不影响。

        若状态放在进程级全局，一个 Agent 切盒子会静默改掉另一个 Agent 的盒子。
        """
        from src.hub import state
        from src.hub.tools import switch_box

        hub_box(name="A", box_id="ba")
        s1, s2 = FakeSession(), FakeSession()
        asyncio.run(switch_box("A", FakeCtx(s1)))
        assert state.current_box_id(s1) == "ba"
        assert state.current_box_id(s2) == "", "会话 2 被会话 1 污染了"


class TestSwitchBoxWithLiveBox:
    """用一个真实的本地 HTTP 服务当远端盒子，验证探针四件事。"""

    @pytest.fixture
    def live_box(self, hub_box):
        import socket
        import threading

        import uvicorn
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        async def me(request):
            return JSONResponse(
                {
                    "actor_id": "k1",
                    "role_id": "member",
                    "role_name": "Member",
                    "modules": {"chat": True, "database": True, "tasks": False},
                    "domains": {"meeting": True, "hot_words": False, "people": False},
                }
            )

        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()

        app = Starlette(routes=[Route("/api/identity/me", me)])
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        )
        ready = threading.Event()
        stop = threading.Event()

        def run():
            async def _serve():
                task = asyncio.create_task(server.serve())
                while not server.started:
                    await asyncio.sleep(0.02)
                ready.set()
                while not stop.is_set():
                    await asyncio.sleep(0.05)
                server.should_exit = True
                await task

            asyncio.run(_serve())

        t = threading.Thread(target=run, daemon=True)
        t.start()
        assert ready.wait(20), "测试用的假盒子没起来"
        hub_box(addr=f"http://127.0.0.1:{port}")
        yield port
        stop.set()
        t.join(timeout=10)

    def test_probe_reports_role_and_unavailable(self, live_box, hub_box):
        """探针一次拿到四件事：可达 / 钥匙有效 / 角色 / 哪些能力不可用。"""
        from src.hub.tools import switch_box

        out = _loads(asyncio.run(switch_box("RemoteBox", FakeCtx(FakeSession()))))
        assert out["reachable"] is True
        assert out["role"] == "member"
        assert out["role_name"] == "Member"
        # tasks 模块 + hot_words/people 两域都应为不可用
        assert "tasks" in out["unavailable"]
        assert "hot_words" in out["unavailable"]

    def test_probe_result_persisted_for_list_boxes(self, live_box, hub_box):
        from src.hub import store
        from src.hub.tools import switch_box

        asyncio.run(switch_box("RemoteBox", FakeCtx(FakeSession())))
        assert store.get_box("rb1")["online"] is True
        assert store.get_box("rb1")["role"] == "member"

    def test_401_key_rejected_is_reported(self, hub_box):
        """钥匙被吊销 → 明说 401，而不是含糊的「连不上」（两者处置不同）。"""
        import socket
        import threading

        import uvicorn
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        async def me(request):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)

        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        app = Starlette(routes=[Route("/api/identity/me", me)])
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        )
        ready, stop = threading.Event(), threading.Event()

        def run():
            async def _serve():
                task = asyncio.create_task(server.serve())
                while not server.started:
                    await asyncio.sleep(0.02)
                ready.set()
                while not stop.is_set():
                    await asyncio.sleep(0.05)
                server.should_exit = True
                await task

            asyncio.run(_serve())

        t = threading.Thread(target=run, daemon=True)
        t.start()
        try:
            assert ready.wait(20)
            hub_box(addr=f"http://127.0.0.1:{port}")
            from src.hub.tools import switch_box

            out = _loads(asyncio.run(switch_box("RemoteBox", FakeCtx(FakeSession()))))
            assert out["reachable"] is False
            assert "401" in out["reason"]
        finally:
            stop.set()
            t.join(timeout=10)


# ── 转发层 ───────────────────────────────────────────────────


class TestRouting:
    def test_path_params_filled(self):
        from src.hub.routing import _fill_path

        assert _fill_path("/notes/{collection}", {"collection": "col_a"}) == "/notes/col_a"

    def test_path_params_are_percent_encoded(self):
        """**核心安全回归**：路径参数里的斜杠必须被编码。

        实测过真实漏洞（未编码时）::

            list_notes(collection="x/../../pairing/tokens")
              → 读到了远端盒子的**钥匙列表**

        即 ``ROUTES`` 那张映射表不再是边界 —— 参数一穿就绕过它，而且不报错。
        两层防线：① 含 ``..`` 直接拒（下面那条测试）；② 斜杠一律编码成 ``%2F``，
        于是"看似一段"的值不可能变成多段路径。
        """
        from src.hub.routing import _fill_path

        got = _fill_path("/notes/{collection}", {"collection": "a/b/c"})
        assert "/" not in got[len("/notes/"):], f"斜杠没被编码：{got}"
        assert got.count("%2F") + got.count("%2f") == 2, got

    def test_traversal_is_blocked_end_to_end(self):
        """把实测那条真实攻击串直接喂进来，确认它**打不出** ``/pairing/tokens``。"""
        from src.hub.routing import _fill_path

        with pytest.raises(KeyError):
            _fill_path("/notes/{collection}", {"collection": "x/../../pairing/tokens"})

    def test_dotdot_is_refused_explicitly(self):
        """含 ``..`` 直接拒绝（比"编码后打到别的路径"给出更清楚的原因）。"""
        from src.hub.routing import _fill_path

        with pytest.raises(KeyError, match=r"\.\."):
            _fill_path("/notes/{collection}", {"collection": "../hub/boxes"})

    def test_empty_path_param_refused(self):
        """空值会拼出 ``//path``（实测 404），视为缺参数。"""
        from src.hub.routing import _fill_path

        with pytest.raises(KeyError, match="empty"):
            _fill_path("/file-mgmt/{collection}/todos", {"collection": ""})

    def test_missing_path_param_raises(self):
        from src.hub.routing import _fill_path

        with pytest.raises(KeyError):
            _fill_path("/notes/{collection}", {})

    def test_ctx_param_name_detected_by_annotation(self):
        """按**注解**识别 Context（与 FastMCP 跳过它的依据一致），不是猜名字。

        ⚠️ 这里用模块级定义的函数（见文件顶部的 ``_anno_a`` / ``_anno_b``），
        不是函数内嵌套定义——本文件带 ``from __future__ import annotations``，
        嵌套函数的注解字符串要在**定义处**的全局命名空间里才求值得到，而
        ``Context`` 若只在测试函数内 import，求值就会失败（这一点与真实工具模块
        一致：它们都在模块顶部 import Context）。
        """
        from src.hub.routing import _ctx_param_name

        assert _ctx_param_name(_anno_a) == "ctx"
        assert _ctx_param_name(_anno_b) == "whatever", "参数名不叫 ctx 时也要靠注解认出来"

    def test_real_tool_modules_are_detected(self):
        """**核心回归**：真实工具模块（带延迟注解）也要能识别出会话参数。

        实测踩过：不带 ``eval_str=True`` 时注解是字符串、识别全部落空，只剩名字
        兜底——看起来正常，换名即静默回落到本机（查错盒子且不报错）。
        """
        from src.hub.routing import _ctx_param_name
        from src.hub.tools import list_boxes, switch_box
        from src.mcp.tools.notes import list_notes

        assert _ctx_param_name(list_notes) == "ctx"
        assert _ctx_param_name(list_boxes) == "ctx"
        assert _ctx_param_name(switch_box) == "ctx"

    def test_bind_kwargs_handles_positional(self):
        """**核心回归**：位置传参也要能归一成 kwargs。

        实测踩过：位置调用 ``list_notes("col_x")`` 时 kwargs 里没有 collection →
        路径拼不出来 → 报 bad_args。真实 MCP 走关键字，但内部/测试直调会走位置。
        """
        from src.hub.routing import _bind_kwargs

        async def fn(collection: str, other: int = 1) -> None: ...

        assert _bind_kwargs(fn, ("c1",), {}) == {"collection": "c1"}
        assert _bind_kwargs(fn, (), {"collection": "c2"}) == {"collection": "c2"}
        # 绑定失败时原样返回 kwargs（后续会报清晰的 bad_args，而不是猜值）
        assert _bind_kwargs(fn, (), {"nope": 1}) == {"nope": 1}

    def test_only_confirmed_tools_are_routed(self):
        """**核心**：转发是逐个确认的，不是「全都转发」。

        未确认等价的工具必须在 ROUTES 之外（它们在切盒子后仍打本机，
        并由 list_boxes 如实列出）。

        ⚠️ ``ROUTES`` 是**导入工具模块时的副作用**填充的，所以必须先 import
        ``src.mcp.server``，否则在单独跑这个文件时会看到空字典
        （实测过：断言结果随测试顺序变化，是很坏的测试）。
        """
        import src.mcp.server  # noqa: F401  (触发工具注册)
        from src.hub.routing import ROUTES

        # 已确认等价的
        assert "list_notes" in ROUTES
        # ``list_folders`` 最初因 include_unassigned 语义不同而不能接；
        # 批次 1 给 REST 加了参数 + 路由里钉死 False 之后**已经接上**（见等价性测试）。
        assert "list_folders" in ROUTES
        # 明确**没**接入的：抽查当前仍在等后续批次的（随批次演进换样本——
        # 批次 4 建了 library-tree/timeline 端点并接上后，旧断言成了假失败）
        from src.hub.routing import NOT_REMOTABLE, ROUTES as _R
        # 明确**永不**接入的（staging 令牌是进程内表，跨盒子无解）
        assert set(NOT_REMOTABLE) <= set(_R) ^ set(NOT_REMOTABLE)  # 它们不在 ROUTES
        for n in NOT_REMOTABLE:
            assert n not in _R
