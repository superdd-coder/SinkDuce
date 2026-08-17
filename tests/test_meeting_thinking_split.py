"""Meeting LLM calls do not use thinking; no Settings switch."""

from src.config import EnrichmentConfig
from src.meeting.service import _meeting_thinking_effort, _thinking_for_meeting_call


def test_meeting_thinking_defaults_off():
    assert EnrichmentConfig().meeting_thinking is False


def test_oneshot_sets_meeting_pro():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "frontend/src/components/llm-provider"
    dash = (root / "oneshot-dashscope-dialog.tsx").read_text(encoding="utf-8")
    opr = (root / "oneshot-openrouter-dialog.tsx").read_text(encoding="utf-8")
    view = (root / "llm-provider-view.tsx").read_text(encoding="utf-8")
    assert 'MEETING_MODEL = "deepseek-v4-pro"' in dash
    assert 'MEETING_MODEL = "deepseek/deepseek-v4-pro"' in opr
    assert "meeting_model:" in dash
    assert "meeting_model:" in opr
    assert "meetingThinking" not in view
    assert "Thinking effort" not in view


def test_meeting_calls_never_think():
    assert _thinking_for_meeting_call("blueprint") is False
    assert _thinking_for_meeting_call("summary") is False
    assert _thinking_for_meeting_call("tagger") is False
    assert _thinking_for_meeting_call("summarizer") is False
    assert _thinking_for_meeting_call("section_desc") is False
    assert _meeting_thinking_effort() is None
