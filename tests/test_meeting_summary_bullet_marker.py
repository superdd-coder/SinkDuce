"""Meeting summary markdown pins "- " as the bullet marker end to end.

The default summary model drifted to `*` bullets; the single-star emphasis
pass in frontend/src/lib/md-emphasis.ts then paired adjacent list markers
across newlines as italics, shredding Data & Facts / Todo rendering with
literal `*` and merged items. These guards pin all three layers: prompt
instruction, view-prep normalization, and the emphasis regex itself.
"""

from __future__ import annotations


def test_summary_prompts_pin_hyphen_bullet_marker():
    import src.prompts as prompts

    for text in (
        prompts.MEETING_GENERAL_SUMMARY_PROMPT,
        prompts.MEETING_SUMMARIZER_V3_PROMPT,
    ):
        assert 'always use "- " (hyphen + space)' in text


def test_frontend_view_prep_normalizes_star_bullets_before_emphasis_pass():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    marks = (
        root / "frontend/src/components/meeting/meeting-summary-marks.ts"
    ).read_text(encoding="utf-8")
    # Bullet normalization must run before the emphasis trim call
    # (first non-import occurrence), so emphasis never sees list markers.
    norm_at = marks.find(r"^([ \t]*)\*[ \t]+")
    trim_at = marks.find("s = trimEmphasisInteriorSpaces(s)")
    assert 0 <= norm_at < trim_at


def test_emphasis_trim_never_crosses_newlines():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    emphasis = (
        root / "frontend/src/lib/md-emphasis.ts"
    ).read_text(encoding="utf-8")
    assert emphasis.count(r"[^*\n]+") == 2
