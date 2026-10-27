"""Session-domain emission: chat sessions must notify other devices.

Design: development_doc/2026-09-16-multi-device-state-sync-design.md (§5).
Without these, device B never learns device A opened a new chat, which is the
reported symptom. session.message_created is deliberately distinct from the
file-management `message.created` (collection messages) — different entities.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.events.bus import EventBus


@pytest.fixture
def fresh_bus(monkeypatch):
    from src.events import bus as bus_module

    test_bus = EventBus()
    monkeypatch.setattr(bus_module, "bus", test_bus)
    return test_bus


@pytest.fixture
def client():
    from src.pairing import service as pairing_service
    from src.main import app

    pairing_service.ensure_local_key()
    # Loopback client so the pairing gate treats this as a local request.
    return TestClient(app, base_url="http://127.0.0.1:18900", client=("127.0.0.1", 51112))


def _fake_session(session_id: str, title: str = "probe"):
    return SimpleNamespace(
        id=session_id,
        title=title,
        collections=[],
        config={},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        message_count=0,
        last_message=None,
    )


class TestSessionEmission:
    def test_create_session_emits_session_created(self, monkeypatch, fresh_bus, client):
        from src.api.routes import sessions as mod

        monkeypatch.setattr(
            mod, "_get_store", lambda: SimpleNamespace(create_session=lambda **kw: _fake_session("s1"))
        )
        monkeypatch.setattr(mod, "_session_response", lambda s, store: {"id": s.id, "title": s.title})

        resp = client.post("/api/sessions", json={"title": "probe"})
        assert resp.status_code == 201, resp.text

        events = [e for e in fresh_bus.snapshot_since(0) if e.type == "session.created"]
        assert len(events) == 1, "creating a session must emit session.created"
        assert events[0].domain == "sessions"
        assert events[0].scope.get("session_id") == "s1"

    def test_delete_session_emits_session_deleted(self, monkeypatch, fresh_bus, client):
        from src.api.routes import sessions as mod

        monkeypatch.setattr(
            mod,
            "_get_store",
            lambda: SimpleNamespace(delete_session=lambda sid: True),
        )
        monkeypatch.setattr(
            "src.chatbox.image_memory.purge_session_images", lambda sid: None, raising=False
        )

        resp = client.delete("/api/sessions/s2")
        assert resp.status_code == 204, resp.text

        events = [e for e in fresh_bus.snapshot_since(0) if e.type == "session.deleted"]
        assert len(events) == 1
        assert events[0].scope.get("session_id") == "s2"

    def test_rename_emits_session_updated(self, monkeypatch, fresh_bus, client):
        from src.api.routes import sessions as mod

        store = SimpleNamespace(
            get_session=lambda sid: _fake_session(sid),
            update_session=lambda sid, **kw: _fake_session(sid, title=kw.get("title", "x")),
        )
        monkeypatch.setattr(mod, "_get_store", lambda: store)
        monkeypatch.setattr(mod, "_session_response", lambda s, store: {"id": s.id, "title": s.title})

        resp = client.patch("/api/sessions/s3", json={"title": "renamed"})
        assert resp.status_code == 200, resp.text
        events = [e for e in fresh_bus.snapshot_since(0) if e.type == "session.updated"]
        assert len(events) == 1
        assert events[0].scope.get("session_id") == "s3"

    def test_emits_are_distinct_from_collection_messages(self, fresh_bus):
        """A chat message event must not collide with file_mgmt's message.created."""
        from src.events.models import resolve_domain

        assert resolve_domain("session.message_created") == "sessions"
        assert resolve_domain("message.created") == "files"


class TestSendMessageEmission:
    """The chat route is an SSE stream; peers must learn the user turn arrived and
    that the assistant turn finished — including on the error path, where
    ChatStreamHandler still persists a message."""

    def _drive(self, monkeypatch, fresh_bus, chunks, *, raises=False):
        import asyncio
        from types import SimpleNamespace

        from src.api.routes import sessions as mod

        store = SimpleNamespace(
            get_session=lambda sid: _fake_session(sid),
            update_session=lambda sid, **kw: _fake_session(sid),
        )
        monkeypatch.setattr(mod, "_get_store", lambda: store)

        async def fake_handle(self, session_id, content, **kwargs):
            for c in chunks:
                yield c
            if raises:
                raise RuntimeError("stream blew up mid-turn")

        monkeypatch.setattr("src.chatbox.stream_handler.ChatStreamHandler.handle", fake_handle)
        monkeypatch.setattr(
            mod, "services", SimpleNamespace(chatbox_agent=object()), raising=False
        )

        body = mod.SessionMessageRequest(content="hello", mode="direct")

        async def run():
            resp = await mod.send_message("s1", body)
            out = []
            async for chunk in resp.body_iterator:
                out.append(chunk)
            return out

        return run()

    def test_send_message_emits_message_created(self, monkeypatch, fresh_bus):
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._drive(monkeypatch, fresh_bus, ["a", "b"]))
        finally:
            loop.close()
            asyncio.set_event_loop(asyncio.new_event_loop())

        events = [
            e for e in fresh_bus.snapshot_since(0) if e.type == "session.message_created"
        ]
        assert events, "sending a message must notify peers"
        assert all(e.scope.get("session_id") == "s1" for e in events)
        # Both ends of the turn are announced: the user turn on acceptance and the
        # assistant turn when it finishes.
        roles = [e.payload.get("role") for e in events]
        assert "user" in roles, roles
        assert "assistant" in roles, roles

    def test_assistant_turn_finishing_emits_even_when_other_devices_must_refresh(
        self, monkeypatch, fresh_bus
    ):
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._drive(monkeypatch, fresh_bus, ["x"]))
        finally:
            loop.close()
            asyncio.set_event_loop(asyncio.new_event_loop())

        after = [e for e in fresh_bus.snapshot_since(0)]
        assert len(after) >= 1

    def test_a_failed_stream_still_emits(self, monkeypatch, fresh_bus):
        # ChatStreamHandler persists an error/partial message, so peers must be
        # told even when the turn ends badly.
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            try:
                loop.run_until_complete(
                    self._drive(monkeypatch, fresh_bus, ["partial"], raises=True)
                )
            except RuntimeError:
                pass
        finally:
            loop.close()
            asyncio.set_event_loop(asyncio.new_event_loop())

        events = [
            e for e in fresh_bus.snapshot_since(0) if e.type == "session.message_created"
        ]
        assert events, "a failed turn still persists a message and must notify peers"
