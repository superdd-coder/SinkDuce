"""Mid-stream disconnect: partial answer must survive a page refresh.

Covers the crash-safety net for chat turns:
  1. ChatStreamHandler accumulates token events and, when closed without a
     ``done`` event (client disconnect), persists the partial answer.
  2. ``ChatboxAgent.persist_interrupted_answer`` writes an assistant message
     flagged ``partial``/``interrupted``.
  3. No duplicate when the full answer was already persisted (done raced the
     disconnect), and no message for an empty partial.
  4. A normal completed stream does not trigger the interrupted path.

Run: pytest tests/test_chat_interrupted_answer.py -v --tb=short
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.chatbox.agent import ChatboxAgent
from src.chatbox.stream_handler import ChatStreamHandler
from src.db.sessions import SessionStore

import pytest


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test_interrupted.db"
    s = SessionStore(str(db_path))
    yield s
    conn = getattr(s._local, "conn", None)
    if conn:
        conn.close()


def _stub_agent(store):
    """Stub with the real persist method bound, without full agent wiring."""
    agent = SimpleNamespace(_store=store)
    agent.persist_interrupted_answer = ChatboxAgent.persist_interrupted_answer.__get__(
        agent
    )
    return agent


def _assistant_messages(store, session_id):
    return [
        m for m in store.get_messages(session_id, limit=None)
        if getattr(m, "role", None) == "assistant"
    ]


def test_persist_interrupted_writes_partial_flagged_message(store):
    agent = _stub_agent(store)
    sid = store.create_session(title="t").id
    agent.persist_interrupted_answer(sid, "Partial ans")
    msgs = _assistant_messages(store, sid)
    assert len(msgs) == 1
    assert msgs[0].content == "Partial ans"
    meta = msgs[0].metadata or {}
    assert meta.get("partial") is True
    assert meta.get("interrupted") is True


def test_persist_interrupted_dedupes_identical_last_answer(store):
    agent = _stub_agent(store)
    sid = store.create_session(title="t").id
    agent.persist_interrupted_answer(sid, "Same text")
    # Simulate the done-race: full answer already on disk with this text
    agent.persist_interrupted_answer(sid, "Same text")
    assert len(_assistant_messages(store, sid)) == 1


def test_persist_interrupted_ignores_empty_text(store):
    agent = _stub_agent(store)
    sid = store.create_session(title="t").id
    agent.persist_interrupted_answer(sid, "   ")
    assert _assistant_messages(store, sid) == []


def _closed_mid_stream(agent, session_id):
    """Drive handler.handle and close it mid-turn like a vanished client."""

    async def scenario():
        handler = ChatStreamHandler(agent)
        agen = handler.handle(session_id, "hi")
        it = agen.__aiter__()
        first = await it.__anext__()
        second = await it.__anext__()
        # Client disconnected: abandon iteration and close the generator.
        await agen.aclose()
        return first, second

    return asyncio.run(scenario())


def test_handler_persists_partial_on_disconnect(store):
    agent = _stub_agent(store)

    def fake_chat_stream(session_id, user_message, **kwargs):
        async def _g():
            yield {"type": "token", "content": "Hello"}
            yield {"type": "token", "content": " world"}
            await asyncio.sleep(3600)  # turn still running…
            yield {"type": "done", "sources": []}

        return _g()

    agent.chat_stream = fake_chat_stream
    sid = store.create_session(title="t").id

    _closed_mid_stream(agent, sid)

    msgs = _assistant_messages(store, sid)
    assert len(msgs) == 1
    assert msgs[0].content == "Hello world"
    assert (msgs[0].metadata or {}).get("partial") is True


def test_handler_skips_persist_after_done(store):
    agent = _stub_agent(store)

    def fake_chat_stream(session_id, user_message, **kwargs):
        async def _g():
            yield {"type": "token", "content": "Full"}
            yield {"type": "done", "sources": []}

        return _g()

    agent.chat_stream = fake_chat_stream
    sid = store.create_session(title="t").id

    async def consume_all():
        handler = ChatStreamHandler(agent)
        agen = handler.handle(sid, "hi")
        async for _ in agen:
            pass
        await agen.aclose()

    asyncio.run(consume_all())
    assert _assistant_messages(store, sid) == []
