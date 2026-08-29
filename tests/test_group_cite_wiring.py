"""Group cite chips stamp occurrence at render and persist cites on history."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QC = ROOT / "frontend" / "src" / "components" / "meeting" / "meeting-quick-chat.tsx"
AGENT = ROOT / "src" / "chatbox" / "agent.py"


def test_group_cite_occurrence_is_stamped_when_chip_is_created():
    src = QC.read_text(encoding="utf-8")
    assert "nextGroupCiteOccurrence" in src
    assert "wrapCite" not in src
    assert "groupCites" in src
    assert "groupCitesFromToolTrace" in src


def test_group_cite_chip_requires_sentence_id():
    src = QC.read_text(encoding="utf-8")
    assert "resolveGroupCite" in src
    assert "GROUP_CITE_RE_SOURCE" in src
    assert "sentence_id" in src
    assert "{match[0]}" not in src


def test_lookup_meeting_transcript_cites_saved_on_tool_trace():
    src = AGENT.read_text(encoding="utf-8")
    assert '_trace_entry["cites"]' in src
    assert '_tr["cites"]' in src


def test_group_cite_chips_use_sequential_display_index():
    src = QC.read_text(encoding="utf-8")
    assert "displayedGroupCites" in src
    assert "displayIndex" in src
    assert "GROUP_CITE_HOVER_DELAY_MS" in src
    assert "TooltipProvider" in src
    assert "pm-qc-sources" in src
    assert "pm-qc-source-item" in src
    assert "citeMeetings" in src or "enrichGroupCites" in src
