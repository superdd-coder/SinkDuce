"""Hot-words UI must follow the active transcription adapter.

Local ONNX adapters declare supports_hot_words=False. Meeting setup/live
must use the realtime flag (not file OR realtime). Studio and Settings
must also disable the picker when the path-appropriate adapter cannot
apply a vocabulary.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEETING = ROOT / "frontend" / "src" / "components" / "meeting"
SETTINGS = ROOT / "frontend" / "src" / "components" / "llm-provider" / "llm-provider-view.tsx"


def test_setup_hot_words_follow_realtime_not_file_or():
    """Setup/empty recording uses the realtime model — do not OR file support."""
    view = (MEETING / "meeting-view.tsx").read_text(encoding="utf-8")
    assert "fileSupportsHotWords || rtSupportsHotWords" not in view
    assert "rtSupportsHotWords" in view
    assert "fileSupportsHotWords" in view


def test_studio_media_bar_gates_hot_words_on_adapter_flag():
    """Studio player chip must honor the file-model hot-words flag."""
    studio = (MEETING / "meeting-studio-stage.tsx").read_text(encoding="utf-8")
    media = (MEETING / "media-bar.tsx").read_text(encoding="utf-8")
    assert "hotWordsSupported" in studio
    assert "hotWordsSupported" in media
    assert "hotWordsSupported={activeHotWordsSupported}" in studio


def test_studio_player_opens_same_dialogs_as_setup():
    """Player hot-words/language must use the setup Dialogs, not a SoftMenu."""
    media = (MEETING / "media-bar.tsx").read_text(encoding="utf-8")
    assert "SoftMenu" not in media
    assert "<HotWordsSelector" in media
    assert "<LanguageHintsSelector" in media
    assert 'variant="chip"' in media


def test_settings_hot_words_gated_when_local_asr_active():
    """Settings Hot words card must read active-provider supports_hot_words."""
    settings = SETTINGS.read_text(encoding="utf-8")
    assert "getActiveProviderInfo" in settings
    assert "supports_hot_words" in settings
