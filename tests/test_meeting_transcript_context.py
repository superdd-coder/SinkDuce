"""Meeting QuickChat: ephemeral transcript at build-context time.

Transcript is loaded from the meeting store on every LLM context build
(after fixed system, before dialogue history). It is never persisted as
a session system message.

Run: pytest tests/test_meeting_transcript_context.py -v --tb=short
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.db.sessions import SessionStore
from src.meeting.models import TranscriptSegment, TranscriptionResult


@pytest.fixture
def store(tmp_path):
    s = SessionStore(str(tmp_path / "meeting_transcript.db"))
    yield s
    conn = getattr(s._local, "conn", None)
    if conn:
        conn.close()


def _transcript(*texts: str, speaker: str = "0") -> TranscriptionResult:
    segs = [
        TranscriptSegment(start=float(i), end=float(i) + 1.0, text=t, speaker_id=speaker)
        for i, t in enumerate(texts)
    ]
    return TranscriptionResult(text=" ".join(texts), segments=segs)


class TestLoadMeetingTranscriptText:
    def test_prefers_sentences_over_transcript_json(self):
        from src.chatbox.meeting_context import load_meeting_transcript_text

        sentences = [
            {"original_text": "from sentences", "speaker": "0"},
        ]
        tx = _transcript("from transcript.json")
        with (
            patch("src.chatbox.meeting_context.get_sentences", return_value=sentences),
            patch("src.chatbox.meeting_context.get_transcript", return_value=tx),
        ):
            text = load_meeting_transcript_text("mid1")
        assert text is not None
        assert "from sentences" in text
        assert "from transcript.json" not in text

    def test_falls_back_to_transcript_json(self):
        from src.chatbox.meeting_context import load_meeting_transcript_text

        tx = _transcript("hello alpha", "hello beta")
        with (
            patch("src.chatbox.meeting_context.get_sentences", return_value=None),
            patch("src.chatbox.meeting_context.get_transcript", return_value=tx),
        ):
            text = load_meeting_transcript_text("mid2")
        assert text is not None
        assert "[ref:1] 0: hello alpha" in text
        assert "[ref:2] 0: hello beta" in text

    def test_returns_none_when_empty(self):
        from src.chatbox.meeting_context import load_meeting_transcript_text

        with (
            patch("src.chatbox.meeting_context.get_sentences", return_value=None),
            patch("src.chatbox.meeting_context.get_transcript", return_value=None),
        ):
            assert load_meeting_transcript_text("mid3") is None


class TestMeetingEphemeralContext:
    def test_load_general_summary_text_reads_tab_general(self):
        from src.chatbox.meeting_context import load_general_summary_text

        with patch(
            "src.meeting.store.get_section_md",
            return_value="## Summary\nTalked about pricing.\n## Todo\n- Send quote",
        ) as get_md:
            text = load_general_summary_text("mid1")
        get_md.assert_called_once_with("mid1", "tab_general")
        assert "Send quote" in text
        assert "## Summary" in text

    def test_load_general_summary_text_empty_when_missing(self):
        from src.chatbox.meeting_context import load_general_summary_text

        with patch("src.meeting.store.get_section_md", return_value=None):
            assert load_general_summary_text("mid1") == ""

    def test_build_ephemeral_context_includes_speakers_and_outline(self):
        from src.chatbox.meeting_context import build_meeting_ephemeral_context

        meeting = MagicMock()
        meeting.speaker_names = {"0": "Alice"}
        meeting.speaker_people = {}
        with patch("src.meeting.store.get_meeting", return_value=meeting), patch(
            "src.meeting.store.get_section_md",
            return_value="## Todo\n- Follow up on price",
        ), patch(
            "src.speakers.service.rebuild_speaker_names",
            return_value={"0": "Alice"},
        ):
            ctx = build_meeting_ephemeral_context("mid1")
        assert "Alice" in ctx
        assert "Follow up on price" in ctx
        assert "outline" in ctx.lower() or "general summary" in ctx.lower()

    def test_injected_outline_replaces_spk_tags_with_display_names(self):
        from src.chatbox.meeting_context import build_meeting_ephemeral_context

        meeting = MagicMock()
        meeting.speaker_names = {"1": "David", "3": "Xu"}
        meeting.speaker_people = {}
        with patch("src.meeting.store.get_meeting", return_value=meeting), patch(
            "src.meeting.store.get_section_md",
            return_value="## Todo\n- [spk:3] email [spk:1] the monthly dose",
        ), patch(
            "src.speakers.service.rebuild_speaker_names",
            return_value={"1": "David", "3": "Xu"},
        ):
            ctx = build_meeting_ephemeral_context("mid1")
        assert "[spk:3]" not in ctx
        assert "[spk:1]" not in ctx
        assert "Xu" in ctx
        assert "David" in ctx


class TestEphemeralBuildContext:
    def test_meeting_chat_has_no_full_transcript_system_slot(self, store):
        """Transcript comes from lookup_meeting_transcript, not a system dump."""
        from src.chatbox.agent import ChatboxAgent

        mid = "live_tx"
        sid = f"meeting_{mid}"
        store.create_session(session_id=sid)
        store.add_message(sid, "user", "what was said?")
        store.add_message(sid, "assistant", "summary")
        store.add_message(sid, "user", "more?")

        agent = ChatboxAgent(store, MagicMock(), None)
        msgs = agent._build_messages(
            sid,
            "more?",
            system_prompt="MEETING_SYSTEM",
            pre_message_context="Speaker mapping: S1=Alice",
        )

        assert msgs[0]["content"] == "MEETING_SYSTEM"
        flat = "\n".join(str(m.get("content") or "") for m in msgs)
        assert "LIVE_TRANSCRIPT_BODY" not in flat
        systems_in_db = [
            m for m in store.get_messages(sid, limit=None) if m.role == "system"
        ]
        assert systems_in_db == []
        contents = [m.get("content") for m in msgs]
        assert "what was said?" in contents
        speaker_idxs = [
            i for i, m in enumerate(msgs)
            if m["role"] == "system" and "Speaker mapping" in (m.get("content") or "")
        ]
        assert speaker_idxs and speaker_idxs[0] > 0

    def test_ignores_stale_db_system_transcript_rows(self, store):
        """Old persisted system rows must not be mixed into context."""
        from src.chatbox.agent import ChatboxAgent

        mid = "stale_db"
        sid = f"meeting_{mid}"
        store.create_session(session_id=sid)
        store.add_message(sid, "system", "STALE_OLD_TRANSCRIPT")
        store.add_message(sid, "system", "STALE_DUP")
        store.add_message(sid, "user", "q1")

        agent = ChatboxAgent(store, MagicMock(), None)
        msgs = agent._build_messages(
            sid, "q1", system_prompt="MEETING_SYSTEM",
        )

        flat = "\n".join(str(m.get("content") or "") for m in msgs)
        assert "STALE_OLD_TRANSCRIPT" not in flat
        assert "STALE_DUP" not in flat
        system_bodies = [
            m["content"] for m in msgs
            if m["role"] == "system" and m["content"] != "MEETING_SYSTEM"
        ]
        assert all("STALE" not in (b or "") for b in system_bodies)

    def test_create_session_does_not_persist_transcript(self, store):
        from src.api.routes.sessions import SessionCreateRequest, create_session

        mid = "no_persist"
        tx = _transcript("should not land in session")
        with (
            patch("src.api.routes.sessions.services") as mock_svc,
            patch(
                "src.chatbox.meeting_context.load_meeting_transcript_text",
                return_value="[1] 0: should not land in session",
            ),
            patch("src.chatbox.meeting_context.get_sentences", return_value=None),
            patch("src.chatbox.meeting_context.get_transcript", return_value=tx),
        ):
            mock_svc.session_store = store
            create_session(SessionCreateRequest(id=f"meeting_{mid}", title="Meet"))
            create_session(SessionCreateRequest(id=f"meeting_{mid}", title="Meet"))

        systems = [
            m for m in store.get_messages(f"meeting_{mid}", limit=None)
            if m.role == "system"
        ]
        assert systems == []

    def test_chat_does_not_inject_full_transcript_into_llm(self, store):
        from src.chatbox.agent import ChatboxAgent

        mid = "agent_live"
        sid = f"meeting_{mid}"
        store.create_session(session_id=sid)
        store.add_message(sid, "user", "q1")
        store.add_message(sid, "assistant", "no transcript before")

        mock_llm = MagicMock()
        mock_llm._model = "test-model"
        mock_llm._client = MagicMock()
        agent = ChatboxAgent(
            session_store=store,
            chat_llm=mock_llm,
            agentic_service=None,
        )

        captured: list[list] = []

        def capture_create(**kwargs):
            captured.append(kwargs.get("messages") or [])
            return _fake_completion("answer from tools")

        mock_llm._client.chat.completions.create.side_effect = capture_create

        resp = agent.chat(
            sid, "How was Company X evaluated?", mode="direct",
        )

        assert resp.answer
        assert captured
        flat = "\n".join(
            str(m.get("content") or "") for m in captured[0]
        )
        assert "Company X performed strongly overall" not in flat
        assert not any(
            m.role == "system"
            for m in store.get_messages(sid, limit=None)
        )

    def test_chat_injects_general_summary_not_persisted(self, store):
        from src.chatbox.agent import ChatboxAgent

        mid = "with_outline"
        sid = f"meeting_{mid}"
        store.create_session(session_id=sid)

        mock_llm = MagicMock()
        mock_llm._model = "test-model"
        mock_llm._client = MagicMock()
        agent = ChatboxAgent(
            session_store=store,
            chat_llm=mock_llm,
            agentic_service=None,
        )
        captured: list[list] = []

        def capture_create(**kwargs):
            captured.append(kwargs.get("messages") or [])
            return _fake_completion("next is send the quote")

        mock_llm._client.chat.completions.create.side_effect = capture_create
        meeting = MagicMock()
        meeting.speaker_names = {"0": "Alice"}
        with patch("src.meeting.store.get_meeting", return_value=meeting), patch(
            "src.meeting.store.get_section_md",
            return_value="## Todo\n- Send the quote",
        ):
            agent.chat(sid, "接下来要做什么", mode="direct")

        assert captured
        flat = "\n".join(str(m.get("content") or "") for m in captured[0])
        assert "Send the quote" in flat
        assert "Alice" in flat
        assert not any(
            m.role == "system"
            for m in store.get_messages(sid, limit=None)
        )


def _fake_completion(content: str):
    mock_resp = MagicMock()
    mock_choice = MagicMock()
    mock_msg = MagicMock()
    mock_msg.content = content
    mock_msg.tool_calls = None
    mock_choice.message = mock_msg
    mock_resp.choices = [mock_choice]
    return mock_resp
