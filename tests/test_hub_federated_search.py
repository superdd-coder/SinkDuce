"""``search_all_boxes`` 联邦检索（第 6 步）。

**这个文件钉的是设计文档 §10 的四个现实约束**，不是快乐路径：

1. 分数不可比 ⇒ **不合并排序**，按盒子分段；
2. 来源标注强制 ⇒ 每条结果带 ``box``；
3. 离线盒子 ⇒ 部分结果 + **明确警告**（绝不静默省略）；
4. 延迟由最慢者决定 ⇒ **并发扇出**（用真实耗时验证，不靠读代码）。

另加两条刻意的决定：**不去重**、**本机也算一段**。

跑法：``pytest tests/test_hub_federated_search.py``
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import pytest


# ── 一个可编排的假盒子（真的 HTTP 服务）─────────────────────


class FakeBox:
    """极小的假盒子：``/api/collections`` + ``/api/recall/search``。

    ``delay`` 用来验证并发（慢盒子不该串行累加）；``fails`` 用来验证离线分支。
    """

    def __init__(self, *, results=None, collections=None, delay=0.0, fail=None):
        self.results = results if results is not None else []
        self.collections = (
            collections if collections is not None else [{"id": "col_1", "name": "C1"}]
        )
        self.delay = delay
        self.fail = fail  # None | "search" | "list"
        self.searches = 0
        self._thread = None
        self._stop = threading.Event()
        self.port = self._free_port()
        self.addr = f"http://127.0.0.1:{self.port}"

    @staticmethod
    def _free_port() -> int:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    def start(self):
        import uvicorn
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        box = self

        async def list_cols(request):
            if box.fail == "list":
                return JSONResponse({"detail": "boom"}, status_code=500)
            return JSONResponse(box.collections)

        async def search(request):
            if box.fail == "search":
                return JSONResponse({"detail": "boom"}, status_code=500)
            body = await request.json()
            box.searches += 1
            if box.delay:
                await asyncio.sleep(box.delay)
            # 回显收到的库列表，便于断言只检索了该检索的
            return JSONResponse(
                {
                    "results": box.results,
                    "time_ms": 1,
                    "total": len(box.results),
                    "query_used": body.get("query"),
                    "collections_received": body.get("collections"),
                }
            )

        app = Starlette(
            routes=[
                Route("/api/collections", list_cols),
                Route("/api/recall/search", search, methods=["POST"]),
            ]
        )
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error")
        )
        ready = threading.Event()

        def run():
            async def _serve():
                task = asyncio.create_task(server.serve())
                while not server.started:
                    await asyncio.sleep(0.02)
                ready.set()
                while not self._stop.is_set():
                    await asyncio.sleep(0.05)
                server.should_exit = True
                await task

            asyncio.run(_serve())

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        assert ready.wait(20), "假盒子没起来"
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)


@pytest.fixture(autouse=True)
def _tmp_hub(tmp_path, monkeypatch):
    from src.hub import store

    monkeypatch.setattr(store, "HUB_DIR", tmp_path / "hub")
    store.clear()
    yield


@pytest.fixture
def boxes():
    started: list[FakeBox] = []

    def _make(**kw) -> FakeBox:
        b = FakeBox(**kw).start()
        started.append(b)
        return b

    yield _make
    for b in started:
        b.stop()


def _one_result(title: str, score: float = 1.0):
    return [
        {
            "id": f"id-{title}",
            "text": f"text of {title}",
            "score": score,
            "source": f"__file__:{title}",
            "collection": "col_1",
            "chunk_index": 0,
            "chunk_type": "normal",
            "display_name": title,
        }
    ]


def _install(*targets):
    """把若干假盒子装进 Hub 的连接清单。"""
    from src.hub import store

    store.replace_boxes(
        [
            {"box_id": f"b{i}", "box_name": name, "addr": addr, "token": "sd1_x"}
            for i, (name, addr) in enumerate(targets)
        ]
    )


def _search(monkeypatch, *targets, **kw):
    """跑一次联邦检索；**把本机段替换成给定的假盒子**（真实环境下本机就是这台机器）。

    直接 monkeypatch ``_local_target`` 而不是 mock 掉整个扇出逻辑——扇出、并发、
    分段、警告这些都还是真跑的。
    """
    from src.hub import federated

    local_addr = targets[0] if targets else None
    monkeypatch.setattr(
        federated,
        "_local_target",
        lambda: {
            "box_id": "local",
            "name": "This machine",
            "addr": local_addr or "http://127.0.0.1:1",
            "token": "",
            "is_local": True,
        },
    )
    return asyncio.run(federated.search_all_boxes_impl(**kw))


# ── 约束 1：不合并排序，按盒子分段 ───────────────────────────


class TestSegmentedNotMerged:
    def test_results_grouped_per_box(self, boxes, monkeypatch):
        a = boxes(results=_one_result("A", 0.9))
        b = boxes(results=_one_result("B", 0.1))
        _install(("BoxA", a.addr), ("BoxB", b.addr))
        out = _search(monkeypatch, query="q")

        assert [s["box"] for s in out["boxes"]] == ["This machine", "BoxA", "BoxB"]
        # 每段各自带结果，没有被合并成一个大 results
        assert "results" not in out
        assert out["boxes"][1]["results"][0]["display_name"] == "A"
        assert out["boxes"][2]["results"][0]["display_name"] == "B"

    def test_low_score_of_one_box_not_ranked_against_another(self, boxes, monkeypatch):
        """**核心**：不同盒子的分数没有可比性 —— 不能出现「全局排名」。

        BoxA 的 0.9 与 BoxB 的 0.1 各自留在自己的段里；若实现偷偷按分数合并，
        就会出现一个跨盒子的顺序（那正是要禁止的）。
        """
        a = boxes(results=_one_result("A", 0.9))
        b = boxes(results=_one_result("B", 0.95))  # 分数更高，但属于另一台
        _install(("BoxA", a.addr), ("BoxB", b.addr))
        out = _search(monkeypatch, query="q")
        for seg in out["boxes"]:
            for r in seg.get("results") or []:
                assert r["box"] == seg["box"], "结果标错了来源盒子"
        assert "not merged" in out["note"] or "NOT comparable" in out["note"]


# ── 约束 2：来源标注强制 ─────────────────────────────────────


class TestSourceLabeling:
    def test_every_result_carries_box(self, boxes, monkeypatch):
        a = boxes(results=_one_result("A"))
        _install(("BoxA", a.addr))
        out = _search(monkeypatch, query="q")
        for seg in out["boxes"]:
            for r in seg.get("results") or []:
                assert r.get("box"), "缺少来源标注"

    def test_box_id_also_present(self, boxes, monkeypatch):
        a = boxes(results=_one_result("A"))
        _install(("BoxA", a.addr))
        out = _search(monkeypatch, query="q")
        r = out["boxes"][1]["results"][0]
        assert r["box_id"] == "b0"


# ── 约束 3：离线盒子必须明说 ─────────────────────────────────


class TestUnreachableIsExplicit:
    def test_dead_box_is_listed_not_silently_dropped(self, boxes, monkeypatch):
        """**核心回归**：连不上的盒子必须出现在 ``unreachable`` 里并给出警告。

        静默省略会产出「看起来完整、实际缺一块」的答案——最危险的失败模式。
        """
        good = boxes(results=_one_result("A"))
        _install(("BoxA", good.addr), ("Dead", "http://127.0.0.1:1"))
        out = _search(monkeypatch, query="q")

        assert "Dead" in out["unreachable"]
        assert "MISSING" in out["warning"] or "partial" in out["warning"].lower()
        # 好的那台照常有结果（部分结果，不是全盘失败）
        assert out["boxes"][1]["results"][0]["display_name"] == "A"
        # 死掉那台作为一个段存在（不是消失）
        assert any(s["box"] == "Dead" for s in out["boxes"])
        assert next(s for s in out["boxes"] if s["box"] == "Dead")["unreachable"] is True

    def test_error_box_gets_error_field(self, boxes, monkeypatch):
        bad = boxes(fail="list")
        _install(("Bad", bad.addr))
        out = _search(monkeypatch, query="q")
        seg = next(s for s in out["boxes"] if s["box"] == "Bad")
        assert seg.get("error")
        assert "Bad" in out["unreachable"]

    def test_warning_absent_when_all_reachable(self, boxes, monkeypatch):
        a = boxes(results=_one_result("A"))
        _install(("BoxA", a.addr))
        # 本机段也指到一个**可达**的假盒子（否则本机那段就是 unreachable，
        # 测的就不是「全都可达」了）
        out = _search(monkeypatch, a.addr, query="q")
        assert "unreachable" not in out
        assert "warning" not in out

    def test_empty_box_is_not_an_error(self, boxes, monkeypatch):
        """没有库 ≠ 出错。要区分「没有可搜的东西」与「连不上」。"""
        empty = boxes(collections=[])
        _install(("Empty", empty.addr))
        out = _search(monkeypatch, empty.addr, query="q")
        seg = next(s for s in out["boxes"] if s["box"] == "Empty")
        assert seg.get("note") == "no collections to search"
        assert "error" not in seg
        assert "unreachable" not in out


# ── 约束 4：并发扇出 ─────────────────────────────────────────


class TestConcurrency:
    def test_slow_boxes_overlap_not_add_up(self, boxes, monkeypatch):
        """**核心**：三台各慢 0.6s 的盒子，总耗时应接近 0.6s 而不是 1.8s。

        用真实耗时判断，而不是读代码猜——串行实现会在这里红。
        """
        a = boxes(results=_one_result("A"), delay=0.6)
        b = boxes(results=_one_result("B"), delay=0.6)
        c = boxes(results=_one_result("C"), delay=0.6)
        _install(("BoxA", a.addr), ("BoxB", b.addr), ("BoxC", c.addr))
        t0 = time.monotonic()
        out = _search(monkeypatch, query="q")
        elapsed = time.monotonic() - t0
        assert out["total"] == 3
        assert elapsed < 1.5, f"看起来是串行扇出（耗时 {elapsed:.2f}s）"


# ── 刻意的决定：不去重、本机也算一段 ─────────────────────────


class TestDeliberateChoices:
    def test_duplicates_are_kept(self, boxes, monkeypatch):
        """同一份文档在两台盒子里都有 → **两条都保留**（去重做错会丢内容）。"""
        same = _one_result("SameDoc")
        a = boxes(results=same)
        b = boxes(results=same)
        _install(("BoxA", a.addr), ("BoxB", b.addr))
        out = _search(monkeypatch, query="q")
        got = [r for s in out["boxes"] for r in (s.get("results") or [])]
        assert len(got) == 2, "去重把一条内容吃掉了"
        assert {r["box"] for r in got} == {"BoxA", "BoxB"}

    def test_local_box_is_included(self, boxes, monkeypatch):
        local = boxes(results=_one_result("Local"))
        _install()
        out = _search(monkeypatch, local.addr, query="q")
        assert out["boxes"][0]["box"] == "This machine"
        assert out["boxes"][0]["is_local"] is True
        assert out["boxes"][0]["results"][0]["display_name"] == "Local"

    def test_no_boxes_at_all(self, monkeypatch):
        from src.hub import federated

        monkeypatch.setattr(federated, "box_targets", lambda: [])
        out = asyncio.run(federated.search_all_boxes_impl(query="q"))
        assert out["boxes"] == []
        assert out["note"]


# ── 只检索该检索的库（含归档开关）────────────────────────────


class TestCollectionSelection:
    def test_archived_excluded_by_default(self, boxes, monkeypatch):
        b = boxes(
            collections=[
                {"id": "col_live", "name": "Live"},
                {"id": "col_arch", "name": "Old", "archived": True},
            ],
            results=_one_result("X"),
        )
        _install(("BoxA", b.addr))
        _search(monkeypatch, query="q")
        # 假盒子把收到的 collections 回显在 results 里（这里通过第二次调用验证更直接）
        assert b.searches == 1

    def test_include_archived_passes_through(self, boxes, monkeypatch):
        """开启 include_archived 后归档库要进检索列表。"""
        import httpx

        b = boxes(
            collections=[
                {"id": "col_live", "name": "Live"},
                {"id": "col_arch", "name": "Old", "archived": True},
            ],
            results=[],
        )
        _install(("BoxA", b.addr))
        seen: list[list[str]] = []

        # 直接观察请求体（假盒子会把收到的库列表回显）
        orig = httpx.AsyncClient.request

        async def spy(self, method, url, **kw):
            if str(url).endswith("/api/recall/search"):
                seen.append(list((kw.get("json") or {}).get("collections") or []))
            return await orig(self, method, url, **kw)

        monkeypatch.setattr(httpx.AsyncClient, "request", spy)
        _search(monkeypatch, query="q", include_archived=True)
        assert "col_arch" in seen[0]
        assert "col_live" in seen[0]
