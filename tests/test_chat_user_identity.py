"""User identity (People 'Me') injection into chat context."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _patch_me(pid: str | None, name: str | None):
    """Patch the speakers store so Me resolves to pid/display_name."""
    person = None
    if pid:
        person = MagicMock()
        person.display_name = name or ""
    return (
        patch("src.speakers.store.get_me_person_id", return_value=pid),
        patch("src.speakers.store.get_person", return_value=person),
    )


def test_identity_line_present_when_me_set():
    from src.chatbox.agent import _build_user_identity_line

    p1, p2 = _patch_me("p1", "Alice")
    with p1, p2:
        line = _build_user_identity_line()
    assert line.startswith("Current user: Alice")


def test_identity_line_empty_when_me_unset():
    from src.chatbox.agent import _build_user_identity_line

    p1, p2 = _patch_me(None, None)
    with p1, p2:
        assert _build_user_identity_line() == ""


def test_surface_context_prepends_identity_for_quick_chat():
    from src.chatbox.agent import _build_surface_context

    p1, p2 = _patch_me("p1", "Alice")
    with (
        p1,
        p2,
        patch("src.meeting.store.list_meetings", return_value=[]),
    ):
        ctx = _build_surface_context("sess_chat", ["col_a"])
    assert ctx is not None
    assert ctx.startswith("Current user: Alice")


def test_meeting_speaker_mapping_marks_me_with_you():
    from src.chatbox.meeting_context import build_meeting_ephemeral_context

    meeting = MagicMock()
    meeting.speaker_names = {"0": "Alice", "1": "Bob"}
    meeting.speaker_people = {"0": "p_me", "1": "p_other"}
    with (
        patch("src.meeting.store.get_meeting", return_value=meeting),
        patch("src.meeting.store.get_section_md", return_value=""),
        patch("src.speakers.service.rebuild_speaker_names", return_value={"0": "Alice", "1": "Bob"}),
        patch("src.speakers.store.get_me_person_id", return_value="p_me"),
    ):
        ctx = build_meeting_ephemeral_context("mid1")
    assert "0: Alice (you)" in ctx
    assert "1: Bob (you)" not in ctx


def test_meeting_speaker_mapping_without_me_binding_has_no_marker():
    from src.chatbox.meeting_context import build_meeting_ephemeral_context

    meeting = MagicMock()
    meeting.speaker_names = {"0": "Alice"}
    meeting.speaker_people = {"0": "p_other"}
    with (
        patch("src.meeting.store.get_meeting", return_value=meeting),
        patch("src.meeting.store.get_section_md", return_value=""),
        patch("src.speakers.service.rebuild_speaker_names", return_value={"0": "Alice"}),
        patch("src.speakers.store.get_me_person_id", return_value=None),
    ):
        ctx = build_meeting_ephemeral_context("mid1")
    assert "(you)" not in ctx


def test_quick_chat_messages_carry_identity_line(tmp_path):
    """End to end: the LLM messages include the identity line on a QC turn."""
    from src.chatbox.agent import ChatboxAgent
    from src.db.sessions import SessionStore

    store = SessionStore(str(tmp_path / "ident.db"))
    store.create_session(title="qc", session_id="quick_col1")
    llm = MagicMock()
    llm._model = "test-model"
    llm._client = MagicMock()
    llm._client.chat.completions.create.return_value = _text_only_response("hi")
    agent = ChatboxAgent(store, llm, MagicMock())

    p1, p2 = _patch_me("p1", "Alice")
    with p1, p2, patch("src.meeting.store.list_meetings", return_value=[]):
        agent.chat("quick_col1", "hello", mode="direct")

    kwargs = llm._client.chat.completions.create.call_args_list[0][1]
    system_msgs = [
        m["content"] for m in kwargs["messages"] if m.get("role") == "system"
    ]
    assert any("Current user: Alice" in (c or "") for c in system_msgs)


def _text_only_response(content: str):
    resp = MagicMock()
    choice = MagicMock()
    choice.message.content = content
    choice.message.tool_calls = None
    resp.choices = [choice]
    return resp
