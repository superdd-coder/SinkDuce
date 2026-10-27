"""Emit sink (file_mgmt.events → bus) and middleware client-identity binding.

These cover the real path: an emit uses the request's identity when there is one,
and leaves origin bare when there is not (background threads).
"""

from __future__ import annotations

import asyncio

import pytest

from src.events.bus import EventBus
from src.events.context import current_client, reset_client, set_client
from src.events.models import make_event


def _run_async(coro):
    """Run a coroutine without poisoning the main-thread loop.

    asyncio.run() closes the loop it creates and leaves no current loop, which
    makes later sync tests that call asyncio.get_event_loop() raise
    "There is no current event loop". Own the loop and install a fresh one
    afterwards, matching the helper in tests/test_consolidation.py.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


@pytest.fixture
def fresh_bus(monkeypatch):
    """Swap the process-wide bus for a fresh one so tests don't cross-talk."""
    from src.events import bus as bus_module

    test_bus = EventBus()
    monkeypatch.setattr(bus_module, "bus", test_bus)
    # file_mgmt.events imports the module, not the attribute, so patching the
    # module attribute is enough — verify below by publishing through emit_event.
    yield test_bus


class TestEmitSink:
    def test_emit_publishes_event_with_collection_scope(self, fresh_bus):
        from src.file_mgmt.events import emit_event

        emit_event("file.uploaded", "col-1", {"file_id": "f1"})
        got = fresh_bus.snapshot_since(0)
        assert len(got) == 1
        ev = got[0]
        assert ev.type == "file.uploaded"
        assert ev.domain == "files"
        assert ev.scope == {"collection_id": "col-1"}
        assert ev.payload == {"file_id": "f1"}
        assert ev.seq == 1

    def test_emit_carries_request_identity_from_context(self, fresh_bus):
        from src.file_mgmt.events import emit_event

        token = set_client("c-7", "k-3")
        try:
            emit_event("node.created", "col-1", {"node_id": "n1"})
        finally:
            reset_client(token)
        ev = fresh_bus.snapshot_since(0)[0]
        assert ev.origin == {"client_id": "c-7", "key_id": "k-3"}

    def test_emit_without_request_context_has_bare_origin(self, fresh_bus):
        # Background completion (no request) must be delivered to everyone.
        from src.file_mgmt.events import emit_event

        emit_event("file.updated", "col-1", {})
        assert fresh_bus.snapshot_since(0)[0].origin == {}

    def test_emit_reaches_live_subscriber(self, fresh_bus):
        from src.file_mgmt.events import emit_event

        sub = fresh_bus.subscribe()
        emit_event("file.deleted", "col-1", {"file_id": "f1"})
        assert sub.queue.get(timeout=1).type == "file.deleted"


class TestMiddlewareClientBinding:
    """Drive the ASGI middleware directly with a probe app that reads the context."""

    def _run(self, monkeypatch, *, headers=None, query=b"", token_record=None, is_local=True):
        from src.pairing import middleware as mw
        from src.pairing import service

        captured: dict = {}

        async def probe_app(scope, receive, send):
            captured["client"] = current_client()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        monkeypatch.setattr(service, "is_local_scope", lambda scope: is_local)
        monkeypatch.setattr(
            service, "verify_token", lambda tok: token_record if tok else None
        )

        scope = {
            "type": "http",
            "path": "/api/whatever",
            "query_string": query,
            "headers": headers or [],
        }
        gate = mw.PairingGateMiddleware(probe_app)

        async def run():
            sent = []

            async def send(msg):
                sent.append(msg)

            await gate(scope, receive, send)
            return sent

        sent = _run_async(run())
        return captured.get("client"), sent

    def test_header_client_id_is_bound(self, monkeypatch):
        client, sent = self._run(
            monkeypatch, headers=[(b"x-client-id", b"client-abc")]
        )
        assert sent[0]["status"] == 200
        assert client.client_id == "client-abc"
        assert client.key_id is None

    def test_header_name_is_case_insensitive(self, monkeypatch):
        client, _ = self._run(monkeypatch, headers=[(b"X-Client-Id", b"client-abc")])
        assert client.client_id == "client-abc"

    def test_query_client_id_is_bound(self, monkeypatch):
        # EventSource cannot set headers, so the SSE endpoint uses ?client=.
        client, _ = self._run(monkeypatch, query=b"client=client-q")
        assert client.client_id == "client-q"

    def test_header_wins_over_query(self, monkeypatch):
        client, _ = self._run(
            monkeypatch,
            headers=[(b"x-client-id", b"from-header")],
            query=b"client=from-query",
        )
        assert client.client_id == "from-header"

    def test_paired_request_binds_key_id(self, monkeypatch):
        # A key id can only be resolved when the request actually presents a key.
        client, _ = self._run(
            monkeypatch,
            headers=[
                (b"x-client-id", b"client-abc"),
                (b"x-pairing-token", b"sd1_sometoken"),
            ],
            token_record={"id": "key-9", "role": "owner"},
        )
        assert client.client_id == "client-abc"
        assert client.key_id == "key-9"

    def test_unpaired_request_leaves_key_id_none(self, monkeypatch):
        # Local keyless access: client id present, no key. Origin must omit key_id.
        client, _ = self._run(monkeypatch, headers=[(b"x-client-id", b"client-abc")])
        assert client.key_id is None

    def test_gate_rejection_does_not_reach_the_app(self, monkeypatch):
        from src.pairing import middleware as mw
        from src.pairing import service

        reached = {"n": 0}

        async def probe_app(scope, receive, send):
            reached["n"] += 1

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        monkeypatch.setattr(service, "is_local_scope", lambda scope: False)
        monkeypatch.setattr(service, "verify_token", lambda tok: None)
        scope = {
            "type": "http",
            "path": "/api/whatever",
            "query_string": b"",
            "headers": [],
        }

        async def run():
            async def send(msg):
                pass

            await mw.PairingGateMiddleware(probe_app)(scope, receive, send)

        _run_async(run())
        assert reached["n"] == 0
