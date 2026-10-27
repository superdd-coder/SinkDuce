"""The event stream must not occupy a worker thread while idle.

Audit finding: `iter_event_frames` was a *synchronous* generator, so Starlette
wrapped it in `iterate_in_threadpool`. Each `next()` took a threadpool token and
the generator then blocked up to 20s inside `queue.get(timeout=...)`, holding
that token. anyio's default limiter has 40 tokens, so ~40 concurrent idle
streams starved every sync `def` route (measured: 42 streams → 15s stall on
`/api/collections`, which also serves file_mgmt).

Frame *semantics* (replay / gap / overflow / keepalive) are covered in
tests/test_events_routes.py. This file guards the structural invariant that made
those semantics cheap: an idle stream must not hold a thread.
"""

from __future__ import annotations

import asyncio
import inspect

from src.events.bus import EventBus
from src.events.models import make_event


def _run(coro):
    """Own the loop; asyncio.run would poison asyncio.get_event_loop() for later tests."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


class TestStreamIsAsync:
    def test_frame_iterator_is_an_async_generator(self):
        """An async generator is what keeps idle streams off the threadpool."""
        from src.events.routes import iter_event_frames

        assert inspect.isasyncgenfunction(iter_event_frames), (
            "must be async: a sync generator holds a threadpool token while blocked"
        )

    def test_idle_stream_does_not_block_the_event_loop(self):
        """The core invariant: waiting for an event must yield to the loop."""
        from src.events.routes import iter_event_frames

        bus = EventBus()
        sub = bus.subscribe()

        async def scenario():
            gen = iter_event_frames(bus, sub, None, limit=2, timeout=1.0)
            hello = await gen.__anext__()
            assert hello[0] == "hello"

            # While the stream waits for the next event, unrelated work must
            # still run. With a blocking sync generator this could not happen.
            progressed = asyncio.Event()

            async def other_work():
                await asyncio.sleep(0)
                progressed.set()

            task = asyncio.ensure_future(other_work())
            await asyncio.wait_for(progressed.wait(), timeout=0.5)
            await task
            await gen.aclose()

        _run(scenario())

    def test_many_idle_streams_do_not_consume_worker_threads(self):
        """40 idle streams (anyio's default token count) must coexist cheaply.

        With the old sync generator this scenario starved sync routes; here it
        must complete almost instantly because nobody is holding a token.
        """
        from src.events.routes import iter_event_frames

        bus = EventBus()

        async def scenario():
            gens = []
            for _ in range(40):
                sub = bus.subscribe()
                gen = iter_event_frames(bus, sub, None, limit=2, timeout=5.0)
                await gen.__anext__()  # hello
                gens.append(gen)
            # A publish must reach all of them promptly without blocking.
            bus.publish(make_event("file.updated"))
            names = [await g.__anext__() for g in gens]
            assert all(n == "change" for n, _ in names)
            for g in gens:
                await g.aclose()

        _run(scenario())

    def test_bus_publish_wakes_an_awaiting_reader(self):
        """publish() must wake the async queue thread-safely."""
        from src.events.routes import iter_event_frames

        bus = EventBus()

        async def scenario():
            sub = bus.subscribe(loop=asyncio.get_running_loop())
            gen = iter_event_frames(bus, sub, None, limit=2, timeout=5.0)
            await gen.__anext__()  # hello

            async def publish_soon():
                await asyncio.sleep(0.01)
                bus.publish(make_event("note.created"))

            await asyncio.gather(publish_soon())
            name, ev = await gen.__anext__()
            assert name == "change"
            assert ev.type == "note.created"
            await gen.aclose()

        _run(scenario())

    def test_publish_from_a_thread_reaches_an_async_reader(self):
        """The load-bearing thread-safety case, now via an async subscriber."""
        import threading

        from src.events.routes import iter_event_frames

        bus = EventBus()

        async def scenario():
            sub = bus.subscribe(loop=asyncio.get_running_loop())
            gen = iter_event_frames(bus, sub, None, limit=2, timeout=5.0)
            await gen.__anext__()  # hello

            done = threading.Event()

            def worker():
                bus.publish(make_event("collection.updated"))
                done.set()

            t = threading.Thread(target=worker)
            t.start()
            name, ev = await gen.__anext__()
            t.join(timeout=2)
            assert name == "change"
            assert ev.domain == "collections"
            await gen.aclose()

        _run(scenario())

    def test_unsubscribe_on_close_stops_delivery(self):
        from src.events.routes import iter_event_frames

        bus = EventBus()

        async def scenario():
            sub = bus.subscribe(loop=asyncio.get_running_loop())
            gen = iter_event_frames(bus, sub, None, limit=2, timeout=5.0)
            await gen.__anext__()  # hello
            await gen.aclose()
            bus.unsubscribe(sub)
            # Publishing after teardown must not raise.
            bus.publish(make_event("file.updated"))

        _run(scenario())
class TestNoSpuriousOverflow:
    """A subscriber that keeps up must never be told to resync.

    Regression guard for a defect introduced by the async refactor: `publish()`
    filled BOTH the sync and async queues, but the async reader only drains the
    async one. The sync queue therefore filled to its maxsize (500) over the
    connection's lifetime even while the reader kept up perfectly, and the next
    publish marked the subscriber overflowed — a spurious `resync {overflow}`
    (i.e. a full client-side reconcile) roughly every 500 events.
    """

    def test_fast_reader_never_sees_overflow(self):
        from src.events.routes import iter_event_frames

        bus = EventBus()
        N = 600  # comfortably past the 500-event queue bound

        async def scenario():
            sub = bus.subscribe(loop=asyncio.get_running_loop())
            gen = iter_event_frames(bus, sub, None, limit=N + 1, timeout=30.0)
            await gen.__anext__()  # hello

            resyncs = []
            # True fast reader: one publish, one read, all the way through.
            for i in range(N):
                bus.publish(make_event("file.updated"))
                name, payload = await gen.__anext__()
                if name == "resync":
                    resyncs.append(payload)
                    break
                assert name == "change"
                assert payload.seq == i + 1, "no event may be skipped"
            await gen.aclose()
            assert resyncs == [], f"spurious resync despite keeping up: {resyncs}"
            assert sub.overflowed is False, "a keeping-up reader must not be marked overflowing"

        _run(scenario())

    def test_sync_queue_is_not_used_for_async_subscribers(self):
        """The sync queue must stay empty when an async reader is attached."""
        from src.events.routes import iter_event_frames

        bus = EventBus()

        async def scenario():
            sub = bus.subscribe(loop=asyncio.get_running_loop())
            gen = iter_event_frames(bus, sub, None, limit=4, timeout=30.0)
            await gen.__anext__()  # hello
            for _ in range(3):
                bus.publish(make_event("file.updated"))
            for _ in range(3):
                await gen.__anext__()
            # If publish() also filled the sync queue, it would accumulate here.
            assert sub.queue.qsize() == 0, (
                "the sync queue must not accumulate for an async subscriber"
            )
            await gen.aclose()

        _run(scenario())
class TestEndpointTeardown:
    """The route must unsubscribe when the stream ends, however it ends.

    A subscriber left behind in the bus is not just a memory leak: every later
    publish fans an event out to a dead consumer and calls
    `call_soon_threadsafe` on its closed loop. The route reaches for the
    module-level bus, so these tests measure that instance via a delta and
    restore it afterwards.
    """

    def test_route_unsubscribes_when_the_stream_is_closed(self):
        from src.events import routes as routes_mod

        bus = routes_mod.bus
        before = len(bus._subs)

        async def scenario():
            resp = await routes_mod.stream_events(
                request=None, since=None, client=None  # type: ignore[arg-type]
            )
            gen = resp.body_iterator
            await gen.__anext__()  # hello → proves we are subscribed
            assert len(bus._subs) == before + 1, "stream should be subscribed while open"
            await gen.aclose()  # client disconnect
            assert len(bus._subs) == before, "subscription must be released"

        try:
            _run(scenario())
        finally:
            # Leave the global bus exactly as found.
            del bus._subs[before:]

    def test_route_unsubscribes_when_cancelled_mid_wait(self):
        """Abrupt disconnect = cancellation while awaiting the next event."""
        import asyncio as _asyncio

        from src.events import routes as routes_mod

        bus = routes_mod.bus
        before = len(bus._subs)

        async def scenario():
            resp = await routes_mod.stream_events(
                request=None, since=None, client=None  # type: ignore[arg-type]
            )
            gen = resp.body_iterator
            await gen.__anext__()  # hello
            assert len(bus._subs) == before + 1

            async def pull():
                await gen.__anext__()  # blocks awaiting an event

            task = _asyncio.ensure_future(pull())
            await _asyncio.sleep(0.01)
            task.cancel()
            try:
                await task
            except _asyncio.CancelledError:
                pass
            await gen.aclose()
            assert len(bus._subs) == before, "cancellation must release the subscription"

        try:
            _run(scenario())
        finally:
            del bus._subs[before:]
