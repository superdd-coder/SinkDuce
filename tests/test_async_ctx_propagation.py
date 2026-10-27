"""线程池里的身份传播（2026-09-17 泄露根因）。

**用户报的 bug**：member 的 Chat 能搜出它没权限的 collection。

**根因（本机实测）**：``loop.run_in_executor()`` **不复制 `contextvars`**，而
``Actor`` 就存在 contextvar 里 → 线程里 ``get_actor()`` 读到**默认 owner** →
``may_see_everything`` 为真 → ``visible_collection_ids`` 返回 ``None``（= 全部）
→ **可见性过滤整个失效**，隐藏库照常被检索。

``asyncio.to_thread`` 会复制上下文，裸 ``run_in_executor`` 不会——这个差异很隐蔽，
所以用测试钉住：任何"在线程里读身份"的路径都必须走 `run_with_context`。

跑法：``pytest tests/test_async_ctx_propagation.py``
"""

from __future__ import annotations

import asyncio

import pytest

from src.async_ctx import run_with_context
from src.identity.actor import Actor, get_actor, set_actor, _reset_actor_token

MEMBER = Actor(id="k-member", kind="key", role_id="member")
OWNER = Actor()


@pytest.fixture
def as_member():
    token = set_actor(MEMBER)
    yield
    _reset_actor_token(token)


class TestHelperPreservesContext:
    def test_run_with_context_keeps_identity(self, as_member):
        async def main():
            return await run_with_context(lambda: get_actor().role_id)

        assert asyncio.run(main()) == "member"

    def test_plain_executor_loses_identity(self, as_member):
        """**反例**：钉住"为什么需要这个 helper"——裸 executor 会丢身份。

        这条测试红了反而是好事信号：说明该行为变了，helper 可以简化。
        """

        async def main():
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, lambda: get_actor().role_id)

        assert asyncio.run(main()) == "owner", "裸 run_in_executor 应当丢身份（这是根因）"

    def test_helper_passes_args_and_kwargs(self, as_member):
        def work(name, *, times=1):
            return f"{get_actor().role_id}:{name}" * times

        async def main():
            return await run_with_context(work, "x", times=2)

        assert asyncio.run(main()) == "member:xmember:x"

    def test_helper_propagates_exceptions(self, as_member):
        def boom():
            raise ValueError("inner")

        async def main():
            return await run_with_context(boom)

        with pytest.raises(ValueError, match="inner"):
            asyncio.run(main())


class TestVisibilityInThread:
    """真正要保住的是**可见性判定**，不只是角色字符串。"""

    def test_visible_set_is_not_none_in_thread(self, as_member, monkeypatch):
        from src.identity import visibility

        monkeypatch.setattr(visibility, "_all_collection_ids", lambda: ["col_a", "col_hidden"])

        def allows(cid, tier, role):
            return cid != "col_hidden"

        from src.identity import acl as acl_mod

        monkeypatch.setattr(acl_mod, "collection_allows", allows)

        async def main():
            def work():
                vis = visibility.visible_collection_ids(get_actor())
                # None = "全部"（owner）；这里必须是**收窄后的集合**
                return None if vis is None else sorted(vis)

            return await run_with_context(work)

        assert asyncio.run(main()) == ["col_a"], "线程里必须仍按 member 收窄"
