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
    assert 'MEETING_MODEL = "qwen3.8-flash"' in dash
    assert 'DEFAULT_MODEL = "qwen3.8-flash"' in dash
    assert 'CHAT_MODEL = "qwen3.8-flash"' in dash
    assert 'LIBRARY_MODEL = "qwen3.8-flash"' in dash
    assert (
        'DASHSCOPE_VISION_AND_TOOLS = ["qwen3.8-flash", "qwen3.7-plus", "qwen3.7-flash"]'
        in dash
    )
    assert "dashscopeCapabilityTags" in dash
    assert "dashscopeIsToolsOnly" in dash
    assert "enrichment_model:" in dash
    assert "agentic_query_model:" in dash
    assert "note_distill_model:" in dash
    assert "live_summary_model:" in dash
    assert 'DEFAULT_MODEL = "qwen/qwen3.8-flash"' in opr
    assert 'CHAT_MODEL = "qwen/qwen3.8-flash"' in opr
    assert 'LIBRARY_MODEL = "qwen/qwen3.8-flash"' in opr
    assert 'MEETING_MODEL = "qwen/qwen3.8-flash"' in opr
    assert (
        'OPENROUTER_VISION_AND_TOOLS = ["qwen/qwen3.8-flash", "qwen/qwen3.7-plus", "qwen/qwen3.7-flash"]'
        in opr
    )
    assert "openrouterCapabilityTags" in opr
    assert "enrichment_model:" in opr
    assert "agentic_query_model:" in opr
    assert "note_distill_model:" in opr
    assert "live_summary_model:" in opr
    assert "meeting_model:" in dash
    assert "meeting_model:" in opr
    assert "applyLlmSlotConfig" in view
    assert "applyOneshotSlots" in view
    assert "refreshAfterOneshot" in view
    assert "onSaved={refreshAfterOneshot}" in view
    assert "oneshotSlotSnapshot" in dash
    assert "oneshotSlotSnapshot" in opr
    assert "onSaved(slots)" in dash
    assert "onSaved(slots)" in opr
    assert "coerceSlotValue(meetingModel" in view
    assert "coerceSlotValue(enrichModel" in view
    assert "coerceSlotValue(agenticQueryModel" in view
    assert "coerceSlotValue(noteDistillModel" in view
    # Slots carry their own i18n-labeled field (never combined "Default / Agentic").
    for label in (
        "common.default",
        "settings.agenticQuery",
        "settings.imageDescription",
        "settings.libraryLlm",
        "settings.noteDistill",
        "settings.meetingSummary",
    ):
        assert f'<FieldLabel>{{t("{label}")}}</FieldLabel>' in dash
        assert f'<FieldLabel>{{t("{label}")}}</FieldLabel>' in opr
    assert "Default / Agentic" not in dash
    assert "Default / Agentic" not in opr
    assert "Image description / Library" not in dash
    assert "Image description / Library" not in opr
    assert "meetingThinking" not in view
    assert "Thinking effort" not in view


def test_meeting_calls_never_think():
    assert _thinking_for_meeting_call("blueprint") is False
    assert _thinking_for_meeting_call("summary") is False
    assert _thinking_for_meeting_call("tagger") is False
    assert _thinking_for_meeting_call("summarizer") is False
    assert _thinking_for_meeting_call("section_desc") is False
    assert _meeting_thinking_effort() is None
