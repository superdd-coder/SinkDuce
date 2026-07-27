"""Tests for the Meeting Summary translation feature.

Covers: store translation-file helpers, the SSE translation stream
(MeetingService.generate_translation_stream — broadcast + replay), and the
translate-stream / translations / active API routes.

Run: python -m pytest tests/test_meeting_translation.py -x -v
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest


# ── Store helpers ─────────────────────────────────────────────


class TestTranslationStore:
    @pytest.fixture(autouse=True)
    def _patch_meetings_dir(self, tmp_path):
        self._meetings_dir = tmp_path / "meetings"
        with patch("src.meeting.store.MEETINGS_DIR", self._meetings_dir):
            yield

    def _make_meeting_dir(self, meeting_id: str):
        (self._meetings_dir / meeting_id).mkdir(parents=True, exist_ok=True)

    def test_translation_path_naming(self):
        from src.meeting.store import translation_md_path

        assert translation_md_path("m1", "tab_02", "cn").name == "tab_02_CN.md"
        assert translation_md_path("m1", "tab_general", "EN").name == "tab_general_EN.md"

    def test_save_and_get_translation(self):
        from src.meeting.store import save_translation_md, get_translation_md

        self._make_meeting_dir("m1")
        save_translation_md("m1", "tab_02", "CN", "# 你好")
        assert get_translation_md("m1", "tab_02", "CN") == "# 你好"
        assert get_translation_md("m1", "tab_02", "cn") == "# 你好"  # case-insensitive

    def test_get_translation_missing_returns_none(self):
        from src.meeting.store import get_translation_md

        self._make_meeting_dir("m1")
        assert get_translation_md("m1", "tab_02", "FR") is None

    def test_list_translation_langs_sorted(self):
        from src.meeting.store import save_translation_md, list_translation_langs

        self._make_meeting_dir("m1")
        save_translation_md("m1", "tab_02", "JA", "x")
        save_translation_md("m1", "tab_02", "CN", "x")
        save_translation_md("m1", "tab_02", "EN", "x")
        save_translation_md("m1", "tab_general", "CN", "x")  # other tab must not leak
        assert list_translation_langs("m1", "tab_02") == ["CN", "EN", "JA"]

    def test_list_translation_langs_empty(self):
        from src.meeting.store import list_translation_langs

        self._make_meeting_dir("m1")
        assert list_translation_langs("m1", "tab_02") == []


# ── Streaming service ─────────────────────────────────────────


class TestTranslationStreamService:
    @pytest.fixture(autouse=True)
    def _patch_meetings_dir(self, tmp_path):
        self._meetings_dir = tmp_path / "meetings"
        with patch("src.meeting.store.MEETINGS_DIR", self._meetings_dir):
            yield

    def _seed_source(self, meeting_id: str, tab_id: str, content: str = "# Summary"):
        (self._meetings_dir / meeting_id).mkdir(parents=True, exist_ok=True)
        from src.meeting.store import save_section_md
        save_section_md(meeting_id, tab_id, content)

    def _service(self):
        from src.meeting.service import MeetingService
        return MeetingService()

    def _llm(self, chunks: list[str]) -> MagicMock:
        llm = MagicMock()
        llm.generate_stream_tagged.return_value = iter([(c, False) for c in chunks])
        return llm

    def test_generates_streams_and_persists(self):
        self._seed_source("m1", "tab_general")
        svc = self._service()
        llm = self._llm(["# 翻", "译", "结果"])
        with patch("src.meeting.service._resolve_meeting_llm", return_value=llm):
            events = list(svc.generate_translation_stream("m1", "tab_general", "CN"))

        token_text = "".join(e["data"] for e in events if e["event"] == "token")
        assert token_text == "# 翻译结果"
        done = [e for e in events if e["event"] == "translation_done"]
        assert len(done) == 1
        assert done[0]["data"]["md"] == "# 翻译结果"
        assert done[0]["data"]["cached"] is False
        assert done[0]["data"]["language"] == "CN"

        from src.meeting.store import get_translation_md
        assert get_translation_md("m1", "tab_general", "CN") == "# 翻译结果"
        # task removed from the active registry once finished
        assert svc.list_active_translations("m1") == []

    def test_cache_branch_skips_llm(self):
        self._seed_source("m1", "tab_02")
        from src.meeting.store import save_translation_md
        save_translation_md("m1", "tab_02", "EN", "cached-content")
        svc = self._service()
        with patch("src.meeting.service._resolve_meeting_llm") as resolve:
            events = list(svc.generate_translation_stream("m1", "tab_02", "EN"))
        resolve.assert_not_called()
        done = [e for e in events if e["event"] == "translation_done"]
        assert len(done) == 1
        assert done[0]["data"]["md"] == "cached-content"
        assert done[0]["data"]["cached"] is True

    def test_invalid_language_emits_error(self):
        self._seed_source("m1", "tab_general")
        svc = self._service()
        events = list(svc.generate_translation_stream("m1", "tab_general", "XX"))
        assert events[0]["event"] == "error"
        assert "Unsupported language" in events[0]["data"]["message"]

    def test_missing_source_emits_error(self):
        svc = self._service()
        events = list(svc.generate_translation_stream("m1", "tab_general", "CN"))
        assert events[0]["event"] == "error"
        assert "No summary found" in events[0]["data"]["message"]

    def test_language_is_case_insensitive(self):
        self._seed_source("m1", "tab_general")
        svc = self._service()
        llm = self._llm(["hola"])
        with patch("src.meeting.service._resolve_meeting_llm", return_value=llm):
            events = list(svc.generate_translation_stream("m1", "tab_general", "es"))
        done = [e for e in events if e["event"] == "translation_done"]
        assert done[0]["data"]["language"] == "ES"
        from src.meeting.store import get_translation_md
        assert get_translation_md("m1", "tab_general", "ES") == "hola"

    def test_list_active_translations_filters(self):
        from src.meeting.service import _TranslationStream
        svc = self._service()
        live = _TranslationStream("m1", "tab_02", "CN")
        finished = _TranslationStream("m1", "tab_03", "EN")
        finished.done = True
        other_meeting = _TranslationStream("m2", "tab_02", "JA")
        svc._active_translation_streams["m1:tab_02:CN"] = live
        svc._active_translation_streams["m1:tab_03:EN"] = finished
        svc._active_translation_streams["m2:tab_02:JA"] = other_meeting

        assert svc.list_active_translations("m1") == [{"tab_id": "tab_02", "language": "CN"}]

    def test_replay_broadcast_for_reconnecting_consumer(self):
        """A consumer attaching to an in-progress task replays all accumulated
        tokens, then continues live until done (the page-refresh path)."""
        from src.meeting.service import _TranslationStream
        svc = self._service()
        st = _TranslationStream("m1", "tab_general", "CN")
        st.accumulated = "part1-"           # already streamed before "disconnect"
        st.gen_state = "streaming"
        svc._active_translation_streams["m1:tab_general:CN"] = st

        events: list[dict] = []

        def consume():
            for e in svc.generate_translation_stream("m1", "tab_general", "CN"):
                events.append(e)

        t = threading.Thread(target=consume, daemon=True)
        t.start()
        time.sleep(0.3)                     # let it replay "part1-"
        with st.cond:                       # producer finishes while consumer attached
            st.accumulated += "part2"
            st.final = "part1-part2"
            st.done = True
            st.gen_state = "idle"
            st.cond.notify_all()
        t.join(timeout=2)

        token_text = "".join(e["data"] for e in events if e["event"] == "token")
        assert token_text == "part1-part2"  # replayed prefix + live suffix, no dup
        assert any(e["event"] == "translation_done" for e in events)
        svc._active_translation_streams.pop("m1:tab_general:CN", None)


# ── Routes ────────────────────────────────────────────────────


class TestTranslationRoutes:
    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.meeting.routes import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_stream_route_emits_sse(self):
        from src.meeting.service import meeting_service
        client = self._client()

        def fake_gen(meeting_id, tab_id, lang):
            yield {"event": "state", "data": {"translation_gen": "idle"}}
            yield {"event": "translation_done", "data": {
                "tab_id": tab_id, "language": lang, "md": "# X", "cached": True}}

        with patch.object(meeting_service, "generate_translation_stream", fake_gen):
            res = client.get("/meetings/m1/sections/tab_general/translate/stream?lang=CN")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        assert "event: state" in res.text
        assert "event: translation_done" in res.text

    def test_translations_route_lists_langs(self):
        from src.meeting.service import meeting_service
        client = self._client()
        with patch.object(
            meeting_service, "list_summary_translations", return_value=["CN", "EN"],
        ):
            res = client.get("/meetings/m1/sections/tab_02/translations")
        assert res.status_code == 200
        assert res.json() == {"languages": ["CN", "EN"]}

    def test_active_route(self):
        from src.meeting.service import meeting_service
        client = self._client()
        with patch.object(
            meeting_service, "list_active_translations",
            return_value=[{"tab_id": "tab_02", "language": "CN"}],
        ):
            res = client.get("/meetings/m1/translations/active")
        assert res.status_code == 200
        assert res.json() == {"active": [{"tab_id": "tab_02", "language": "CN"}]}
