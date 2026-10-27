"""批次 1 等价性测试：**转发的结果必须与本地调用一致**。

## 测试方法：让 Hub 指向它自己

转发正确性最难测的地方是"要有个真的远端盒子"。这里用一个巧妙的办法：
**把 Hub 的连接清单里加一条指向自己的记录**，然后

1. 在本机模式下调用工具 → 输出 A；
2. ``switch_box`` 到那条"自己"的记录，同一个调用 → 输出 B（走 REST 转发）；
3. 断言 **A 的形状 == B 的形状**。

因为两个方向上读的是**同一台盒子、同一份数据**，所以任何形状差异都**只能**来自
适配器写错了 —— 这正是要抓的东西（§0.1 的教训：字段少一半、语义不同，都不会报错）。

## 两个层次

- ``TestAdapters``：**纯函数**，把真实 REST 响应喂进适配器，断言输出形状。
  快、无需服务，钉住整形逻辑本身。
- ``TestLiveEquivalence``：**真机**（需要 18900 在跑），走上面那个"指向自己"的办法。
  没有服务时自动跳过，不制造假绿。

跑法：``pytest tests/test_hub_forward_equivalence.py``
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

BASE = "http://127.0.0.1:18900"

#: 批次 1 已接转发的工具 → (调用参数, 期望输出里的关键键)
WIRED: list[tuple[str, dict]] = [
    ("list_folders", {"collection": "@col"}),
    ("list_groups", {"collection": "@col"}),
    ("list_todos", {"collection": "@col", "include_done": True}),
    ("list_notes", {"collection": "@col"}),
    ("get_note", {"collection": "@col", "note_id": "@note"}),
    ("get_collection_summary", {"collection": "@col"}),
    ("get_conflicts", {"collection": "@col"}),
    ("list_hot_words_libraries", {}),
    ("get_hot_words_library", {"library_id": "@lib"}),
    ("list_tasks", {"limit": 5}),
    ("get_query_history", {"limit": 3, "include_details": False}),
]


# ── 纯函数层：适配器 ─────────────────────────────────────────


class TestAdapters:
    def test_envelope_wraps_bare_list(self):
        from src.hub.adapters import envelope, total_of

        shape = envelope("folders", extra=total_of("folders"))
        out = shape([{"name": "A"}, {"name": "B"}], {"collection": "col_x"})
        assert out["folders"] == [{"name": "A"}, {"name": "B"}]
        assert out["total"] == 2
        # 工具的输出带 collection（本地实现如此）→ 适配器必须补上
        assert out["collection"] == "col_x"

    def test_envelope_passes_through_existing_envelope(self):
        """REST 已经带信封时（如 /notes 返回 {collection, notes}）不能包两层。"""
        from src.hub.adapters import envelope

        shape = envelope("notes")
        out = shape({"collection": "c", "notes": [{"id": "n"}]}, {"collection": "c"})
        assert out["notes"] == [{"id": "n"}]
        assert "notes" in out and isinstance(out["notes"], list)

    def test_envelope_handles_non_list(self):
        from src.hub.adapters import envelope

        out = envelope("groups")({"detail": "boom"}, {"collection": "c"})
        # 不是列表就包成单项，绝不抛（错误响应也要能通过）
        assert isinstance(out, dict)

    def test_todos_query_include_done_means_no_filter(self):
        """``include_done=True`` → **不带 done 参数**（不过滤）。

        这条是"参数被静默忽略"的防线：早期透传写法会让 REST 用默认
        （只看未完成），于是"包含已完成"的需求静默失效。
        """
        from src.mcp.tools.file_mgmt import _todos_query

        assert _todos_query({"include_done": True}) == {"done": None, "mine": None} or \
            _todos_query({"include_done": True}).get("done") is None
        # 显式 done 优先
        assert _todos_query({"done": True, "include_done": False})["done"] is True
        # 默认只看未完成
        assert _todos_query({})["done"] is False
        # mine 透传
        assert _todos_query({"mine": True})["mine"] is True

    def test_todos_query_drops_none_values(self):
        """None 不该被拼进 URL（client 层也挡一道，这里保证语义）。"""
        from src.mcp.tools.file_mgmt import _todos_query

        q = _todos_query({})
        assert q["mine"] is None  # client 会丢掉 None


# ── 真机层：本地 vs 转发 ─────────────────────────────────────


def _has_service() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


pytestmark_live = pytest.mark.skipif(
    not _has_service(), reason="需要 18900 在跑（真机等价性测试）"
)


class _Mcp:
    """一条 MCP 会话（复用：switch_box 是会话级状态）。"""

    def __init__(self):
        import httpx

        self._c = httpx.Client(timeout=90)
        r = self._c.post(f"{BASE}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                   "clientInfo": {"name": "equiv", "version": "0"}}},
                        headers={"Accept": "application/json, text/event-stream"})
        self.sid = r.headers.get("mcp-session-id", "")
        self._c.post(f"{BASE}/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                     headers=self._h())

    def _h(self):
        return {"Accept": "application/json, text/event-stream", "mcp-session-id": self.sid}

    def call(self, name: str, args: dict) -> dict:
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


def _rest(path: str, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "ignore")
            return json.loads(raw) if raw.strip() else None
    except Exception:
        return None


def _shape_of(obj) -> set:
    """取**字段名**集合（忽略嵌套层级）——比对形状用。"""
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            out |= _shape_of(v)
    elif isinstance(obj, list) and obj:
        out |= _shape_of(obj[0])
    return out


@pytest.fixture
def self_hub():
    """把一条"指向自己"的记录塞进 Hub 清单，测完**恢复原样**。

    ⚠️ **必须走被探测盒子的 REST 端点写**，不能用 ``src.hub.store``：
    测试进程与"被测的那台盒子"（这里是 18900 容器）是**两个进程、两份
    ``boxes.json``**。用 store 写只会改测试进程的文件，容器完全看不到 →
    ``switch_box`` 报 ``unknown_box``，测试变成"测了个不存在的东西"。
    实测踩过（症状：转发调用返回空/被守卫拒，其实清单根本没写进去）。
    """
    original = _rest("/api/hub/boxes") or {}
    original = original.get("boxes") or []

    import json as _json
    import urllib.request as _u

    def _put(boxes):
        body = _json.dumps({"boxes": boxes}).encode()
        req = _u.Request(f"{BASE}/api/hub/boxes", data=body, method="PUT",
                         headers={"Content-Type": "application/json"})
        with _u.urlopen(req, timeout=20) as r:
            r.read()

    _put([
        *[{"box_id": b["box_id"], "box_name": b["box_name"], "addr": b["addr"],
           "last_checked": b.get("last_checked", "")} for b in original],
        {"box_id": "__equiv_self__", "box_name": "EquivSelf", "addr": BASE, "token": ""},
    ])
    yield
    # 恢复：去掉测试条目，把原条目放回（token 传空 = 保留后端已存的钥匙）
    _put([{"box_id": b["box_id"], "box_name": b["box_name"], "addr": b["addr"],
           "last_checked": b.get("last_checked", "")} for b in original])


@pytest.fixture
def ids():
    """真实靶子 id（collection / note / hot-words library）。"""
    cols = _rest("/api/collections") or []
    col = cols[0]["id"] if cols else None
    notes = _rest(f"/api/notes/{col}") if col else None
    note = ((notes or {}).get("notes") or [{}])[0].get("id") if notes else None
    libs = _rest("/api/hot-words") or []
    lib = libs[0]["id"] if libs else None
    return {"col": col, "note": note, "lib": lib}


@pytestmark_live
class TestLiveEquivalence:
    """本地 vs 转发：**同一台盒子**，输出形状必须一致。"""

    @pytest.fixture(autouse=True)
    def _ids(self, ids):
        self.col, self.note, self.lib = ids["col"], ids["note"], ids["lib"]

    def _resolve(self, args: dict) -> dict:
        out = {}
        for k, v in args.items():
            if v == "@col":
                out[k] = self.col
            elif v == "@note":
                out[k] = self.note
            elif v == "@lib":
                out[k] = self.lib
            else:
                out[k] = v
        return out

    def _cases(self):
        for name, raw in WIRED:
            args = self._resolve(raw)
            if any(v is None for v in args.values()):
                continue  # 缺 id 的靶子，跳过（不算失败）
            yield name, args

    def test_every_wired_tool_matches_local(self, self_hub):
        """**核心回归**：逐个工具比「本机输出」与「经 Hub 转发输出」的字段集。"""
        s = _Mcp()
        mismatches = []
        checked = 0
        for name, args in self._cases():
            s.call("switch_box", {"name": "local"})
            local = s.call(name, args)
            sw = s.call("switch_box", {"name": "EquivSelf"})
            if not sw.get("reachable"):
                pytest.skip(f"无法切到测试用的 self hub：{sw}")
            forwarded = s.call(name, args)
            checked += 1
            lk, fk = _shape_of(local), _shape_of(forwarded)
            # 转发结果**不该缺**本地有的字段（多出来是良性超集）
            missing = sorted(lk - fk)
            if missing:
                mismatches.append((name, f"转发少了字段: {missing}",
                                   json.dumps(forwarded, ensure_ascii=False)[:120]))
        s.call("switch_box", {"name": "local"})
        assert checked >= 5, f"只跑通了 {checked} 个工具，测试没有说服力"
        assert mismatches == [], f"转发形状与本地不一致：{mismatches}"

    def test_forwarded_call_actually_hits_rest(self, self_hub, monkeypatch):
        """**证明它真的走了转发**，而不是碰巧两边都跑本地实现。

        办法：让远端调用**失败**（把 addr 改成一个连不上的端口），
        转发路径应当报连接错误；若仍返回数据，说明它偷偷跑了本地实现。
        """
        import json as _json
        import urllib.request as _u

        boxes = _rest("/api/hub/boxes").get("boxes") or []
        for b in boxes:
            if b["box_id"] == "__equiv_self__":
                b["addr"] = "http://127.0.0.1:1"  # 无人监听
        body = _json.dumps({"boxes": boxes}).encode()
        req = _u.Request(f"{BASE}/api/hub/boxes", data=body, method="PUT",
                         headers={"Content-Type": "application/json"})
        with _u.urlopen(req, timeout=20) as r:
            r.read()

        s = _Mcp()
        s.call("switch_box", {"name": "EquivSelf"})
        out = s.call("list_notes", {"collection": self.col})
        s.call("switch_box", {"name": "local"})
        blob = json.dumps(out, ensure_ascii=False)
        assert "cannot reach" in blob or "connect" in blob.lower(), (
            "远端不可达时竟然返回了数据 —— 说明没真走转发：" + blob[:160]
        )

    def test_forwarded_folders_hides_unassigned(self, self_hub):
        """**语义等价**：转发的 ``list_folders`` 也必须隐藏「未分配」。

        这是 §0.1 记的那个陷阱：REST 默认 ``include_unassigned=True``。
        若适配器忘了带 False，跨盒子会看到本地看不到的文件夹 —— **不报错**。
        """
        s = _Mcp()
        s.call("switch_box", {"name": "local"})
        local = s.call("list_folders", {"collection": self.col})
        s.call("switch_box", {"name": "EquivSelf"})
        forwarded = s.call("list_folders", {"collection": self.col})
        s.call("switch_box", {"name": "local"})

        def names(p):
            return sorted(
                f.get("name", "") for f in (p.get("folders") or [])
            )

        assert names(forwarded) == names(local)
        assert "Unassigned" not in names(forwarded), "未分配被转发泄露出来了"

    def test_forwarded_todos_mine_is_honoured(self, self_hub):
        """``mine=true`` 必须真的按"我的"过滤（不能静默返回全部）。"""
        s = _Mcp()
        s.call("switch_box", {"name": "local"})
        local = s.call("list_todos", {"collection": self.col, "mine": True})
        s.call("switch_box", {"name": "EquivSelf"})
        forwarded = s.call("list_todos", {"collection": self.col, "mine": True})
        s.call("switch_box", {"name": "local"})
        # 本地与转发必须一致（包括"都是错误：Me 未设置"这种情况）
        assert _shape_of(forwarded) == _shape_of(local), (local, forwarded)


class TestParamGuard:
    """参数守卫：**无法忠实转发**的参数必须让转发失败，而不是被静默丢掉。

    为什么这条重要：参数被静默忽略会产出「看起来对、其实错」的结果
    （``list_todos(mine=True)`` 返回全部待办就是实例），而调用方**没有任何线索**。

    ⚠️ 早先这里写了个"传一个签名里没有的参数"的 live 测试 —— **测不到东西**：
    那种参数在 FastMCP 的 schema 校验阶段就被剥掉了，根本到不了守卫。
    改成下面两条：一条测守卫本身，一条**静态**验证每个已接路由都声明全了参数
    （后者才是真正的防线：新加工具参数却忘了 declare 时它会红）。
    """

    def test_leftovers_refused(self):
        from src.hub.routing import RefuseForward, _check_leftovers

        # 声明了的 → 放行
        _check_leftovers("t", {"collection": "c"}, ("collection",), ())
        # 没声明的 → 拒绝（fail-closed）
        with pytest.raises(RefuseForward) as ei:
            _check_leftovers("t", {"collection": "c", "surprise": 1}, ("collection",), ())
        assert "surprise" in str(ei.value)
        assert ei.value.code == "not_forwardable"
        # ignore 里显式声明的 → 放行（有意不转发）
        _check_leftovers("t", {"collection": "c", "local_only_knob": 1}, ("collection",),
                         ("local_only_knob",))

    def test_none_values_do_not_trip_the_guard(self):
        """``None`` 等于"没传"：不能在只传了 collection 时报"你漏了 xxx"。"""
        from src.hub.routing import _check_leftovers

        _check_leftovers("t", {"collection": "c", "mine": None}, ("collection",), ())

    def test_every_wired_route_declares_all_its_params(self):
        """**核心防线**：每个已接路由的 ``forwards + ignore`` 必须覆盖工具**全部**参数。

        这条把"给工具新增参数、却忘了加进路由声明"变成一个**测试失败** ——
        否则那个新参数会在跨盒子时被静默忽略（正是要防的静默陷阱）。
        """
        import inspect

        from src.hub.routing import ROUTES, _ctx_param_name
        from src.mcp.server import mcp

        registered = {t.name: t for t in mcp._tool_manager.list_tools()}
        problems = []
        for name, route in ROUTES.items():
            entry = registered[name]
            fn = entry.fn if hasattr(entry, "fn") else entry
            ctx = _ctx_param_name(fn)
            params = {
                p.name
                for p in inspect.signature(fn, eval_str=True).parameters.values()
                if p.name != ctx and p.name != "self"
            }
            declared = set(route.get("forwards") or ()) | set(route.get("ignore") or ())
            missing = sorted(params - declared)
            if missing:
                problems.append(f"{name}: 未声明 {missing}（会被静默忽略）")
        assert problems == [], "这些路由的参数声明不全：\n  " + "\n  ".join(problems)
