"""线程池里的身份传播（2026-09-17 第二次修复）。

**用户第二次报**：「无法读取的 collection 仍然还是可以在 Chat 里面找出来」。

**为什么上次修完还漏**：身份线程池是**嵌套**的，上次只修了最外层
`run_in_executor`。检索链实际是这样：

    agentic.run（外层 executor —— 上次已修）
      └─ ThreadPoolExecutor 每个 atomic-query      ← agentic_query.py
           └─ ThreadPoolExecutor 每个 query-variant ← variant_fetcher.py
                └─ ThreadPoolExecutor 每个 collection ← direct_query.py
                     └─ Retriever.retrieve → is_collection_visible(get_actor())

**任何一层用裸池，身份就在那一层断成默认 owner**，`may_see_everything` 为真
→ 可见性过滤整个关掉。所以三层都必须用 `ContextThreadPoolExecutor`。

本文件把"裸池丢身份"这件事本身钉住（反例），并验证新池保得住。
"""

from __future__ import annotations

import asyncio

import pytest

from src.async_ctx import ContextThreadPoolExecutor, run_with_context
from src.identity.actor import Actor, _reset_actor_token, get_actor, set_actor

MEMBER = Actor(id="k-member", kind="key", role_id="member")
OWNER = Actor()


@pytest.fixture
def as_member():
    token = set_actor(MEMBER)
    yield
    _reset_actor_token(token)


class TestContextThreadPoolExecutor:
    def test_preserves_identity(self, as_member):
        with ContextThreadPoolExecutor(max_workers=2) as ex:
            role = ex.submit(lambda: get_actor().role_id).result(timeout=5)
        assert role == "member"

    def test_plain_pool_loses_identity(self, as_member):
        """**反例**：钉住"为什么需要这个类"。这条红了是好信号（说明行为变了）。"""
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=2) as ex:
            role = ex.submit(lambda: get_actor().role_id).result(timeout=5)
        assert role == "owner", "裸 ThreadPoolExecutor 应当丢身份（这是根因）"

    def test_many_concurrent_submits_do_not_crash(self, as_member):
        """每个 submit 要**各自** copy_context。

        共享一份 Context 会被并发进入，直接 ``RuntimeError: cannot enter context``
        —— 那会让整条检索链在生产里偶发炸掉。
        """
        with ContextThreadPoolExecutor(max_workers=8) as ex:
            futs = [ex.submit(lambda: get_actor().role_id) for _ in range(40)]
            roles = [f.result(timeout=10) for f in futs]
        assert set(roles) == {"member"}, f"并发下身份应当稳定，实得 {set(roles)}"

    def test_passes_args_and_kwargs(self, as_member):
        with ContextThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(lambda a, *, b: f"{get_actor().role_id}:{a + b}", 1, b=2)
            assert fut.result(timeout=5) == "member:3"

    def test_exceptions_propagate(self, as_member):
        def boom():
            raise ValueError("inner")

        with ContextThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(boom)
            with pytest.raises(ValueError, match="inner"):
                fut.result(timeout=5)

    def test_map_also_preserves_identity(self, as_member):
        with ContextThreadPoolExecutor(max_workers=2) as ex:
            roles = list(ex.map(lambda _: get_actor().role_id, range(4)))
        assert set(roles) == {"member"}


class TestNestedPools:
    """真实检索链是**多层嵌套**的；这层是上次漏掉的形状。"""

    def test_two_levels_nested_keep_identity(self, as_member):
        def inner():
            return get_actor().role_id

        def outer():
            with ContextThreadPoolExecutor(max_workers=2) as ex2:
                return list(ex2.map(lambda _: inner(), range(3)))

        with ContextThreadPoolExecutor(max_workers=2) as ex1:
            results = ex1.submit(outer).result(timeout=10)
        assert set(results) == {"member"}

    def test_three_levels_nested_keep_identity(self, as_member):
        """agentic → variant → collection 就是三层。"""

        def level3():
            return get_actor().role_id

        def level2():
            with ContextThreadPoolExecutor(max_workers=2) as ex:
                return list(ex.map(lambda _: level3(), range(2)))

        def level1():
            with ContextThreadPoolExecutor(max_workers=2) as ex:
                return list(ex.map(lambda _: level2(), range(2)))

        with ContextThreadPoolExecutor(max_workers=2) as ex:
            out = ex.submit(level1).result(timeout=15)
        flat = [r for sub in out for r in sub]
        assert set(flat) == {"member"}, f"三层嵌套后身份必须还在，实得 {set(flat)}"

    def test_one_plain_level_breaks_the_chain(self, as_member):
        """**中间任何一层**用裸池都会断——这正是上次"修了还漏"的原因。"""
        from concurrent.futures import ThreadPoolExecutor

        def inner():
            return get_actor().role_id

        def middle():
            with ThreadPoolExecutor(max_workers=1) as bad:  # 裸池
                return bad.submit(inner).result(timeout=5)

        with ContextThreadPoolExecutor(max_workers=1) as ex:
            got = ex.submit(middle).result(timeout=10)
        assert got == "owner", "中间层裸池应当把身份断掉（复现用户第二次报的现象）"


class TestRetrievalChainUsesContextPools:
    """把"三层都必须用 ContextThreadPoolExecutor"钉住。

    行为测试要跑起完整检索链才生效（贵且依赖多），而这里要防的是**回归**
    （有人把某层改回裸池）。所以直接查源码——**但要查实际的 `with ... as executor`
    语句，不能只查类名是否出现**：`import` 那行也含类名，只查出现会让"改回裸池但
    留着 import"的变异逃过检测（第一次写就是这么漏的，变异校验抓出来了）。
    """

    def _src(self, name: str) -> str:
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "src" / "rag"
        return (root / name).read_text(encoding="utf-8")

    def _assert_uses_context_pool(self, name: str):
        import re

        s = self._src(name)
        # 真正用它起池子（with X(...) as ...）
        used = re.search(r"with\s+ContextThreadPoolExecutor\s*\(", s)
        assert used, f"{name} 的扇出必须用 ContextThreadPoolExecutor 起池"
        # 而且不能再有裸池的 with 语句（防"两处并存、走的是旧的"）
        plain = re.search(r"with\s+ThreadPoolExecutor\s*\(", s)
        assert not plain, f"{name} 里不该再有裸 ThreadPoolExecutor 的 with 语句"

    def test_direct_query_collection_fanout(self):
        self._assert_uses_context_pool("direct_query.py")

    def test_variant_fetcher_query_fanout(self):
        self._assert_uses_context_pool("variant_fetcher.py")

    def test_agentic_query_aq_fanout(self):
        self._assert_uses_context_pool("agentic_query.py")
