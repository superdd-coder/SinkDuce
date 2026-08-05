from __future__ import annotations

import pytest
from src.db.sessions import SessionStore


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test_sessions.db"
    s = SessionStore(str(db_path))
    yield s
    # Cleanup: close the connection
    conn = getattr(s._local, "conn", None)
    if conn:
        conn.close()


class TestSessionCRUD:
    def test_create_session_defaults(self, store):
        s = store.create_session()
        assert s.id
        assert s.title == "New Chat"
        assert s.collections == []
        assert s.created_at
        assert s.updated_at == s.created_at

        # Verify persistence
        fetched = store.get_session(s.id)
        assert fetched is not None
        assert fetched.id == s.id
        assert fetched.title == "New Chat"

    def test_create_session_with_title(self, store):
        s = store.create_session(title="test session")
        assert s.title == "test session"

        fetched = store.get_session(s.id)
        assert fetched.title == "test session"

    def test_create_session_with_collections(self, store):
        s = store.create_session(collections=["col-a", "col-b"])
        assert s.collections == ["col-a", "col-b"]

        fetched = store.get_session(s.id)
        assert fetched.collections == ["col-a", "col-b"]

    def test_list_sessions_order(self, store):
        s1 = store.create_session(title="first")
        s2 = store.create_session(title="second")
        s3 = store.create_session(title="third")

        sessions = store.list_sessions()
        ids = [s.id for s in sessions]
        # Most recently updated first
        assert ids == [s3.id, s2.id, s1.id]

    def test_get_session(self, store):
        s = store.create_session(title="find me")
        fetched = store.get_session(s.id)
        assert fetched is not None
        assert fetched.title == "find me"

    def test_get_session_not_found(self, store):
        assert store.get_session("nonexistent-id") is None

    def test_update_session_title(self, store):
        s = store.create_session(title="old")
        updated = store.update_session(s.id, title="new")
        assert updated.title == "new"
        assert updated.updated_at > s.updated_at

        fetched = store.get_session(s.id)
        assert fetched.title == "new"

    def test_delete_session(self, store):
        s = store.create_session()
        assert store.delete_session(s.id) is True
        assert store.get_session(s.id) is None

    def test_delete_nonexistent(self, store):
        assert store.delete_session("nonexistent-id") is False

    def test_delete_cascades_messages(self, store):
        s = store.create_session()
        store.add_message(s.id, "user", "hello")
        store.add_message(s.id, "assistant", "hi there")

        assert len(store.get_messages(s.id)) == 2

        store.delete_session(s.id)
        # After cascade delete, messages should be gone
        assert store.get_messages(s.id) == []
        assert store.get_session(s.id) is None


class TestMessageCRUD:
    def test_add_message_user(self, store):
        s = store.create_session()
        m = store.add_message(s.id, "user", "hello world")
        assert m.role == "user"
        assert m.content == "hello world"
        assert m.sources is None
        assert m.metadata is None
        assert m.created_at

    def test_add_message_assistant_with_sources(self, store):
        s = store.create_session()
        sources = [{"file": "doc.pdf", "chunk_id": "abc123"}]
        m = store.add_message(s.id, "assistant", "answer", sources=sources)
        assert m.role == "assistant"
        assert m.sources == sources

    def test_add_message_with_metadata(self, store):
        s = store.create_session()
        meta = {"tokens": 150, "model": "gpt-4"}
        m = store.add_message(s.id, "user", "test", metadata=meta)
        assert m.metadata == meta

    def test_get_messages_order(self, store):
        s = store.create_session()
        m1 = store.add_message(s.id, "user", "first")
        m2 = store.add_message(s.id, "assistant", "second")
        m3 = store.add_message(s.id, "user", "third")

        msgs = store.get_messages(s.id)
        ids = [m.id for m in msgs]
        assert ids == [m1.id, m2.id, m3.id]  # chronological

    def test_get_messages_limit(self, store):
        """limit counts dialogue units (user/final-assistant), not raw rows."""
        s = store.create_session()
        for i in range(10):
            store.add_message(s.id, "user", f"msg {i}")

        msgs = store.get_messages(s.id, limit=5)
        # 10 user-only dialogue units → last 5 users
        assert [m.content for m in msgs] == [f"msg {i}" for i in range(5, 10)]
        # Full history
        assert len(store.get_messages(s.id, limit=None)) == 10

    def test_get_messages_dialogue_window_includes_tools(self, store):
        s = store.create_session()
        store.add_message(s.id, "user", "old")
        store.add_message(s.id, "assistant", "old-a")
        store.add_message(s.id, "user", "new")
        store.add_message(
            s.id, "assistant", "",
            metadata={"tool_calls": [{"id": "t1", "type": "function",
                                      "function": {"name": "x", "arguments": "{}"}}]},
        )
        store.add_message(s.id, "tool", "TOOL_BODY", metadata={"tool_call_id": "t1"})
        store.add_message(s.id, "assistant", "new-a")

        # last 2 dialogue units = "new" + "new-a" (tool rows ride along)
        msgs = store.get_messages(s.id, limit=2)
        contents = [m.content for m in msgs]
        assert "old" not in contents
        assert "TOOL_BODY" in contents
        assert "new-a" in contents

    def test_get_context_messages_keeps_system_and_recent_dialogue(self, store):
        """Transcript/system always kept; dialogue window is last N turns (not tool rows)."""
        s = store.create_session(session_id="meeting_ctx_test")
        store.add_message(s.id, "system", "TRANSCRIPT_BODY")
        # 6 early dialogue units (3 user + 3 assistant answers)
        for i in range(3):
            store.add_message(s.id, "user", f"old-q-{i}")
            store.add_message(s.id, "assistant", f"old-a-{i}")
        # Tool-heavy middle that must not alone push out later turns
        store.add_message(s.id, "user", "need-tools")
        store.add_message(
            s.id, "assistant", "",
            metadata={"tool_calls": [{"id": "c1", "type": "function",
                                      "function": {"name": "request_web_search", "arguments": "{}"}}]},
        )
        store.add_message(s.id, "tool", "KOREA_DUMP" * 100, metadata={"tool_call_id": "c1"})
        store.add_message(s.id, "assistant", "korea-answer")
        # Recent explicit topic
        store.add_message(s.id, "user", "澳大利亚呢？")
        store.add_message(s.id, "assistant", "australia-answer")
        store.add_message(s.id, "user", "还有吗")

        # max_dialogue=4 → last 4 dialogue units
        ctx = store.get_context_messages(s.id, max_dialogue=4)
        # same as get_messages(limit=4)
        assert [m.id for m in ctx] == [m.id for m in store.get_messages(s.id, limit=4)]
        roles_contents = [(m.role, (m.content or "")[:40]) for m in ctx]
        # System/transcript always first
        assert roles_contents[0] == ("system", "TRANSCRIPT_BODY")
        texts = [m.content for m in ctx if m.role in ("user", "assistant") and m.content]
        assert "澳大利亚呢？" in texts
        assert "还有吗" in texts
        assert "australia-answer" in texts
        # Early pure Q&A should be outside a tight window
        assert "old-q-0" not in texts
        # Tool rows after the cut stay with the turn
        assert any(m.role == "tool" for m in ctx) or "korea-answer" in texts

    def test_add_message_updates_session_updated_at(self, store):
        s = store.create_session()
        original_updated = s.updated_at

        import time
        time.sleep(0.01)  # ensure timestamp changes

        store.add_message(s.id, "user", "new message")

        fetched = store.get_session(s.id)
        assert fetched.updated_at > original_updated

    def test_empty_messages(self, store):
        s = store.create_session()
        assert store.get_messages(s.id) == []


class TestPersistence:
    def test_data_survives_reopen(self, tmp_path):
        db_path = tmp_path / "persist.db"

        # Create and write data
        store1 = SessionStore(str(db_path))
        s = store1.create_session(title="persistent")
        store1.add_message(s.id, "user", "hello")
        store1.add_message(s.id, "assistant", "world")

        # Close
        conn1 = getattr(store1._local, "conn", None)
        if conn1:
            conn1.close()

        # Reopen
        store2 = SessionStore(str(db_path))
        fetched = store2.get_session(s.id)
        assert fetched is not None
        assert fetched.title == "persistent"

        msgs = store2.get_messages(s.id)
        assert len(msgs) == 2
        assert msgs[0].content == "hello"
        assert msgs[1].content == "world"

        conn2 = getattr(store2._local, "conn", None)
        if conn2:
            conn2.close()
