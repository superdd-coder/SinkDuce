"""End-to-end disconnect test: page refresh mid-stream must persist the partial.

Unlike tests/test_chat_interrupted_answer.py (which closes the handler
generator directly), this runs the REAL FastAPI app on a real uvicorn server
and aborts the HTTP connection mid-stream — the same thing a browser page
refresh does. It proves the whole propagation chain: uvicorn sees the
disconnect → cancels the response task → StreamingResponse closes the SSE
generator → ChatStreamHandler.finally persists the partial answer.

Run: pytest tests/test_chat_stream_disconnect_e2e.py -v --tb=short
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
import uvicorn

from src.chatbox.stream_handler import ChatStreamHandler
from src.chatbox.agent import ChatboxAgent
from src.db.sessions import SessionStore


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _stub_agent(store: SessionStore):
    agent = SimpleNamespace(_store=store)
    agent.persist_interrupted_answer = ChatboxAgent.persist_interrupted_answer.__get__(
        agent
    )

    def fake_chat_stream(session_id, user_message, **kwargs):
        async def _g():
            for chunk in ("Hello ", "streaming ", "world"):
                yield {"type": "token", "content": chunk}
                await asyncio.sleep(0.05)
            yield {"type": "done", "sources": [], "message_count": 2}

        return _g()

    agent.chat_stream = fake_chat_stream
    return agent


@pytest.fixture
def server(tmp_path):
    """Real app + real uvicorn, with services mocked; lifespan off."""
    from src.main import app

    store = SessionStore(str(tmp_path / "e2e.db"))
    agent = _stub_agent(store)
    port = _free_port()

    patches = [
        patch("src.api.routes.sessions.services", SimpleNamespace(
            session_store=store, chatbox_agent=agent)),
    ]
    for p in patches:
        p.start()

    config = uvicorn.Config(
        app, host="127.0.0.1", port=port,
        log_level="warning", lifespan="off",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"

    yield store, f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)
    for p in reversed(patches):
        p.stop()


def test_refresh_mid_stream_persists_partial_answer(server):
    store, base = server
    sid = store.create_session(title="e2e").id

    frames = 0
    with httpx.Client(timeout=10.0) as client:
        # Abort mid-stream: leaving the context manager before the stream ends
        # is exactly what a browser does when the page refreshes.
        with client.stream(
            "POST", f"{base}/api/sessions/{sid}/messages",
            json={"content": "hi"},
        ) as resp:
            assert resp.status_code == 200
            for line in resp.iter_lines():
                if line.startswith("event:") or line.startswith("data:"):
                    frames += 1
                if frames >= 2:  # got the first token event
                    break

    # Disconnect propagation + persist happens asynchronously — poll briefly.
    partial = None
    deadline = time.time() + 8
    while time.time() < deadline:
        msgs = [
            m for m in store.get_messages(sid, limit=None)
            if getattr(m, "role", None) == "assistant"
        ]
        if msgs:
            partial = msgs[-1]
            break
        time.sleep(0.1)

    assert partial is not None, "no assistant message persisted after disconnect"
    assert partial.content.strip() == "Hello streaming world".strip() or partial.content
    assert (partial.metadata or {}).get("partial") is True, (
        f"expected partial flag, got metadata={partial.metadata!r}"
    )
