"""Meeting section ## Todo candidate extraction (design 2026-08-12)."""

from __future__ import annotations

import json

from src.meeting.todo_candidates import (
    candidate_id_for,
    extract_todo_candidates,
    extract_todo_candidates_llm,
    parse_section_todo_candidates,
    prepare_section_todo_snapshot,
)


def test_parse_empty_or_missing_todo_section():
    assert parse_section_todo_candidates("") == []
    assert parse_section_todo_candidates("# Title\n\n## Summary\nhello") == []


def test_parse_bullets_with_spk_priority_and_name():
    md = """## Summary
Some overview.

## Todo
- [spk:0] to prepare the Q3 budget report [priority: high]
- Vendor Northline to send revised quotes by Friday [priority: medium]
- [spk:1] circulate notes

## Data & Facts
- revenue 1M
"""
    items = parse_section_todo_candidates(md)
    assert len(items) == 3

    a = items[0]
    assert a["assignee_label"] == "[spk:0]"
    assert a["title"] == "prepare the Q3 budget report"
    assert a["priority"] == "high"
    assert a["body"] is None
    assert "[spk:0]" in a["raw_line"]

    b = items[1]
    assert b["assignee_label"] == "Vendor Northline"
    assert b["title"] == "send revised quotes by Friday"
    assert b["priority"] == "medium"

    c = items[2]
    assert c["assignee_label"] == "[spk:1]"
    assert c["title"] == "circulate notes"
    assert c["priority"] is None
    assert c["ddl"] is None


def test_parse_title_detail_em_dash():
    md = """## Todo
- [spk:0] Prepare Q3 budget report — Include YoY variance and draft for review by Friday [priority: high]
"""
    items = parse_section_todo_candidates(md, speaker_names={"0": "Alex"})
    assert len(items) == 1
    assert items[0]["title"] == "Prepare Q3 budget report"
    assert items[0]["body"] == (
        "Include YoY variance and draft for review by Friday"
    )
    assert items[0]["assignee_label"] == "Alex"
    assert items[0]["priority"] == "high"


def test_parse_long_title_soft_caps_to_body():
    long = (
        "coordinate with finance and ops to finalize the multi-year CAPEX "
        "model including sensitivity cases and board pack appendices"
    )
    md = f"## Todo\n- [spk:1] {long}\n"
    items = parse_section_todo_candidates(md)
    assert len(items) == 1
    title = items[0]["title"]
    body = items[0]["body"] or ""
    assert len(title) <= 72
    assert len(title.split()) <= 12
    assert body
    # Remainder of the long line should land in description
    assert "board pack" in body or "sensitivity" in body


def test_parse_with_speaker_name_map():
    md = """## Todo
- [spk:0] ship docs [priority: low]
"""
    items = parse_section_todo_candidates(md, speaker_names={"0": "Alice"})
    assert len(items) == 1
    assert items[0]["assignee_label"] == "Alice"
    assert items[0]["title"] == "ship docs"
    assert items[0]["priority"] == "low"


def test_candidate_id_stable():
    md = """## Todo
- Alice ship docs [priority: low]
"""
    # Without " to " split, whole line is title when no spk
    items = parse_section_todo_candidates(
        """## Todo
- Alice to ship docs [priority: low]
"""
    )
    a = items[0]
    b = parse_section_todo_candidates(
        """## Todo
- Alice to ship docs [priority: low]
"""
    )[0]
    assert a["candidate_id"] == b["candidate_id"]
    assert a["assignee_label"] == "Alice"
    assert a["title"] == "ship docs"
    assert a["candidate_id"] == candidate_id_for(
        a["title"], a.get("assignee_label"), "low"
    )


def test_stops_at_next_h2():
    md = """## Todo
- task one
## Detail
- not a todo
"""
    items = parse_section_todo_candidates(md)
    assert len(items) == 1
    assert items[0]["title"] == "task one"


def test_parse_heading_variants():
    for heading in ("## Todos", "## 待办", "## Action Items"):
        md = f"{heading}\n- ship docs\n"
        items = parse_section_todo_candidates(md)
        assert len(items) == 1, heading
        assert items[0]["title"] == "ship docs"


def test_prepare_snapshot_resolves_speakers_and_strips_stt():
    md = """## Summary
[spk:0] agreed. [stt_0012]
Speaker 1 will follow up stt_0099.

## Todo
- [spk:0] Ship docs [priority: low]
"""
    snap = prepare_section_todo_snapshot(md, {"0": "Alice", "1": "Bob"})
    assert "[spk:0]" not in snap
    assert "Alice" in snap
    assert "Bob will follow up" in snap
    assert "stt_" not in snap
    assert "[stt_" not in snap


def test_extract_todo_candidates_llm_parses_json():
    class _FakeLLM:
        def generate(self, prompt, system="", **kwargs):
            return json.dumps(
                {
                    "items": [
                        {
                            "title": "Alice: prepare Q3 budget",
                            "body": "Include YoY variance",
                            "assignee_label": "Alice",
                            "priority": "high",
                            "ddl": "2026-08-15",
                        },
                        {
                            "title": "Circulate notes",
                            "body": None,
                            "priority": None,
                            "ddl": None,
                        },
                    ]
                }
            )

    items = extract_todo_candidates_llm(
        "## Todo\n- Alice prepare Q3 budget",
        meeting_created_at="2026-08-10T10:00:00+00:00",
        llm=_FakeLLM(),
    )
    assert len(items) == 2
    assert items[0]["title"] == "Alice: prepare Q3 budget"
    assert items[0]["assignee_label"] == "Alice"
    assert items[0]["body"] == "Include YoY variance"
    assert items[0]["priority"] == "high"
    assert items[0]["ddl"] == "2026-08-15"
    assert items[1]["title"] == "Circulate notes"
    assert items[1]["ddl"] is None


def test_extract_todo_candidates_llm_keeps_assignee_name():
    """If model only sets assignee_label, fold name into title (do not drop)."""

    class _FakeLLM:
        def generate(self, prompt, system="", **kwargs):
            return json.dumps(
                {
                    "items": [
                        {
                            "title": "Prepare Q3 budget",
                            "body": "Include YoY variance",
                            "assignee_label": "Alice",
                            "priority": "high",
                            "ddl": None,
                        }
                    ]
                }
            )

    items = extract_todo_candidates_llm(
        "## Todo\n- Alice prepare Q3 budget",
        meeting_created_at="2026-08-10T10:00:00+00:00",
        llm=_FakeLLM(),
    )
    assert len(items) == 1
    assert "Alice" in items[0]["title"]
    assert items[0]["assignee_label"] == "Alice"


def test_extract_todo_candidates_llm_disables_thinking():
    """Structured extract must force thinking=False + max_tokens (not meeting_thinking)."""
    seen: dict = {}

    class _FakeLLM:
        def generate(self, prompt, system="", **kwargs):
            seen.update(kwargs)
            return json.dumps({"items": [{"title": "Ship docs", "body": None}]})

    extract_todo_candidates_llm(
        "## Todo\n- Ship docs",
        meeting_created_at="2026-08-10T10:00:00+00:00",
        llm=_FakeLLM(),
    )
    assert seen.get("thinking") is False
    assert seen.get("max_tokens") == 2048
    assert seen.get("response_format") == {"type": "json_object"}


def test_extract_todo_candidates_falls_back_to_parse_without_llm():
    md = """## Todo
- Alice to ship docs [priority: low]
"""
    items = extract_todo_candidates(md, use_llm=True, llm=None)
    assert len(items) == 1
    assert items[0]["title"] == "ship docs"
    assert items[0]["priority"] == "low"


def test_mark_todo_candidates_created_writes_back():
    """Creating a checklist todo should stamp created_todo_id on the candidate."""
    import shutil
    from pathlib import Path

    from src.meeting import store as meeting_store
    from src.meeting.service import meeting_service

    mid = "meet_mark_cand_1"
    # Clean leftover if any
    root = Path("data/meetings") / mid
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)

    m = meeting_store.create_meeting(title="Mark cand test")
    mid = m.id
    try:
        tabs = [
            {
                "tab_id": "tab_sec",
                "type": "section",
                "name": "Sec",
                "description": "",
                "processing_state": "idle",
                "associated_collection_id": "col1",
                "associated_collection_name": "C",
                "allocated_file_id": "f1",
                "todo_candidates": [
                    {
                        "candidate_id": "cid_aaa",
                        "title": "do thing",
                        "priority": None,
                        "ddl": None,
                        "created_todo_id": None,
                    }
                ],
            }
        ]
        meeting_store.update_meeting(mid, tabs=tabs)
        out = meeting_service.mark_todo_candidates_created(
            mid,
            [
                {
                    "tab_id": "tab_sec",
                    "candidate_id": "cid_aaa",
                    "todo_id": "todo_xyz",
                }
            ],
        )
        sec = next(t for t in out.tabs if (t if isinstance(t, dict) else t.model_dump())["tab_id"] == "tab_sec")
        sec_d = sec if isinstance(sec, dict) else sec.model_dump()
        assert sec_d["todo_candidates"][0]["created_todo_id"] == "todo_xyz"
    finally:
        try:
            meeting_store.delete_meeting(mid)
        except Exception:
            pass
