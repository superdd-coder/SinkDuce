"""Meeting transcript-ref helpers live in meeting.refs, not a god service."""

from __future__ import annotations


def test_num_to_stt():
    from src.meeting.refs import num_to_stt

    assert num_to_stt(1) == "stt_0001"
    assert num_to_stt("123") == "stt_0123"


def test_normalize_ref_prefix():
    from src.meeting.refs import clean_refs, normalize_brackets, normalize_refs

    md = normalize_refs(normalize_brackets("见【ref:67】和 [ref:70]"))
    assert "stt_0067" in md
    assert "stt_0070" in md
    cleaned = clean_refs(md, ["stt_0067"])
    assert "stt_0067" in cleaned
    assert "stt_0070" not in cleaned


def test_normalize_ref_list_and_range():
    from src.meeting.refs import normalize_refs

    md = normalize_refs("a [ref:67,70] b [ref:12-15]")
    assert md == "a [stt_0067,stt_0070] b [stt_0012-0015]"


def test_bare_number_brackets_are_not_refs():
    from src.meeting.refs import normalize_brackets, normalize_refs

    raw = "附件[1] 方案[2] [20-25]°C 见【3】"
    md = normalize_refs(normalize_brackets(raw))
    assert "stt_" not in md
    assert "附件[1]" in md
    assert "方案[2]" in md
    assert "[20-25]°C" in md
    assert "[3]" in md


def test_parse_tagger_numeric_ids():
    from src.meeting.refs import parse_tagger_response

    out = parse_tagger_response('{"sentence_ids": [1, 2]}')
    assert out["sentence_ids"] == ["stt_0001", "stt_0002"]


def test_strip_speaker_name_glosses():
    from src.meeting.refs import strip_speaker_name_glosses

    md = (
        "[spk:2] (Jethro) reported high OPEX. "
        "[spk:3]（Ray）explained feed pressure. "
        "[spk:4] (Herman) confirmed 1.2 kWh. "
        "The offer is RM2.30+ (excluding VAT)."
    )
    out = strip_speaker_name_glosses(md)
    assert "[spk:2] reported high OPEX" in out
    assert "[spk:3]explained feed pressure" in out or "[spk:3] explained" in out
    assert "[spk:4] confirmed 1.2 kWh" in out
    assert "(Jethro)" not in out
    assert "（Ray）" not in out
    assert "(Herman)" not in out
    assert "(excluding VAT)" in out
    assert "[spk:2]" in out
    assert "[spk:3]" in out
    assert "[spk:4]" in out


def test_prompts_forbid_parenthetical_speaker_names():
    import src.prompts as prompts

    for text in (
        prompts.MEETING_GENERAL_SUMMARY_PROMPT,
        prompts.MEETING_SUMMARIZER_V3_PROMPT,
        prompts.MEETING_BLUEPRINT_SYSTEM,
    ):
        assert "[spk:0] (Alex)" in text


def test_prompts_teach_ref_prefix_not_bare_n():
    import src.prompts as prompts

    assert "[ref:67,70]" in prompts.MEETING_GENERAL_SUMMARY_PROMPT
    assert "[ref:N]" in prompts.MEETING_SUMMARIZER_V3_PROMPT
    assert "Cite sentences as [67]" not in prompts.MEETING_BLUEPRINT_SYSTEM
    assert "[ref:67]" in prompts.MEETING_BLUEPRINT_SYSTEM


def test_frontend_view_and_chat_chip_ref_prefix():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    marks = (
        root / "frontend/src/components/meeting/meeting-summary-marks.ts"
    ).read_text(encoding="utf-8")
    chat = (
        root / "frontend/src/components/meeting/meeting-quick-chat.tsx"
    ).read_text(encoding="utf-8")
    assert "parseMeetingRefGroups" in marks
    assert r"[ref:" in marks
    strip_at = marks.find(r"[\(（]")
    if strip_at < 0:
        strip_at = marks.find("[（(]")
    name_at = marks.find("s.replace(/\\[spk:(\\d+)\\]/g")
    assert strip_at >= 0
    assert 0 <= strip_at < name_at
    assert "MEETING_CITE_RE_SOURCE" in chat
    assert "Bare [67] is ordinary text" in chat


def test_legacy_ddl_prompt_aliases_removed():
    import src.prompts as prompts

    assert not hasattr(prompts, "MEETING_TODO_DDL_SYSTEM_PROMPT")
    assert not hasattr(prompts, "MEETING_TODO_DDL_USER_PROMPT")
    assert hasattr(prompts, "MEETING_TODO_EXTRACT_SYSTEM_PROMPT")


def test_sessions_route_does_not_redefine_num_to_stt():
    import src.api.routes.sessions as sessions

    assert not hasattr(sessions, "_num_to_stt")
