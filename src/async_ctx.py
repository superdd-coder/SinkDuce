"""在线程池里保留请求上下文（`contextvars`）的辅助函数。

**为什么需要它**：``loop.run_in_executor()`` **不复制 `contextvars`**。而身份
（``src/identity/actor.py`` 的 ``Actor``）正是存在 contextvar 里的，于是被丢进
线程池的函数会读到**默认值 = owner**。

后果不是"少了个字段"，而是**授权静默全开**：``may_see_everything(owner)`` 为真，
可见性过滤直接返回"全部库"，隐藏的 collection 于是能被检索出来。

2026-09-17 实测（本机复现）：

    async def main():
        set_actor(Actor(id="k", kind="key", role_id="member"))
        loop = asyncio.get_event_loop()
        print(await loop.run_in_executor(None, lambda: get_actor().role_id))
        # → "owner"   ← 身份丢失，可见性过滤整个失效
        print(await asyncio.to_thread(lambda: get_actor().role_id))
        # → "member"  ← to_thread 会复制上下文

**规则**：任何"会在里面读 `get_actor()` 做授权/可见性判断"的阻塞函数，必须用
``run_with_context()`` 而不是裸 ``run_in_executor``；**要起线程池时用
``ContextThreadPoolExecutor`` 而不是裸 ``ThreadPoolExecutor``**。纯计算 / 纯 IO、
不碰身份的地方用哪个都一样（本文件不强制全量替换）。

**⚠️ 线程池是嵌套的，每一层都要管**（2026-09-17 第二次踩到）：检索链上
agentic → 每 AQ → 每 variant → 每 collection 各起一层池，**只修最外层等于没修**。
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, TypeVar

T = TypeVar("T")


class ContextThreadPoolExecutor(ThreadPoolExecutor):
    """``ThreadPoolExecutor`` 的**上下文保留**版本。

    裸 ``ThreadPoolExecutor.submit`` 和 ``loop.run_in_executor`` 一样**不复制
    `contextvars`**，所以工作线程里 ``get_actor()`` 读到的是默认值 = **owner**。
    在检索路径上这意味着 ``may_see_everything`` 为真 → 可见性过滤**整个关掉**。

    **为什么必须要它**（2026-09-17 用户第二次报"隐藏库还能搜出来"）：修好最外层
    `run_in_executor` 之后**仍然漏**，因为没有身份的线程池是**嵌套**的——

        agentic.run（外层 executor：已修）
          └─ ThreadPoolExecutor 每 atomic-query   ← variant_fetcher / agentic_query
               └─ ThreadPoolExecutor 每个 variant ← variant_fetcher
                    └─ ThreadPoolExecutor 每个 collection ← direct_query
                         └─ Retriever.retrieve → is_collection_visible(get_actor())

    任何一层用裸池，身份就在那一层断掉、过滤就没了。所以**凡是在检索链上起线程池
    的地方，一律用本类**。

    **每次 submit 单独 ``copy_context()``**：一个 Context 不能被并发进入
    （``RuntimeError: cannot enter context``），共享一份会让并发 submit 直接炸。
    """

    def submit(  # type: ignore[override]
        self,
        fn: Callable[..., T],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Future[T]:
        ctx = contextvars.copy_context()
        return super().submit(ctx.run, fn, *args, **kwargs)


async def run_with_context(
    fn: Callable[..., T],
    /,
    *args: Any,
    executor: Any = None,
    **kwargs: Any,
) -> T:
    """在默认（或指定）线程池里执行 ``fn``，**保留调用方的 `contextvars`**。

    用法与 ``loop.run_in_executor(None, fn, *args)`` 一致，只是身份等上下文不会丢：

        result = await run_with_context(_run)                 # 无参
        result = await run_with_context(parse_file, path)     # 带位置参

    ``executor`` 是**具名**参数（在 ``*args`` 之后），不会被误当成 ``fn`` 的位置参数。
    ``functools.partial`` 而非 ``ctx.run(fn, *a, **kw)``：后者对关键字参数的支持随
    版本而异，partial 在所有版本行为一致。
    """
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    call = functools.partial(ctx.run, fn, *args, **kwargs)
    return await loop.run_in_executor(executor, call)
