"""MCP 传输配置：有状态会话 + 传输安全（2026-09-18 二次拍板）。

**两条都是前提驱动，不是偏好**——前提变了结论就要跟着变：

1. ``stateless_http=False``（从 stateless **切回**）—— 前一天切 stateless 是为了修
   ①身份冻结 ②会话劫持（任意钥匙借用别人会话的身份 = 提权）。**两者的危害前提都是
   「MCP 面上存在多个身份」。** 现在 ``/mcp`` 只认回环（见
   ``src/pairing/middleware.py``），能进来的恒为 owner：冻结的是 owner、能劫到的
   也是 owner —— 前提消失，危害消失。同时换回会话级状态，``elicit`` 与 Hub 的
   ``switch_box``「当前盒子」才有地方放。

   ⚠️ **顺序是承重的**：必须与「/mcp 只认回环」同时成立。MCP 还在局域网上敞着
   而又切成有状态 → 会话劫持立刻复活。

2. ``enable_dns_rebinding_protection=False`` —— SDK 那层是**静态** Host 白名单
   （只认 127.0.0.1:* / localhost:*），本机地址会漂移、桌面壳端口是 18910，
   静态清单只会带来「配错就 421」。等价保护由配对门卫动态提供
   （Host 必须回环或本机地址、Origin 必须本机来源或缺省、/mcp 必须是本机）。

跑法：``pytest tests/test_mcp_transport_config.py``
"""

from __future__ import annotations


class TestStatefulSession:
    def test_stateless_disabled(self):
        """**核心回归**：切回 stateless 会让 Hub 的会话级状态无处安放，
        并让 ``elicit`` 挂住（实测两次）——而它当初存在的理由（多身份前提下的
        身份冻结/会话劫持）已被「/mcp 只认回环」消掉。"""
        from src.mcp.server import mcp

        assert mcp.settings.stateless_http is False

    def test_session_manager_is_stateful(self):
        """settings 与真正建出来的 session manager 必须一致（防两处真相）。"""
        from src.mcp.server import get_http_app, mcp

        get_http_app()
        sm = mcp.session_manager
        assert sm is not None
        assert sm.stateless is False, "session manager 仍是 stateless —— settings 没传下去"


class TestTransportSecurity:
    def test_sdk_static_host_whitelist_disabled(self):
        """SDK 的静态 Host 白名单保持关闭，保护改由配对门卫动态提供。

        见 ``src/pairing/service.py::is_local_scope``（Host 回环或本机地址 +
        Origin 本机或缺省）与 ``src/pairing/middleware.py::should_allow``
        （/mcp 只认本机）。
        """
        from src.mcp.server import mcp

        sec = mcp.settings.transport_security
        assert sec is not None, "transport_security 未配置"
        assert sec.enable_dns_rebinding_protection is False, (
            "SDK 的静态 Host 白名单又开了 —— 本机地址一漂移 / 桌面壳 18910 就 421"
        )


class TestGateAndTransportInteraction:
    """工具闸门读的必须是**当下**的 actor（与传输模式无关的那条契约）。"""

    def test_require_domain_reads_current_actor(self):
        """域闸门读的是「当下」的 actor，不是某个缓存值。"""
        import asyncio

        from src.identity import acl as acl_mod
        from src.identity import Actor
        from src.identity.actor import _current, _set_actor
        from src.mcp.common import require_domain

        tokens = []
        try:
            tokens.append(_set_actor(Actor(id="v", kind="key", role_id="visitor")))
            denied = require_domain(acl_mod.DOMAIN_MEETING)
        finally:
            for t in reversed(tokens):
                _current.reset(t)

        assert denied is not None and "error" in denied, "visitor 应被拦"

    def test_owner_passes_without_touching_acl(self):
        """本机回环（owner）不该被自己的闸门拦住 —— 否则本机 Agent 全废。"""
        from src.identity import acl as acl_mod
        from src.identity import Actor
        from src.identity.actor import _current, _set_actor
        from src.mcp.common import require_domain

        tokens = []
        try:
            tokens.append(_set_actor(Actor(id="local", kind="local", role_id="owner")))
            assert require_domain(acl_mod.DOMAIN_MEETING) is None
        finally:
            for t in reversed(tokens):
                _current.reset(t)


# ── 动态身份（2026-09-18）：多盒子同时连时不能混 ──────────────
#
# 用户提出：一个 Agent 同时连多个 SinkDuce 盒子时，**所有盒子的工具名与描述都一样**
# （都有 list_collections 等），模型分不清答案来自谁。而协议里 tools/call 只带工具名、
# 不带盒子标识 —— 所以身份只能从**握手时**注入：serverInfo.name 带上盒子名（主流客户端
# 会拼进工具名，如 mcp__<server>__<tool>），instructions 说清是本地还是远端。


class TestDynamicIdentity:
    def _opts(self, actor=None, box_name="TestBox"):
        """构造一次 create_initialization_options 调用（含身份上下文）。"""
        from src.identity.actor import _current, _set_actor
        from src.pairing import service as pairing_service
        from src.mcp import server as mcp_server

        original_box = pairing_service.get_box_info
        pairing_service.get_box_info = lambda: {"box_name": box_name, "box_id": "b1"}
        tokens = []
        if actor is not None:
            tokens.append(_set_actor(actor))
        try:
            return mcp_server.mcp._mcp_server.create_initialization_options()
        finally:
            for t in reversed(tokens):
                _current.reset(t)
            pairing_service.get_box_info = original_box

    def test_server_name_carries_box_name(self):
        """serverInfo.name 必须带盒子名 —— 客户端靠它把工具名区分开。"""
        from src.identity import Actor

        opts = self._opts(Actor(id="local", kind="local", role_id="owner"))
        assert opts.server_name == "SinkDuce-TestBox", opts.server_name

    def test_two_boxes_produce_different_server_names(self):
        """**核心回归**：两个盒子的 server 名必须不同（否则多盒子必混）。"""
        a = self._opts(box_name="Docker")
        b = self._opts(box_name="JethroDesktop")
        assert a.server_name != b.server_name
        assert a.server_name == "SinkDuce-Docker"
        assert b.server_name == "SinkDuce-JethroDesktop"

    def test_local_instructions_say_local(self):
        from src.identity import Actor

        opts = self._opts(Actor(id="local", kind="local", role_id="owner"))
        low = opts.instructions.lower()
        assert "loopback" in low or "same machine" in low
        assert "remote" not in low.split("## when to use")[0].lower()

    def test_remote_instructions_say_remote_and_not_your_machine(self):
        """**核心回归**：远端连接时原文案写的是 "running locally on their machine"
        —— 那是**假的**，会让模型以为能拿到本机数据。"""
        from src.identity import Actor

        owner_of_remote = Actor(id="k", kind="key", role_id="member")
        opts = self._opts(owner_of_remote, box_name="OfficeBox")
        head = opts.instructions.lower()
        assert "remote" in head
        assert "not" in head and "machine you run on" in head

    def test_instructions_tell_model_to_disambiguate(self):
        """来源标注指引必须在 —— 而且 Hub 让它从「如果你连了多台」变成**默认要求**。

        旧文案写的是「If you are connected to more than one SinkDuce box」：Hub 架构下
        **这一台**就能代表多台，所以那句话的前提已经变了（模型可能以为自己只连了一台，
        于是不标来源）。现在文案直接说「本盒子能触达别的盒子，来自别处就要说明」。
        """
        opts = self._opts(box_name="Any")
        low = opts.instructions.lower()
        assert "which box it came from" in low
        assert "list_boxes" in opts.instructions, "指引要靠 list_boxes 才能落地"

    def test_instructions_route_the_hub_tools(self):
        """**核心回归**：Hub 工具必须进工具路由表。

        instructions 是模型的**首要路由指南**（比 tools/list 更早读到）。三个 Hub 工具
        不进表，模型就不知道「问别的盒子」该调什么 —— 实测这曾是漏项。
        """
        from src.mcp.server import _build_instructions

        t = _build_instructions("Any", True)
        for name in ("list_boxes", "switch_box", "search_all_boxes"):
            assert name in t, f"{name} 没进 instructions"
        # 并且要说清「转发未覆盖的工具仍打本机」——否则模型会以为切了盒子就全切了
        assert "tool_routing" in t or "local_only_tools" in t

    def test_instructions_keep_the_static_body(self):
        """动态头不能把工具路由表那些静态内容弄丢。"""
        opts = self._opts(box_name="Any")
        assert "search_direct_chunks" in opts.instructions
        assert "Route by goal" in opts.instructions

    def test_identity_failure_falls_back_to_static(self, monkeypatch):
        """身份注入失败不能挡住 MCP —— 回落静态值（不抛）。"""
        from src.mcp import server as mcp_server

        def _boom():
            raise RuntimeError("box info down")

        monkeypatch.setattr("src.pairing.service.get_box_info", _boom)
        opts = mcp_server.mcp._mcp_server.create_initialization_options()
        assert opts.server_name  # 仍有值（默认 sinkduce）
        assert opts.instructions
