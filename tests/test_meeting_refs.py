"""Meeting transcript-ref helpers live in meeting.refs, not a god service."""

from __future__ import annotations


def test_num_to_stt():
    from src.meeting.refs import num_to_stt

    assert num_to_stt(1) == "stt_0001"
    assert num_to_stt("123") == "stt_0123"


def test_normalize_and_clean_refs():
    from src.meeting.refs import clean_refs, normalize_brackets, normalize_refs

    md = normalize_refs(normalize_brackets("见【67】和 [70]"))
    assert "stt_0067" in md
    assert "stt_0070" in md
    cleaned = clean_refs(md, ["stt_0067"])
    assert "stt_0067" in cleaned
    assert "stt_0070" not in cleaned


def test_parse_tagger_numeric_ids():
    from src.meeting.refs import parse_tagger_response

    out = parse_tagger_response('{"sentence_ids": [1, 2]}')
    assert out["sentence_ids"] == ["stt_0001", "stt_0002"]


def test_legacy_ddl_prompt_aliases_removed():
    import src.prompts as prompts

    assert not hasattr(prompts, "MEETING_TODO_DDL_SYSTEM_PROMPT")
    assert not hasattr(prompts, "MEETING_TODO_DDL_USER_PROMPT")
    assert hasattr(prompts, "MEETING_TODO_EXTRACT_SYSTEM_PROMPT")


def test_sessions_route_does_not_redefine_num_to_stt():
    import src.api.routes.sessions as sessions

    assert not hasattr(sessions, "_num_to_stt")
