"""Group chat: UI rounds are dialogue turns; transcript lookup is required evidence."""

from __future__ import annotations


def test_ui_turn_count_for_group_uses_dialogue_turns_not_raw_rows():
    from src.chatbox.agent import _ui_turn_count

    class Store:
        def count_dialogue_turns(self, session_id):
            return 2

        def count_messages(self, session_id, exclude_system=True):
            return 24

    assert _ui_turn_count(Store(), "group_abc", fallback=0) == 2
    assert _ui_turn_count(Store(), "meeting_x", fallback=0) == 2


def test_ui_turn_count_main_chat_still_uses_raw_rows():
    from src.chatbox.agent import _ui_turn_count

    class Store:
        def count_dialogue_turns(self, session_id):
            return 2

        def count_messages(self, session_id, exclude_system=True):
            return 24

    assert _ui_turn_count(Store(), "sess_main", fallback=0) == 24


def test_group_tool_choice_forces_transcript_after_summary_only():
    from src.chatbox.agent import _group_stream_tool_choice

    assert _group_stream_tool_choice("group_1", transcript_lookup_done=False, force_transcript_lookup=False) == "auto"
    forced = _group_stream_tool_choice(
        "group_1", transcript_lookup_done=False, force_transcript_lookup=True,
    )
    assert forced["function"]["name"] == "lookup_group_transcript"
    assert _group_stream_tool_choice("group_1", transcript_lookup_done=True, force_transcript_lookup=True) == "auto"
    assert _group_stream_tool_choice("meeting_1", transcript_lookup_done=False, force_transcript_lookup=True) == "auto"


def test_forced_tool_choice_disables_thinking_for_that_round():
    from src.chatbox.agent import (
        _group_stream_tool_choice,
        _thinking_on_for_tool_round,
    )

    forced = _group_stream_tool_choice(
        "group_1", transcript_lookup_done=False, force_transcript_lookup=True,
    )
    assert _thinking_on_for_tool_round(True, forced) is False
    assert _thinking_on_for_tool_round(True, "auto") is True
    assert _thinking_on_for_tool_round(False, forced) is False


def test_retry_kwargs_disable_thinking_then_drop_forced_tool():
    from src.chatbox.agent import _retry_llm_kwargs_after_error

    err = Exception(
        "Error code: 400 - {'error': {'message': 'Thinking mode does not support this tool_choice'}}"
    )
    forced = {
        "type": "function",
        "function": {"name": "lookup_group_transcript"},
    }
    first = _retry_llm_kwargs_after_error(
        {
            "tool_choice": forced,
            "extra_body": {"enable_thinking": True},
        },
        err,
    )
    assert first is not None
    assert first["tool_choice"] == forced
    assert first["extra_body"]["enable_thinking"] is False

    second = _retry_llm_kwargs_after_error(first, err)
    assert second is not None
    assert second["tool_choice"] == "auto"


def test_group_transcript_lookup_is_offloaded_from_the_event_loop():
    """Hybrid retrieve must not stall GET /meetings during Group Chat."""
    import inspect
    import threading

    import asyncio

    from src.chatbox.agent import _run_blocking

    src = inspect.getsource(__import__("src.chatbox.agent", fromlist=["ChatboxAgent"]))
    # chat_stream path (not the sync query fallback)
    assert "await _run_blocking(\n                            execute_group_lookup_json" in src.replace(
        "\r\n", "\n"
    ) or "await _run_blocking(execute_group_lookup_json" in src

    caller = threading.get_ident()

    def job() -> int:
        return threading.get_ident()

    worker = asyncio.run(_run_blocking(job))
    assert worker != caller


def test_group_prompt_treats_summary_as_orientation_not_evidence():
    from src.prompts import MEETING_GROUP_CHAT_SYSTEM_PROMPT
    from src.chatbox.query_tools import LOOKUP_GROUP_TRANSCRIPT_TOOL, READ_MEETING_SUMMARY_TOOL

    p = MEETING_GROUP_CHAT_SYSTEM_PROMPT.lower()
    assert "read_meeting_summary" in p
    assert "lookup_group_transcript" in p
    assert "orientation" in p or "itinerary" in p or "map of the series" in p
    assert "spoken" in p or "transcript" in p
    assert "paraphrase" in p or "synthes" in p
    assert "verbatim" in p or "paste" in p or "quote" in p
    assert "[n:k]" in MEETING_GROUP_CHAT_SYSTEM_PROMPT
    assert "do not invent" in p or "never invent" in p
    # Not a literal dump of the product brief
    assert "我不要看原句" not in MEETING_GROUP_CHAT_SYSTEM_PROMPT

    sdesc = READ_MEETING_SUMMARY_TOOL["function"]["description"].lower()
    tdesc = LOOKUP_GROUP_TRANSCRIPT_TOOL["function"]["description"].lower()
    assert "orient" in sdesc or "itinerary" in sdesc or "map" in sdesc
    assert "evidence" in tdesc or "spoken" in tdesc
