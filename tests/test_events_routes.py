"""Event SSE endpoint: frame semantics (replay/gap/overflow/keepalive) and gating.

The frame generator is an async generator (see tests/test_events_stream_concurrency.py
for why), so these tests drive it with a real event loop. A loop-poisoning-safe
runner is used because asyncio.run() would break asyncio.get_event_loop() for
later sync tests.
"""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from src.events.bus import EventBus
from src.events.models import make_event
from src.events.routes import iter_event_frames

# A tiny idle interval keeps the keepalive path fast in tests; the production
# default (20s) is asserted separately below.
FAST = 0.05


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


def drain(event_bus: EventBus, since=None, limit=5, sub=None):
    """Collect up to `limit` frames from a fresh (or supplied) subscription."""

    async def scenario():
        subscription = sub or event_bus.subscribe()
        out = []
        gen = iter_event_frames(
            event_bus, subscription, since, limit=limit, timeout=FAST
        )
        try:
            async for frame in gen:
                out.append(frame)
        finally:
            await gen.aclose()
        return out

    return _run(scenario())


def drain_one(event_bus: EventBus, gen):
    """Await a single frame from an existing generator."""

    async def scenario():
        return await gen.__anext__()

    return _run(scenario())


class TestFrameStream:
    def test_hello_is_first_and_carries_cursor(self):
        bus = EventBus()
        bus.publish(make_event("file.updated"))
        frames = drain(bus, limit=1)
        name, data = frames[0]
        assert name == "hello"
        assert data["seq"] == 1
        assert data["oldest"] == 1

    def test_hello_on_empty_bus_reports_no_floor(self):
        frames = drain(EventBus(), limit=1)
        assert frames[0] == ("hello", {"seq": 0, "oldest": None})

    def test_fresh_connection_gets_no_replay(self):
        bus = EventBus()
        bus.publish(make_event("file.updated"))
        frames = drain(bus, since=None, limit=1)
        assert [n for n, _ in frames] == ["hello"]  # nothing replayed

    def test_replay_after_cursor_in_order(self):
        bus = EventBus()
        for _ in range(4):
            bus.publish(make_event("file.updated"))
        frames = drain(bus, since=2, limit=3)
        assert [n for n, _ in frames] == ["hello", "change", "change"]
        assert [d.seq for n, d in frames if n == "change"] == [3, 4]

    def test_gap_when_cursor_evicted(self):
        bus = EventBus(maxlen=2)
        for _ in range(5):
            bus.publish(make_event("file.updated"))
        frames = drain(bus, since=1, limit=2)
        assert [n for n, _ in frames] == ["hello", "resync"]
        assert frames[1][1]["reason"] == "gap"

    def test_no_duplicate_when_event_lands_between_subscribe_and_snapshot(self):
        # subscribe() happens first, so a publish right after must not appear
        # twice (once from replay, once from the live queue).
        bus = EventBus()
        sub = bus.subscribe()
        bus.publish(make_event("file.updated"))  # seq 1, also lands in sub.queue
        frames = drain(bus, since=0, limit=4, sub=sub)
        changes = [d.seq for n, d in frames if n == "change"]
        assert changes == [1]

    def test_keepalive_interval_default_is_twenty_seconds(self):
        from src.events.routes import KEEPALIVE_SECONDS

        assert KEEPALIVE_SECONDS == 20


class TestEncoding:
    def test_change_frame_is_valid_sse_with_event_name(self):
        from src.events.routes import _encode

        bus = EventBus()
        ev = bus.publish(make_event("meeting.updated", scope={"meeting_id": "m1"}))
        text = _encode("change", ev)
        assert text.startswith("event: change\ndata: ")
        assert text.endswith("\n\n")
        payload = json.loads(text.split("data: ", 1)[1].strip())
        assert payload["type"] == "meeting.updated"
        assert payload["domain"] == "meetings"
        assert payload["scope"] == {"meeting_id": "m1"}

    def test_keepalive_frame_is_a_comment(self):
        from src.events.routes import _encode

        assert _encode(":", None) == ": keepalive\n\n"


class TestGate:
    """The endpoint lives under /api, so the pairing gate must apply unchanged."""

    def test_lan_request_without_key_is_401(self):
        from src.main import app

        client = TestClient(app, base_url="http://192.168.1.30:18901")
        r = client.get("/api/events/stream")
        assert r.status_code == 401
