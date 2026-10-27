"""Tests for cross-turn session image memory (refs + snapshots + hydration)."""

from __future__ import annotations

import json
import base64
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image


def _image_url(i: int) -> str:
    buffer = BytesIO()
    Image.new("RGB", (2, 2), (i, 0, 0)).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


# ── Snapshot store ────────────────────────────────────────────


class TestSnapshotStore:
    def test_write_once_roundtrip_and_purge(self, tmp_path):
        from src.chatbox.image_memory import (
            purge_session_images,
            load_session_image,
            snapshot_session_images,
        )

        payload = "data:image/png;base64,AAAA"
        snapshot_session_images("sess1", {"imgA": payload}, base_dir=tmp_path)
        # second write must not change the file (write-once determinism)
        snapshot_session_images("sess1", {"imgA": "data:image/png;base64,BBBB"}, base_dir=tmp_path)

        assert load_session_image("sess1", "imgA", base_dir=tmp_path) == payload
        assert load_session_image("sess1", "missing", base_dir=tmp_path) is None

        purge_session_images("sess1", base_dir=tmp_path)
        assert load_session_image("sess1", "imgA", base_dir=tmp_path) is None

    def test_unsafe_ids_are_sanitized(self, tmp_path):
        from src.chatbox.image_memory import (
            load_session_image,
            snapshot_session_images,
        )

        snapshot_session_images("s", {"../evil": "data:text/plain;base64,eG1l"}, base_dir=tmp_path)
        assert load_session_image("s", "../evil", base_dir=tmp_path) == "data:text/plain;base64,eG1l"
        # nothing escaped the session dir
        assert not (tmp_path / "evil.txt").exists()


# ── Persist / projection ─────────────────────────────────────


class TestPersistTextChannel:
    def test_persist_prefers_persist_text_for_list_content(self, store):
        from src.chatbox.agent import _persist_extra_messages

        s = store.create_session()
        ctx = 'before\n:::image imgA f1\nocr\ndesc\n:::end\nafter'
        extras = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_v", "type": "function",
                 "function": {"name": "lookup_collection", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_v",
             "content": [{"type": "text", "text": "before"},
                          {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}],
             "_persist_text": ctx},
        ]
        _persist_extra_messages(store, s.id, extras)
        rows = [m for m in store.get_messages(s.id) if m.role == "tool"]
        assert len(rows) == 1
        assert rows[0].content == ctx
        assert rows[0].metadata.get("tool_call_id") == "call_v"

    def test_sanitize_projects_private_keys(self):
        """_persist_text must never leak into an outgoing API request."""
        from src.chatbox.agent import _sanitize_tool_messages

        msgs = [{
            "role": "tool", "tool_call_id": "call_v",
            "content": "ctx",
            "_persist_text": "secret-ish",
        }]
        out = _sanitize_tool_messages(msgs)
        assert "_persist_text" not in out[0]
        assert out[0]["content"] == "ctx"


# ── Hydration ─────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    from src.db.sessions import SessionStore

    s = SessionStore(str(tmp_path / "imgmem.db"))
    yield s
    conn = getattr(s._local, "conn", None)
    if conn:
        conn.close()


def _ctx(img_id: str) -> str:
    return (
        "text before\n"
        f":::image\nimage_id: {img_id}\nfile_id: file1\n"
        f"ocr_text: ocr {img_id}\ndescription: desc {img_id}\n"
        ":::\n"
        "text after"
    )


def _seed_history(store, sid: str, n: int):
    store.add_message(sid, "user", "看看图")
    for i in range(n):
        tid = f"call_i{i}"
        store.add_message(sid, "assistant", "", metadata={"tool_calls": [
            {"id": tid, "type": "function",
             "function": {"name": "lookup_collection", "arguments": "{}"}},
        ]})
        store.add_message(sid, "tool", _ctx(f"a{i}"), metadata={"tool_call_id": tid})
    store.add_message(sid, "assistant", "答")


class TestHydration:
    @pytest.fixture
    def agent(self, store, tmp_path):
        from src.chatbox.agent import ChatboxAgent

        a = ChatboxAgent(
            session_store=store,
            chat_llm=MagicMock(),
            agentic_service=MagicMock(),
        )
        a._image_store_dir = tmp_path  # injected by implementation
        return a

    def test_visual_model_hydrates_recent_images(self, store, agent, tmp_path):
        from src.chatbox.image_memory import snapshot_session_images

        s = store.create_session()
        _seed_history(store, s.id, 2)
        for i in range(2):
            snapshot_session_images(s.id, {f"a{i}": _image_url(i)}, base_dir=tmp_path)

        with patch("src.chatbox.agent._chat_model_is_visual", return_value=True), \
             patch.object(agent, "_image_store_dir", tmp_path):
            msgs = agent._build_messages(s.id, "图里有几个池子")

        hydrated = [m for m in msgs if isinstance(m.get("content"), list)]
        assert len(hydrated) == 2
        flat = json.dumps(hydrated, ensure_ascii=False)
        assert _image_url(0) in flat
        assert _image_url(1) in flat

    def test_budget_keeps_oldest_as_explicit_reference(self, store, agent, tmp_path):
        from src.chatbox.image_memory import snapshot_session_images
        from src.chatbox.agent import _MAX_INLINE_HISTORY_IMAGES

        s = store.create_session()
        n = _MAX_INLINE_HISTORY_IMAGES + 1
        _seed_history(store, s.id, n)
        for i in range(n):
            snapshot_session_images(s.id, {f"a{i}": _image_url(i)}, base_dir=tmp_path)

        with patch("src.chatbox.agent._chat_model_is_visual", return_value=True), \
             patch.object(agent, "_image_store_dir", tmp_path):
            msgs = agent._build_messages(s.id, "对比这些图")

        lists = [m for m in msgs if isinstance(m.get("content"), list)]
        assert len(lists) == _MAX_INLINE_HISTORY_IMAGES
        flat = json.dumps(lists, ensure_ascii=False)
        assert _image_url(n - 1) in flat, "newest stays inline"
        assert _image_url(0) not in flat, "oldest stays as a reference"
        assert not any("desc a0" in (m.get("content") or "") for m in msgs if isinstance(m.get("content"), str))

    def test_non_visual_model_keeps_text(self, store, agent, tmp_path):
        from src.chatbox.image_memory import snapshot_session_images

        s = store.create_session()
        _seed_history(store, s.id, 1)
        snapshot_session_images(s.id, {"a0": "data:image/png;base64,B0"}, base_dir=tmp_path)

        with patch("src.chatbox.agent._chat_model_is_visual", return_value=False), \
             patch.object(agent, "_image_store_dir", tmp_path):
            msgs = agent._build_messages(s.id, "图里有几个池子")

        assert not any(isinstance(m.get("content"), list) for m in msgs)
        assert any("desc a0" in (m.get("content") or "") for m in msgs
                   if isinstance(m.get("content"), str))
