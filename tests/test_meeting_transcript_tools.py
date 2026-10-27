def test_quick_chat_ui_has_index_gate():
    from pathlib import Path

    src = Path("frontend/src/components/meeting/meeting-quick-chat.tsx").read_text(
        encoding="utf-8"
    )
    assert "txIndexBuilding" in src
    assert "startTranscriptIndex" in src
    assert 'indexStatus !== "ready"' in src


def test_meeting_quick_chat_tool_result_does_not_default_zero_sources():
    from pathlib import Path

    src = Path("frontend/src/components/meeting/meeting-quick-chat.tsx").read_text(
        encoding="utf-8"
    )
    assert "sources_count || 0" not in src
    assert "txLookupHits" in src


def test_meeting_quick_chat_exposes_lookup_tool():
    from src.chatbox.query_tools import allowed_tool_names, tools_for_mode

    tools = tools_for_mode("direct", is_meeting=True)
    names = {t["function"]["name"] for t in tools}
    assert "lookup_meeting_transcript" in names
    assert "lookup_collection" not in names
    assert "lookup_meeting_transcript" in allowed_tool_names("direct", is_meeting=True)
