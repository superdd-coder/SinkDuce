"""Meeting module comprehensive tests.

Covers: config, models, store, service (transcribe handler + MeetingService),
transcription adapters (factory + DashScope + ABCs), and API route handlers.

Run: python -m pytest tests/test_meeting.py -x -v
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from src.config import AppConfig, TranscriptionConfig, TranscriptionProviderConfig
from src.meeting.models import Meeting, MeetingMode, MeetingStatus, TranscriptionResult, TranscriptSegment


# ── Helpers ───────────────────────────────────────────────────


def _make_meeting(**overrides) -> Meeting:
    defaults = dict(
        id="abc123",
        title="Test Meeting",
        status=MeetingStatus.created,
        created_at=datetime(2026, 6, 3, 10, 0, 0),
        updated_at=datetime(2026, 6, 3, 10, 0, 0),
    )
    defaults.update(overrides)
    return Meeting(**defaults)


def _make_transcript_result() -> TranscriptionResult:
    return TranscriptionResult(
        text="Hello world. How are you?",
        segments=[
            TranscriptSegment(start=0.0, end=1.5, text="Hello world.", speaker_id="spk1"),
            TranscriptSegment(start=1.5, end=3.0, text="How are you?", speaker_id="spk2"),
        ],
    )


def _make_provider_config(adapter: str, is_active: bool = True) -> TranscriptionProviderConfig:
    return TranscriptionProviderConfig(
        id="prov-1", name="Test Provider", adapter=adapter,
        api_key="sk-test", model=None, is_active=is_active,
    )


def _make_task() -> MagicMock:
    task = MagicMock()
    task.id = "task-001"
    task.progress = 0.0
    task.message = ""
    task.filename = "meeting_abc123"
    return task


# ═══════════════════════════════════════════════════════════════
# 1. Config Tests
# ═══════════════════════════════════════════════════════════════


class TestTranscriptionConfig:
    """Transcription config models and active-provider properties."""

    def test_provider_config_defaults(self):
        """TranscriptionProviderConfig has sane defaults."""
        cfg = TranscriptionProviderConfig()
        assert cfg.id == ""
        assert cfg.adapter == ""
        assert cfg.api_key == ""
        assert cfg.model is None
        assert cfg.is_active is False

    def test_provider_config_custom_values(self):
        """TranscriptionProviderConfig accepts custom values."""
        cfg = TranscriptionProviderConfig(
            id="p1", name="DashScope", adapter="dashscope_funasr",
            api_key="sk-abc", model="fun-asr", is_active=True,
        )
        assert cfg.id == "p1"
        assert cfg.adapter == "dashscope_funasr"
        assert cfg.is_active is True

    def test_transcription_config_defaults(self):
        """TranscriptionConfig has empty provider lists by default."""
        cfg = TranscriptionConfig()
        assert cfg.file_providers == []
        assert cfg.realtime_providers == []

    def test_active_file_provider_returns_first_active(self):
        """active_file_provider returns the first provider with is_active=True."""
        p1 = _make_provider_config("dashscope_funasr", is_active=False)
        p2 = _make_provider_config("dashscope_funasr", is_active=True)
        cfg = TranscriptionConfig(file_providers=[p1, p2])
        assert cfg.active_file_provider is p2

    def test_active_file_provider_none_when_no_active(self):
        """active_file_provider returns None when no provider is active."""
        p1 = _make_provider_config("dashscope_funasr", is_active=False)
        cfg = TranscriptionConfig(file_providers=[p1])
        assert cfg.active_file_provider is None

    def test_active_file_provider_none_when_empty(self):
        """active_file_provider returns None when list is empty."""
        cfg = TranscriptionConfig(file_providers=[])
        assert cfg.active_file_provider is None

    def test_active_realtime_provider_returns_first_active(self):
        """active_realtime_provider returns the first active realtime provider."""
        p1 = _make_provider_config("dashscope_funasr_realtime", is_active=False)
        p2 = _make_provider_config("dashscope_funasr_realtime", is_active=True)
        cfg = TranscriptionConfig(realtime_providers=[p1, p2])
        assert cfg.active_realtime_provider is p2

    def test_active_realtime_provider_none_when_empty(self):
        """active_realtime_provider returns None when list is empty."""
        cfg = TranscriptionConfig(realtime_providers=[])
        assert cfg.active_realtime_provider is None

    def test_app_config_has_transcription(self):
        """AppConfig includes transcription field with default empty config."""
        cfg = AppConfig()
        assert isinstance(cfg.transcription, TranscriptionConfig)
        assert cfg.transcription.file_providers == []


# ═══════════════════════════════════════════════════════════════
# 2. Model Tests
# ═══════════════════════════════════════════════════════════════


class TestMeetingModels:
    """Pydantic models: construction, defaults, serialization."""

    def test_meeting_status_enum_values(self):
        """MeetingStatus has all expected values."""
        assert MeetingStatus.created == "created"
        assert MeetingStatus.recording == "recording"
        assert MeetingStatus.transcribing == "transcribing"
        assert MeetingStatus.completed == "completed"

    def test_meeting_mode_enum_values(self):
        """MeetingMode has upload and record."""
        assert MeetingMode.upload == "upload"
        assert MeetingMode.record == "record"

    def test_transcript_segment_with_speaker(self):
        """TranscriptSegment stores text, timestamps, and speaker_id."""
        seg = TranscriptSegment(start=1.0, end=2.5, text="Hello", speaker_id="spk1")
        assert seg.start == 1.0
        assert seg.end == 2.5
        assert seg.text == "Hello"
        assert seg.speaker_id == "spk1"

    def test_transcript_segment_without_speaker(self):
        """TranscriptSegment defaults speaker_id to None."""
        seg = TranscriptSegment(start=0.0, end=1.0, text="Hi")
        assert seg.speaker_id is None

    def test_transcription_result_round_trip(self):
        """TranscriptionResult survives model_dump and reconstruction."""
        result = _make_transcript_result()
        data = result.model_dump()
        restored = TranscriptionResult(**data)
        assert restored.text == result.text
        assert len(restored.segments) == 2
        assert restored.segments[0].speaker_id == "spk1"

    def test_meeting_defaults(self):
        """Meeting has correct default values."""
        m = Meeting()
        assert m.id == ""
        assert m.title == ""
        assert m.status == MeetingStatus.created
        assert m.mode is None
        assert m.audio_path is None
        assert m.detail is None
        assert m.allocated_collections == []
        assert isinstance(m.created_at, datetime)

    def test_meeting_model_dump(self):
        """Meeting model_dump includes all expected keys."""
        m = _make_meeting(title="Weekly Standup")
        data = m.model_dump()
        expected_keys = {
            "id", "title", "status", "mode", "audio_path", "notes_path",
            "transcript_path", "detail", "summary",
            "allocated_collections", "allocated_file_ids",
            "created_at", "updated_at",
        }
        assert expected_keys.issubset(set(data.keys()))
        assert data["title"] == "Weekly Standup"


# ═══════════════════════════════════════════════════════════════
# 3. Store Tests
# ═══════════════════════════════════════════════════════════════


class TestMeetingStore:
    """File-based meeting CRUD and file operations."""

    @pytest.fixture(autouse=True)
    def _patch_meetings_dir(self, tmp_path):
        """Redirect MEETINGS_DIR to a temporary directory."""
        self._meetings_dir = tmp_path / "meetings"
        with patch("src.meeting.store.MEETINGS_DIR", self._meetings_dir):
            yield

    def test_create_and_get_meeting(self):
        """create_meeting writes meta.json; get_meeting reads it back."""
        from src.meeting.store import create_meeting, get_meeting

        meeting = create_meeting("Team Sync")
        assert meeting.title == "Team Sync"
        assert meeting.id  # auto-generated UUID
        assert meeting.status == MeetingStatus.created

        fetched = get_meeting(meeting.id)
        assert fetched is not None
        assert fetched.title == "Team Sync"
        assert fetched.id == meeting.id

    def test_create_meeting_with_mode(self):
        """create_meeting accepts an optional MeetingMode."""
        from src.meeting.store import create_meeting, get_meeting

        meeting = create_meeting("Upload Session", mode=MeetingMode.upload)
        fetched = get_meeting(meeting.id)
        assert fetched is not None
        assert fetched.mode == MeetingMode.upload

    def test_get_meeting_missing_returns_none(self):
        """get_meeting returns None for a non-existent ID."""
        from src.meeting.store import get_meeting

        assert get_meeting("nonexistent") is None

    def test_list_meetings_empty(self):
        """list_meetings returns empty list when no meetings exist."""
        from src.meeting.store import list_meetings

        assert list_meetings() == []

    def test_list_meetings_sorted_by_updated_at(self):
        """list_meetings returns meetings sorted by updated_at descending."""
        from src.meeting.store import create_meeting, list_meetings

        m1 = create_meeting("First")
        m2 = create_meeting("Second")
        # m2 was created after m1 so it should appear first
        meetings = list_meetings()
        assert len(meetings) == 2
        assert meetings[0].id == m2.id
        assert meetings[1].id == m1.id

    def test_list_meetings_before_dir_exists(self):
        """list_meetings returns empty list if the directory doesn't exist."""
        import shutil
        from src.meeting.store import list_meetings

        # The autouse fixture creates the dir; remove it to simulate non-existence
        if self._meetings_dir.exists():
            shutil.rmtree(self._meetings_dir)
        assert list_meetings() == []

    def test_update_meeting(self):
        """update_meeting modifies fields and sets updated_at."""
        from src.meeting.store import create_meeting, update_meeting, get_meeting

        meeting = create_meeting("Original")
        updated = update_meeting(meeting.id, title="Renamed", detail="Some detail")
        assert updated.title == "Renamed"
        assert updated.detail == "Some detail"

        fetched = get_meeting(meeting.id)
        assert fetched.title == "Renamed"

    def test_update_meeting_missing_raises(self):
        """update_meeting raises FileNotFoundError for missing meeting."""
        from src.meeting.store import update_meeting

        with pytest.raises(FileNotFoundError):
            update_meeting("nope", title="X")

    def test_delete_meeting(self):
        """delete_meeting removes the meeting directory."""
        from src.meeting.store import create_meeting, delete_meeting, get_meeting

        meeting = create_meeting("To Delete")
        assert delete_meeting(meeting.id) is True
        assert get_meeting(meeting.id) is None

    def test_delete_meeting_missing_returns_false(self):
        """delete_meeting returns False for non-existent meeting."""
        from src.meeting.store import delete_meeting

        assert delete_meeting("nope") is False

    def test_save_audio(self):
        """save_audio writes audio file and updates meeting.audio_path."""
        from src.meeting.store import create_meeting, save_audio, get_meeting

        meeting = create_meeting("Audio Test")
        path = save_audio(meeting.id, b"\x00\x01\x02", "webm")
        assert path.endswith("audio.webm")
        assert Path(path).exists()

        fetched = get_meeting(meeting.id)
        assert fetched.audio_path == path

    def test_save_audio_missing_meeting_raises(self):
        """save_audio raises FileNotFoundError for missing meeting."""
        from src.meeting.store import save_audio

        with pytest.raises(FileNotFoundError):
            save_audio("nope", b"data", "wav")

    def test_save_notes_and_get_notes(self):
        """save_notes writes markdown file; get_notes reads it back."""
        from src.meeting.store import create_meeting, save_notes, get_notes

        meeting = create_meeting("Notes Test")
        save_notes(meeting.id, "# Meeting Notes\n- Item 1")

        content = get_notes(meeting.id)
        assert content is not None
        assert "Meeting Notes" in content

    def test_get_notes_missing_returns_none(self):
        """get_notes returns None when no notes exist."""
        from src.meeting.store import create_meeting, get_notes

        meeting = create_meeting("No Notes")
        assert get_notes(meeting.id) is None

    def test_get_notes_missing_meeting_returns_none(self):
        """get_notes returns None for non-existent meeting."""
        from src.meeting.store import get_notes

        assert get_notes("nope") is None

    def test_save_transcript_and_get_transcript(self):
        """save_transcript writes JSON; get_transcript reads it back."""
        from src.meeting.store import create_meeting, save_transcript, get_transcript

        meeting = create_meeting("Transcript Test")
        result = _make_transcript_result()
        save_transcript(meeting.id, result)

        fetched = get_transcript(meeting.id)
        assert fetched is not None
        assert fetched.text == "Hello world. How are you?"
        assert len(fetched.segments) == 2

    def test_get_transcript_missing_returns_none(self):
        """get_transcript returns None when no transcript exists."""
        from src.meeting.store import create_meeting, get_transcript

        meeting = create_meeting("No Transcript")
        assert get_transcript(meeting.id) is None

    def test_save_notes_missing_meeting_raises(self):
        """save_notes raises FileNotFoundError for missing meeting."""
        from src.meeting.store import save_notes

        with pytest.raises(FileNotFoundError):
            save_notes("nope", "content")

    def test_save_transcript_missing_meeting_raises(self):
        """save_transcript raises FileNotFoundError for missing meeting."""
        from src.meeting.store import save_transcript

        with pytest.raises(FileNotFoundError):
            save_transcript("nope", _make_transcript_result())


# ═══════════════════════════════════════════════════════════════
# 4. Summary Parser Tests
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# 5. Transcribe Handler Tests
# ═══════════════════════════════════════════════════════════════


class TestTranscribeHandler:
    """Async task handler for file transcription."""

    def test_happy_path(self):
        """transcribe_handler returns result dict on success."""
        from src.meeting.service import transcribe_handler

        meeting = _make_meeting(audio_path="/tmp/audio.webm")
        provider = MagicMock()
        provider.transcribe = AsyncMock(return_value=_make_transcript_result())
        config = AppConfig()
        config.transcription.file_providers.append(
            _make_provider_config("dashscope_funasr", is_active=True)
        )

        with patch("src.meeting.service.store") as mock_store, \
             patch("src.meeting.service.get_config", return_value=config), \
             patch("src.meeting.service.create_file_transcription_provider", return_value=provider):
            mock_store.get_meeting.return_value = meeting
            mock_store.update_meeting.return_value = meeting
            mock_store.save_transcript.return_value = "/tmp/transcript.json"

            task = _make_task()
            # Run the async handler
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                transcribe_handler(task, "abc123")
            )

        assert result["message"] == "Transcription complete"
        assert result["meeting_id"] == "abc123"
        assert result["segments"] == 2
        mock_store.save_transcript.assert_called_once()

    def test_meeting_not_found_raises(self):
        """transcribe_handler raises FileNotFoundError for missing meeting."""
        from src.meeting.service import transcribe_handler

        with patch("src.meeting.service.store") as mock_store:
            mock_store.get_meeting.return_value = None
            task = _make_task()

            import asyncio
            with pytest.raises(FileNotFoundError):
                asyncio.get_event_loop().run_until_complete(
                    transcribe_handler(task, "missing")
                )

    def test_no_audio_path_raises(self):
        """transcribe_handler raises ValueError when meeting has no audio."""
        from src.meeting.service import transcribe_handler

        meeting = _make_meeting(audio_path=None)
        with patch("src.meeting.service.store") as mock_store:
            mock_store.get_meeting.return_value = meeting
            task = _make_task()

            import asyncio
            with pytest.raises(ValueError, match="no audio file"):
                asyncio.get_event_loop().run_until_complete(
                    transcribe_handler(task, "abc123")
                )

    def test_fallback_to_local_provider(self):
        """transcribe_handler falls back to built-in local provider when none configured."""
        from src.meeting.service import transcribe_handler

        meeting = _make_meeting(audio_path="/tmp/audio.webm")
        provider = MagicMock()
        provider.transcribe = AsyncMock(return_value=_make_transcript_result())
        config = AppConfig()  # empty transcription config

        with patch("src.meeting.service.store") as mock_store, \
             patch("src.meeting.service.get_config", return_value=config), \
             patch("src.meeting.service.create_file_transcription_provider", return_value=provider):
            mock_store.get_meeting.return_value = meeting
            mock_store.update_meeting.return_value = meeting
            mock_store.save_transcript.return_value = "/tmp/transcript.json"
            task = _make_task()

            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                transcribe_handler(task, "abc123")
            )

        assert result["message"] == "Transcription complete"


# ═══════════════════════════════════════════════════════════════
# 6. MeetingService Tests
# ═══════════════════════════════════════════════════════════════


class TestMeetingService:
    """MeetingService: provider accessors, summary generation, allocation."""

    def test_get_active_file_provider(self):
        """Returns provider when active file provider is configured."""
        from src.meeting.service import MeetingService

        config = AppConfig()
        config.transcription.file_providers.append(
            _make_provider_config("dashscope_funasr", is_active=True)
        )

        with patch("src.meeting.service.get_config", return_value=config), \
             patch("src.meeting.service.create_file_transcription_provider") as mock_create, \
             patch("src.meeting.service.cached_provider", side_effect=lambda key, factory: factory()):
            mock_create.return_value = MagicMock()
            svc = MeetingService()
            provider = svc.get_active_file_provider()

        assert provider is not None
        mock_create.assert_called_once()

    def test_get_active_file_provider_fallback_to_local(self):
        """Returns local provider when no active file provider is configured."""
        from src.meeting.service import MeetingService

        config = AppConfig()
        with patch("src.meeting.service.get_config", return_value=config):
            svc = MeetingService()
            # System always falls back to built-in local FunASR provider
            provider = svc.get_active_file_provider()
            assert provider is not None

    def test_get_active_realtime_provider(self):
        """Returns provider when active realtime provider is configured."""
        from src.meeting.service import MeetingService

        config = AppConfig()
        config.transcription.realtime_providers.append(
            _make_provider_config("dashscope_funasr_realtime", is_active=True)
        )

        with patch("src.meeting.service.get_config", return_value=config), \
             patch("src.meeting.service.create_realtime_transcription_provider") as mock_create:
            mock_create.return_value = MagicMock()
            svc = MeetingService()
            provider = svc.get_active_realtime_provider()

        assert provider is not None

    def test_get_active_realtime_provider_fallback_to_local(self):
        """Returns local realtime provider when no active realtime provider is configured."""
        from src.meeting.service import MeetingService

        config = AppConfig()
        with patch("src.meeting.service.get_config", return_value=config):
            svc = MeetingService()
            # System always falls back to built-in local realtime provider
            provider = svc.get_active_realtime_provider()
            assert provider is not None

    @pytest.mark.skip(reason="v3: generate_summary replaced by _do_blueprint_summary (async task-based)")
    def test_generate_summary(self):
        """v2 test — skipped: generate_summary is now async task-based."""
        pass

    def test_generate_summary_meeting_not_found(self):
        """generate_summary route returns error for missing meeting (v3)."""
        from src.meeting.routes import generate_summary

        with patch("src.meeting.routes.store") as mock_store:
            mock_store.get_meeting.return_value = None
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                generate_summary("missing")
            )
        assert "error" in result

    @pytest.mark.skip(reason="v3: allocate_to_collection replaced by allocate_section_to_collection(meeting_id, tab_id, collection_id)")
    def test_allocate_to_collection(self):
        """v2 test — skipped: allocate_to_collection now requires tab_id parameter."""
        pass

    @pytest.mark.skip(reason="v3: allocate_to_collection replaced by allocate_section_to_collection")
    def test_allocate_deletes_old_allocation(self):
        """v2 test — skipped: v3 idempotency logic tested via allocate_section_to_collection."""
        pass

    def test_allocate_meeting_not_found(self):
        """allocate_section_to_collection raises FileNotFoundError for missing meeting (v3)."""
        from src.meeting.service import MeetingService

        with patch("src.meeting.service.store") as mock_store:
            mock_store.get_meeting.return_value = None
            svc = MeetingService()

            import asyncio
            with pytest.raises(FileNotFoundError):
                asyncio.get_event_loop().run_until_complete(
                    svc.allocate_section_to_collection("missing", "tab_01", "col")
                )


# ═══════════════════════════════════════════════════════════════
# 7. Transcription Factory Tests
# ═══════════════════════════════════════════════════════════════


class TestTranscriptionFactory:
    """Factory functions for creating transcription providers."""

    def test_create_file_provider_dashscope(self):
        """create_file_transcription_provider returns DashScopeFileTranscription for dashscope_funasr."""
        from src.meeting.transcription import create_file_transcription_provider
        from src.meeting.transcription.dashscope_file import DashScopeFileTranscription

        cfg = _make_provider_config("dashscope_funasr")
        with patch("src.meeting.transcription.dashscope_file._HAS_DASHSCOPE", True):
            provider = create_file_transcription_provider(cfg)
        assert isinstance(provider, DashScopeFileTranscription)

    def test_create_file_provider_unknown_raises(self):
        """create_file_transcription_provider raises ValueError for unknown adapter."""
        from src.meeting.transcription import create_file_transcription_provider

        cfg = _make_provider_config("unknown_adapter")
        with pytest.raises(ValueError, match="Unknown file_transcription provider"):
            create_file_transcription_provider(cfg)

    def test_create_realtime_provider_dashscope(self):
        """create_realtime_transcription_provider returns DashScopeRealtimeTranscription."""
        from src.meeting.transcription import create_realtime_transcription_provider
        from src.meeting.transcription.dashscope_realtime import DashScopeRealtimeTranscription

        cfg = _make_provider_config("dashscope_funasr_realtime")
        with patch("src.meeting.transcription.dashscope_realtime._HAS_DASHSCOPE", True):
            provider = create_realtime_transcription_provider(cfg)
        assert isinstance(provider, DashScopeRealtimeTranscription)

    def test_create_realtime_provider_unknown_raises(self):
        """create_realtime_transcription_provider raises ValueError for unknown adapter."""
        from src.meeting.transcription import create_realtime_transcription_provider

        cfg = _make_provider_config("unknown")
        with pytest.raises(ValueError, match="Unknown realtime_transcription provider"):
            create_realtime_transcription_provider(cfg)


# ═══════════════════════════════════════════════════════════════
# 8. DashScope Transcription Tests
# ═══════════════════════════════════════════════════════════════


class TestDashScopeTranscription:
    """DashScope file and realtime transcription implementations."""

    def test_file_transcription_default_model(self):
        """DashScopeFileTranscription uses 'fun-asr' as default model."""
        from src.meeting.transcription.dashscope_file import DashScopeFileTranscription

        with patch("src.meeting.transcription.dashscope_file._HAS_DASHSCOPE", True):
            cfg = TranscriptionProviderConfig(api_key="sk-test")
            provider = DashScopeFileTranscription(cfg)
            assert provider._model == "fun-asr"

    def test_file_transcription_custom_model(self):
        """DashScopeFileTranscription uses custom model when provided."""
        from src.meeting.transcription.dashscope_file import DashScopeFileTranscription

        with patch("src.meeting.transcription.dashscope_file._HAS_DASHSCOPE", True):
            cfg = TranscriptionProviderConfig(api_key="sk-test", model="custom-model")
            provider = DashScopeFileTranscription(cfg)
            assert provider._model == "custom-model"

    def test_realtime_transcription_default_model(self):
        """DashScopeRealtimeTranscription uses 'fun-asr-realtime' as default model."""
        from src.meeting.transcription.dashscope_realtime import DashScopeRealtimeTranscription

        with patch("src.meeting.transcription.dashscope_realtime._HAS_DASHSCOPE", True):
            cfg = TranscriptionProviderConfig(api_key="sk-test")
            provider = DashScopeRealtimeTranscription(cfg)
            assert provider._model == "fun-asr-realtime"

    def test_realtime_send_frame_before_start_raises(self):
        """send_frame raises RuntimeError when session not started."""
        from src.meeting.transcription.dashscope_realtime import DashScopeRealtimeTranscription

        with patch("src.meeting.transcription.dashscope_realtime._HAS_DASHSCOPE", True):
            cfg = TranscriptionProviderConfig(api_key="sk-test")
            provider = DashScopeRealtimeTranscription(cfg)

            import asyncio
            with pytest.raises(RuntimeError, match="not started"):
                asyncio.get_event_loop().run_until_complete(
                    provider.send_frame(b"\x00\x00")
                )

    def test_fetch_and_parse_segments(self):
        """_fetch_and_parse_segments parses DashScope transcription JSON."""
        from src.meeting.transcription.dashscope_file import DashScopeFileTranscription

        response_data = {
            "transcripts": [
                {
                    "sentences": [
                        {"text": "Hello", "begin_time": 1000, "end_time": 2000, "speaker_id": "spk1"},
                        {"text": "World", "begin_time": 2500, "end_time": 3500, "speaker_id": "spk2"},
                        {"text": "", "begin_time": 4000, "end_time": 5000},  # empty text, skipped
                    ]
                }
            ]
        }

        mock_response = MagicMock()
        mock_response.json.return_value = response_data
        mock_response.raise_for_status = MagicMock()

        with patch("src.meeting.transcription.dashscope_file.httpx.get", return_value=mock_response):
            segments = DashScopeFileTranscription._fetch_and_parse_segments("http://example.com/result.json")

        assert len(segments) == 2
        assert segments[0].text == "Hello"
        assert segments[0].start == 1.0  # 1000ms -> 1.0s
        assert segments[0].end == 2.0
        assert segments[0].speaker_id == "spk1"
        assert segments[1].text == "World"

    def test_fetch_and_parse_empty_transcripts(self):
        """_fetch_and_parse_segments returns empty list for empty transcripts."""
        from src.meeting.transcription.dashscope_file import DashScopeFileTranscription

        mock_response = MagicMock()
        mock_response.json.return_value = {"transcripts": []}
        mock_response.raise_for_status = MagicMock()

        with patch("src.meeting.transcription.dashscope_file.httpx.get", return_value=mock_response):
            segments = DashScopeFileTranscription._fetch_and_parse_segments("http://example.com/result.json")

        assert segments == []


class TestRealtimeCallback:
    """DashScope _RealtimeCallback event handling."""

    def test_on_event_parses_segment(self):
        """on_event extracts segment from DashScope result and calls callback."""
        from src.meeting.transcription.dashscope_realtime import _RealtimeCallback

        callback = MagicMock()
        handler = _RealtimeCallback(callback)

        result = MagicMock()
        result.output = {
            "sentence": {
                "text": "Hello world",
                "begin_time": 1000,
                "end_time": 2500,
                "speaker_id": "spk1",
                "sentence_end": True,
            }
        }

        handler.on_event(result)

        callback.assert_called_once()
        segment, is_final, segment_key = callback.call_args[0]
        assert segment.text == "Hello world"
        assert segment.start == 1.0
        assert segment.end == 2.5
        assert segment.speaker_id == "spk1"
        assert is_final is True
        assert segment_key is not None

    def test_on_event_empty_text_skips(self):
        """on_event skips events with empty text."""
        from src.meeting.transcription.dashscope_realtime import _RealtimeCallback

        callback = MagicMock()
        handler = _RealtimeCallback(callback)

        result = MagicMock()
        result.output = {"sentence": {"text": "", "begin_time": 0, "end_time": 0}}

        handler.on_event(result)
        callback.assert_not_called()

    def test_on_event_no_sentence_skips(self):
        """on_event skips events with no sentence dict."""
        from src.meeting.transcription.dashscope_realtime import _RealtimeCallback

        callback = MagicMock()
        handler = _RealtimeCallback(callback)

        result = MagicMock()
        result.output = {}

        handler.on_event(result)
        callback.assert_not_called()

    def test_on_error_logs(self):
        """on_error extracts message from result."""
        from src.meeting.transcription.dashscope_realtime import _RealtimeCallback

        callback = MagicMock()
        handler = _RealtimeCallback(callback)

        result = MagicMock()
        result.message = "Connection lost"

        # Should not raise
        handler.on_error(result)

    def test_on_open_does_not_raise(self):
        """on_open is a no-op that doesn't raise."""
        from src.meeting.transcription.dashscope_realtime import _RealtimeCallback

        handler = _RealtimeCallback(MagicMock())
        handler.on_open()

    def test_on_close_does_not_raise(self):
        """on_close is a no-op that doesn't raise."""
        from src.meeting.transcription.dashscope_realtime import _RealtimeCallback

        handler = _RealtimeCallback(MagicMock())
        handler.on_close(1000, "Normal closure")


class TestRequireDashscope:
    """_require_dashscope guard function."""

    def test_raises_when_dashscope_missing(self):
        """_require_dashscope raises ImportError when dashscope is not installed."""
        from src.meeting.transcription.dashscope_file import _require_dashscope

        with patch("src.meeting.transcription.dashscope_file._HAS_DASHSCOPE", False):
            with pytest.raises(ImportError, match="dashscope package is required"):
                _require_dashscope()

    def test_does_not_raise_when_dashscope_present(self):
        """_require_dashscope passes when dashscope is available."""
        from src.meeting.transcription.dashscope_file import _require_dashscope

        with patch("src.meeting.transcription.dashscope_file._HAS_DASHSCOPE", True):
            _require_dashscope()  # should not raise


# ═══════════════════════════════════════════════════════════════
# 9. Route Tests
# ═══════════════════════════════════════════════════════════════


class TestMeetingRoutes:
    """Meeting API route handlers tested as direct function calls."""

    def test_create_meeting_route(self):
        """POST /meetings creates a meeting and returns it."""
        from src.meeting.routes import create_meeting

        meeting = _make_meeting()
        with patch("src.meeting.routes.store") as mock_store:
            mock_store.create_meeting.return_value = meeting
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                create_meeting({"title": "Test Meeting"})
            )

        assert result["id"] == "abc123"
        assert result["title"] == "Test Meeting"

    def test_list_meetings_route(self):
        """GET /meetings returns list of meetings."""
        from src.meeting.routes import list_meetings

        m1 = _make_meeting(id="1", title="A")
        m2 = _make_meeting(id="2", title="B")
        with patch("src.meeting.routes.store") as mock_store:
            mock_store.list_meetings.return_value = [m1, m2]
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(list_meetings())

        assert len(result) == 2
        assert result[0]["title"] == "A"

    def test_get_meeting_route_found(self):
        """GET /meetings/{id} returns meeting with notes and transcript."""
        from src.meeting.routes import get_meeting

        meeting = _make_meeting()
        with patch("src.meeting.routes.store") as mock_store:
            mock_store.get_meeting.return_value = meeting
            mock_store.get_notes.return_value = "My notes"
            mock_store.get_transcript.return_value = _make_transcript_result()
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                get_meeting("abc123")
            )

        assert result["notes_content"] == "My notes"
        assert result["transcript"]["text"] == "Hello world. How are you?"

    def test_get_meeting_route_not_found(self):
        """GET /meetings/{id} returns error when meeting not found."""
        from src.meeting.routes import get_meeting

        with patch("src.meeting.routes.store") as mock_store:
            mock_store.get_meeting.return_value = None
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                get_meeting("missing")
            )

        assert "error" in result

    def test_delete_meeting_route(self):
        """DELETE /meetings/{id} deletes the meeting."""
        from src.meeting.routes import delete_meeting

        meeting = _make_meeting()
        with patch("src.meeting.routes.store") as mock_store:
            mock_store.get_meeting.return_value = meeting
            mock_store.delete_meeting.return_value = True
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                delete_meeting("abc123")
            )

        assert result["message"] == "Meeting deleted"

    def test_delete_meeting_not_found(self):
        """DELETE /meetings/{id} returns error for missing meeting."""
        from src.meeting.routes import delete_meeting

        with patch("src.meeting.routes.store") as mock_store:
            mock_store.get_meeting.return_value = None
            mock_store.delete_meeting.return_value = False
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                delete_meeting("missing")
            )

        assert "error" in result

    def test_update_meeting_route(self):
        """PUT /meetings/{id} updates meeting fields."""
        from src.meeting.routes import update_meeting

        updated = _make_meeting(title="New Title")
        with patch("src.meeting.routes.store") as mock_store:
            mock_store.update_meeting.return_value = updated
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                update_meeting("abc123", {"title": "New Title"})
            )

        assert result["title"] == "New Title"

    def test_update_meeting_notes(self):
        """PUT /meetings/{id} with notes key saves notes to file."""
        from src.meeting.routes import update_meeting

        meeting = _make_meeting()
        with patch("src.meeting.routes.store") as mock_store:
            mock_store.get_meeting.return_value = meeting
            mock_store.update_meeting.return_value = meeting
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                update_meeting("abc123", {"notes": "New notes"})
            )

        mock_store.save_notes.assert_called_once_with("abc123", "New notes")

    def test_start_transcription_route(self):
        """POST /meetings/{id}/transcribe creates a task."""
        from src.meeting.routes import start_transcription

        meeting = _make_meeting(audio_path="/tmp/audio.webm")
        provider = MagicMock()
        task = _make_task()

        with patch("src.meeting.routes.store") as mock_store, \
             patch("src.meeting.routes.meeting_service") as mock_svc, \
             patch("src.meeting.routes.task_manager") as mock_tm:
            mock_store.get_meeting.return_value = meeting
            mock_store.update_meeting.return_value = meeting
            mock_svc.get_active_file_provider.return_value = provider
            mock_tm.create_task.return_value = task

            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                start_transcription("abc123")
            )

        assert result["message"] == "Transcription started"
        assert result["task_id"] == "task-001"

    def test_start_transcription_no_audio(self):
        """POST /meetings/{id}/transcribe fails when no audio uploaded."""
        from src.meeting.routes import start_transcription

        meeting = _make_meeting(audio_path=None)
        with patch("src.meeting.routes.store") as mock_store:
            mock_store.get_meeting.return_value = meeting
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                start_transcription("abc123")
            )

        assert "error" in result
        assert "No audio" in result["error"]

    def test_start_transcription_no_provider(self):
        """POST /meetings/{id}/transcribe fails when no provider configured."""
        from src.meeting.routes import start_transcription

        meeting = _make_meeting(audio_path="/tmp/audio.webm")
        with patch("src.meeting.routes.store") as mock_store, \
             patch("src.meeting.routes.meeting_service") as mock_svc:
            mock_store.get_meeting.return_value = meeting
            mock_store.update_meeting.return_value = meeting
            mock_svc.get_active_file_provider.return_value = None
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                start_transcription("abc123")
            )

        assert "error" in result

    def test_generate_summary_route(self):
        """POST /meetings/{id}/generate-summary creates a task (v3)."""
        from src.meeting.routes import generate_summary

        meeting = _make_meeting()
        with patch("src.meeting.routes.store") as mock_store, \
             patch("src.meeting.routes.task_manager") as mock_tm:
            mock_store.get_meeting.return_value = meeting
            mock_store.get_transcript.return_value = _make_transcript_result()
            mock_task = _make_task()
            mock_tm.create_task.return_value = mock_task

            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                generate_summary("abc123")
            )

        assert result["message"] == "Generation started"

    def test_generate_summary_no_transcript(self):
        """POST /meetings/{id}/generate-summary fails when no transcript."""
        from src.meeting.routes import generate_summary

        meeting = _make_meeting()
        with patch("src.meeting.routes.store") as mock_store:
            mock_store.get_meeting.return_value = meeting
            mock_store.get_transcript.return_value = None

            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                generate_summary("abc123")
            )

        assert "error" in result

    @pytest.mark.skip(reason="v3: allocate_to_db replaced by allocate_section(meeting_id, tab_id, body)")
    def test_allocate_route(self):
        """v2 test — skipped: allocate endpoint now requires tab_id in path."""
        pass

    def test_allocate_missing_collection(self):
        """POST /meetings/{id}/sections/{tab_id}/allocate fails when collection not specified (v3)."""
        from src.meeting.routes import allocate_section

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            allocate_section("abc123", "tab_01", {})
        )
        assert "error" in result

    def test_get_meeting_tasks_route(self):
        """GET /meetings/{id}/tasks returns matching tasks."""
        from src.meeting.routes import get_meeting_tasks

        task = _make_task()
        task.to_dict.return_value = {"status": "pending", "filename": "meeting_abc123"}
        with patch("src.meeting.routes.task_manager") as mock_tm:
            mock_tm.get_all_tasks.return_value = [task]
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                get_meeting_tasks("abc123")
            )

        assert len(result["tasks"]) == 1


# ═══════════════════════════════════════════════════════════════
# 10. ABC Tests
# ═══════════════════════════════════════════════════════════════


class TestTranscriptionABCs:
    """Abstract base classes cannot be instantiated directly."""

    def test_file_transcription_provider_is_abstract(self):
        """FileTranscriptionProvider cannot be instantiated."""
        from src.meeting.transcription.base import FileTranscriptionProvider

        with pytest.raises(TypeError):
            FileTranscriptionProvider()

    def test_realtime_transcription_provider_is_abstract(self):
        """RealtimeTranscriptionProvider cannot be instantiated."""
        from src.meeting.transcription.base import RealtimeTranscriptionProvider

        with pytest.raises(TypeError):
            RealtimeTranscriptionProvider()
