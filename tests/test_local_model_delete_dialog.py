"""Local model Delete must use the in-app Dialog — window.confirm is silent in Tauri."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "frontend" / "src" / "components" / "llm-provider" / "llm-provider-view.tsx"


def test_local_model_delete_uses_dialog_not_window_confirm():
    text = VIEW.read_text(encoding="utf-8")
    assert "confirm(" not in text
    assert "deleteLocalModelsOpen" in text
    assert "pm-dialog-confirm" in text
    assert "Delete local models" in text
