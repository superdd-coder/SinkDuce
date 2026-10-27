"""Recall empty/results cards sit in a fold clip; shadow must stay inside it."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "frontend" / "src" / "index.css"


def test_card_fold_keeps_shadow_inside_hidden_clip():
    css = CSS.read_text(encoding="utf-8")
    clip = css.split(".pm-recall-fold-clip {", 1)[1].split("}", 1)[0]
    assert "overflow: hidden" in clip

    assert "--pm-recall-shadow-gutter:" in css
    # Card-wrapping folds pad the body so box-shadow is not clipped.
    assert ".pm-recall-fold:has(.pm-recall-card) .pm-recall-fold-body" in css
    gutter_block = css.split(
        ".pm-recall-fold:has(.pm-recall-card) .pm-recall-fold-body", 1
    )[1].split("}", 1)[0]
    assert "padding" in gutter_block
    assert "var(--pm-recall-shadow-gutter)" in gutter_block
