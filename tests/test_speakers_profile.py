"""Person profile domain: meeting-level observation cards, fingerprint dirty, aggregation.

Card model (v2): one batch card per MEETING covering every speaker slot —
extracted once, shared by all persons in that meeting. Profiles distill only
the person's RECENT meetings (cap 5).
"""

from __future__ import annotations

import json
import threading
from unittest.mock import patch

import pytest


@pytest.fixture
def speakers_dir(tmp_path):
    root = tmp_path / "speakers"
    root.mkdir()
    with patch("src.speakers.store.SPEAKERS_DIR", root):
        yield root


@pytest.fixture
def meetings_dir(tmp_path):
    root = tmp_path / "meetings"
    root.mkdir()
    with patch("src.meeting.store.MEETINGS_DIR", root):
        yield root


class _FakeLLM:
    """Dispatches by prompt markers (extraction mentions 'pattern evidence',
    aggregation mentions 'person profile'); records every call."""

    def __init__(self, obs_map=None, profile_text="PROFILE(default)"):
        self.obs_map = obs_map if obs_map is not None else {
            "spk0": ["direct; pushes on cost and staffing"]
        }
        self.profile_text = profile_text
        self.calls: list[str] = []

    def generate(self, prompt: str, **kwargs) -> str:
        self.calls.append(prompt)
        low = prompt.lower()
        if "pattern evidence" in low:
            return json.dumps(self.obs_map, ensure_ascii=False)
        return self.profile_text

    @property
    def extraction_calls(self) -> list[str]:
        return [c for c in self.calls if "pattern evidence" in c.lower()]


class _SlowLLM(_FakeLLM):
    """Adds latency so background regeneration is observable."""

    def __init__(self, delay: float = 0.4, **kw):
        super().__init__(**kw)
        self.delay = delay

    def generate(self, prompt: str, **kwargs) -> str:
        import time as _time

        _time.sleep(self.delay)
        return super().generate(prompt, **kwargs)


def _seed_meeting(title, segs_by_spk, bindings):
    from src.meeting.models import TranscriptSegment, TranscriptionResult
    from src.meeting.store import create_meeting, save_transcript, update_meeting

    meeting = create_meeting(title)
    segments = []
    for spk, texts in segs_by_spk.items():
        for i, text in enumerate(texts):
            segments.append(
                TranscriptSegment(start=i * 10.0, end=i * 10.0 + 5.0, text=text, speaker_id=spk)
            )
    save_transcript(
        meeting.id, TranscriptionResult(text=" ".join(s.text for s in segments), segments=segments)
    )
    if bindings:
        update_meeting(meeting.id, speaker_people=dict(bindings))
    return meeting.id


class TestMeetingCardStore:
    def test_card_roundtrip(self, speakers_dir):
        from src.speakers.models import MeetingObservations
        from src.speakers.profile import get_card, save_card

        card = MeetingObservations(
            meeting_id="m1",
            input_hash="h1",
            speakers={"spk0": ["direct", "cost-focused"], "spk1": []},
            extracted_at="2026-08-30T00:00:00+00:00",
        )
        save_card(card)
        assert get_card("m1") == card

    def test_missing_card_returns_none(self, speakers_dir):
        from src.speakers.profile import get_card

        assert get_card("m1") is None

    def test_profile_roundtrip(self, speakers_dir):
        from src.speakers.models import PersonProfile
        from src.speakers.profile import get_profile, save_profile

        p = PersonProfile(
            person_id="p1",
            text="direct; cost-focused",
            generated_at="2026-08-30T00:00:00+00:00",
            source_count=3,
            input_fingerprint="fp",
        )
        save_profile(p)
        assert get_profile("p1") == p


class TestEffectiveKeys:
    def test_bound_meeting_with_transcript_yields_key(self, speakers_dir, meetings_dir):
        from src.speakers.profile import effective_binding_keys
        from src.speakers.store import create_person

        person = create_person("Zhang")
        mid = _seed_meeting("Kickoff", {"spk0": ["hello world"]}, {"spk0": person.id})
        assert effective_binding_keys(person.id) == [(mid, "spk0")]

    def test_unbound_slot_excluded(self, speakers_dir, meetings_dir):
        from src.speakers.profile import effective_binding_keys
        from src.speakers.store import create_person

        person = create_person("Zhang")
        _seed_meeting("Kickoff", {"spk0": ["hello world"]}, {})
        assert effective_binding_keys(person.id) == []

    def test_meeting_without_transcript_excluded(self, speakers_dir, meetings_dir):
        from src.meeting.store import create_meeting, update_meeting
        from src.speakers.profile import effective_binding_keys
        from src.speakers.store import create_person

        person = create_person("Zhang")
        meeting = create_meeting("No transcript")
        update_meeting(meeting.id, speaker_people={"spk0": person.id})
        assert effective_binding_keys(person.id) == []

    def test_slot_without_segments_excluded(self, speakers_dir, meetings_dir):
        from src.speakers.profile import effective_binding_keys
        from src.speakers.store import create_person

        person = create_person("Zhang")
        # spk0 has segments and binding; spk1 has a binding but no segments.
        mid = _seed_meeting("Kickoff", {"spk0": ["hello world"]}, {"spk0": person.id, "spk1": person.id})
        # Only slots with actual transcript segments count.
        assert effective_binding_keys(person.id) == [(mid, "spk0")]


class TestDirty:
    def _regen(self, person_id, **kw):
        from src.speakers.profile import regenerate_profile

        return regenerate_profile(person_id, llm=_FakeLLM(**kw))

    def test_no_profile_is_dirty(self, speakers_dir, meetings_dir):
        from src.speakers.profile import is_dirty
        from src.speakers.store import create_person

        person = create_person("Zhang")
        _seed_meeting("Kickoff", {"spk0": ["hello world"]}, {"spk0": person.id})
        assert is_dirty(person.id) is True

    def test_fresh_after_regen_clean(self, speakers_dir, meetings_dir):
        from src.speakers.profile import is_dirty
        from src.speakers.store import create_person

        person = create_person("Zhang")
        _seed_meeting("Kickoff", {"spk0": ["hello world"]}, {"spk0": person.id})
        self._regen(person.id)
        assert is_dirty(person.id) is False

    def test_new_bound_meeting_marks_dirty(self, speakers_dir, meetings_dir):
        from src.speakers.profile import is_dirty
        from src.speakers.store import create_person

        person = create_person("Zhang")
        _seed_meeting("Kickoff", {"spk0": ["hello world"]}, {"spk0": person.id})
        self._regen(person.id)
        _seed_meeting("Followup", {"spk0": ["more words"]}, {"spk0": person.id})
        assert is_dirty(person.id) is True

    def test_unbind_marks_dirty(self, speakers_dir, meetings_dir):
        from src.meeting.store import update_meeting
        from src.speakers.profile import is_dirty
        from src.speakers.store import create_person

        person = create_person("Zhang")
        mid = _seed_meeting("Kickoff", {"spk0": ["hello world"]}, {"spk0": person.id})
        self._regen(person.id)
        update_meeting(mid, speaker_people=None)
        assert is_dirty(person.id) is True

    def test_transcript_edit_marks_dirty(self, speakers_dir, meetings_dir):
        from src.meeting.store import get_transcript, save_transcript
        from src.speakers.profile import is_dirty
        from src.speakers.store import create_person

        person = create_person("Zhang")
        mid = _seed_meeting("Kickoff", {"spk0": ["hello world"]}, {"spk0": person.id})
        self._regen(person.id)
        transcript = get_transcript(mid)
        transcript.segments[0].text = "edited text"
        transcript.text = "edited text"
        save_transcript(mid, transcript)
        assert is_dirty(person.id) is True


class TestRegenerate:
    def test_extracts_once_then_cached(self, speakers_dir, meetings_dir):
        from src.speakers.profile import regenerate_profile
        from src.speakers.store import create_person

        person = create_person("Zhang")
        _seed_meeting("Kickoff", {"spk0": ["hello world"]}, {"spk0": person.id})

        first = _FakeLLM()
        profile = regenerate_profile(person.id, llm=first)
        assert len(first.extraction_calls) == 1
        assert len([c for c in first.calls if "person profile" in c.lower()]) == 1
        assert profile.text == "PROFILE(default)"
        assert profile.source_count == 1

        second = _FakeLLM()
        regenerate_profile(person.id, llm=second)
        assert second.calls == []  # fully cached: no extraction, no aggregation

    def test_unbound_card_excluded_from_aggregation(self, speakers_dir, meetings_dir):
        from src.meeting.store import update_meeting
        from src.speakers.profile import regenerate_profile
        from src.speakers.store import create_person

        person = create_person("Zhang")
        m1 = _seed_meeting("Kickoff", {"spk0": ["hello world"]}, {"spk0": person.id})
        _seed_meeting("Retrospective", {"spk0": ["other topic"]}, {"spk0": person.id})
        regenerate_profile(person.id, llm=_FakeLLM())

        update_meeting(m1, speaker_people=None)
        second = _FakeLLM(profile_text="PROFILE(v2)")
        profile = regenerate_profile(person.id, llm=second)
        agg = [c for c in second.calls if "person profile" in c.lower()]
        assert len(agg) == 1
        assert "Kickoff" not in agg[0]
        assert "Retrospective" in agg[0]
        assert profile.text == "PROFILE(v2)"

    def test_empty_observation_saves_empty_card_no_recall(self, speakers_dir, meetings_dir):
        from src.speakers.profile import get_card, regenerate_profile
        from src.speakers.store import create_person

        person = create_person("Zhang")
        mid = _seed_meeting("Kickoff", {"spk0": ["small talk only"]}, {"spk0": person.id})
        regenerate_profile(person.id, llm=_FakeLLM(obs_map={"spk0": []}))

        card = get_card(mid)
        assert card is not None
        assert card.speakers.get("spk0") == []

        second = _FakeLLM()
        regenerate_profile(person.id, llm=second)
        assert second.extraction_calls == []

    def test_no_keys_profile_empty_no_aggregation(self, speakers_dir, meetings_dir):
        from src.speakers.profile import regenerate_profile
        from src.speakers.store import create_person

        person = create_person("Zhang")
        fake = _FakeLLM()
        profile = regenerate_profile(person.id, llm=fake)
        assert profile.text == ""
        assert profile.source_count == 0
        assert fake.calls == []


class TestRecentCap:
    def test_only_recent_five_meetings_distilled(self, speakers_dir, meetings_dir):
        from src.speakers.profile import regenerate_profile
        from src.speakers.store import create_person

        person = create_person("Zhang")
        for i in range(1, 7):  # E1 oldest … E6 newest
            _seed_meeting(f"Episode {i}", {"spk0": [f"talk {i}"]}, {"spk0": person.id})

        fake = _FakeLLM()
        profile = regenerate_profile(person.id, llm=fake)
        # One batch extraction per meeting, capped at the 5 most recent.
        assert len(fake.extraction_calls) == 5
        assert profile.source_count == 5
        agg = [c for c in fake.calls if "person profile" in c.lower()][0]
        assert "Episode 1" not in agg  # oldest meeting is out of the window
        assert "Episode 6" in agg

    def test_recent_window_shifts_with_new_meeting(self, speakers_dir, meetings_dir):
        from src.speakers.profile import is_dirty, regenerate_profile
        from src.speakers.store import create_person

        person = create_person("Zhang")
        for i in range(1, 7):
            _seed_meeting(f"Episode {i}", {"spk0": [f"talk {i}"]}, {"spk0": person.id})
        regenerate_profile(person.id, llm=_FakeLLM())
        assert is_dirty(person.id) is False

        _seed_meeting("Episode 7", {"spk0": ["newest"]}, {"spk0": person.id})
        assert is_dirty(person.id) is True  # window slid: E2 dropped, E7 entered


class TestSharedMeetingDedup:
    def test_second_person_reuses_meeting_card(self, speakers_dir, meetings_dir):
        from src.speakers.profile import regenerate_profile
        from src.speakers.store import create_person

        zhang = create_person("Zhang")
        li = create_person("Li")
        _seed_meeting(
            "Joint",
            {"spk0": ["zhang talks"], "spk1": ["li talks"]},
            {"spk0": zhang.id, "spk1": li.id},
        )

        first = _FakeLLM(obs_map={"spk0": ["direct"], "spk1": ["careful"]})
        regenerate_profile(zhang.id, llm=first)
        assert len(first.extraction_calls) == 1  # one batch call for the meeting

        second = _FakeLLM(profile_text="PROFILE(li)")
        profile = regenerate_profile(li.id, llm=second)
        assert second.extraction_calls == []  # card reused, no re-extraction
        assert profile.text == "PROFILE(li)"

    def test_concurrent_regeneration_extracts_meeting_once(self, speakers_dir, meetings_dir):
        from src.speakers.profile import regenerate_profile
        from src.speakers.store import create_person

        zhang = create_person("Zhang")
        li = create_person("Li")
        _seed_meeting(
            "Joint",
            {"spk0": ["zhang talks"], "spk1": ["li talks"]},
            {"spk0": zhang.id, "spk1": li.id},
        )

        slow = _SlowLLM(delay=0.4, obs_map={"spk0": ["direct"], "spk1": ["careful"]})
        threads = [
            threading.Thread(target=lambda pid=pid: regenerate_profile(pid, llm=slow))
            for pid in (zhang.id, li.id)
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=10)
        # The per-meeting extraction lock must collapse the two racing runs
        # into a single LLM extraction call.
        assert len(slow.extraction_calls) == 1


class TestProfileState:
    def test_state_none_when_missing(self, speakers_dir, meetings_dir):
        from src.speakers.profile import profile_state
        from src.speakers.store import create_person

        person = create_person("Zhang")
        state = profile_state(person.id)
        assert state["state"] == "none"
        assert state["dirty"] is True


class TestRoutes:
    @pytest.fixture
    def client(self, speakers_dir, meetings_dir):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.speakers.routes import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_get_profile_unknown_person_404(self, client):
        assert client.get("/speakers/unknown/profile").status_code == 404

    def test_get_profile_state_none(self, client, speakers_dir, meetings_dir):
        from src.speakers.store import create_person

        person = create_person("Zhang")
        resp = client.get(f"/speakers/{person.id}/profile")
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "none"
        assert body["text"] == ""

    def test_regenerate_route_runs_and_gets_ready(self, client, speakers_dir, meetings_dir):
        import time

        from src.speakers.store import create_person

        person = create_person("Zhang")
        _seed_meeting("Kickoff", {"spk0": ["hello world"]}, {"spk0": person.id})

        fake = _FakeLLM(profile_text="PROFILE(route)")
        with patch("src.speakers.profile._resolve_llm", lambda: fake):
            resp = client.post(f"/speakers/{person.id}/profile/regenerate")
            assert resp.status_code == 200
            assert resp.json()["state"] in {"generating", "ready"}

            deadline = time.monotonic() + 5.0
            body = None
            while time.monotonic() < deadline:
                body = client.get(f"/speakers/{person.id}/profile").json()
                if body["state"] == "ready":
                    break
                time.sleep(0.05)
        assert body is not None and body["state"] == "ready"
        assert body["text"] == "PROFILE(route)"
        assert body["source_count"] == 1
        assert body["dirty"] is False


class TestConcurrency:
    def test_start_regenerate_skips_when_clean(self, speakers_dir, meetings_dir):
        import time as _time

        from src.speakers.profile import regenerate_profile, start_regenerate
        from src.speakers.store import create_person

        person = create_person("Zhang")
        _seed_meeting("Kickoff", {"spk0": ["hello world"]}, {"spk0": person.id})
        regenerate_profile(person.id, llm=_FakeLLM())  # now clean

        probe = _FakeLLM(profile_text="SHOULD_NOT_RUN")
        with patch("src.speakers.profile._resolve_llm", lambda: probe):
            state = start_regenerate(person.id, force=False)
            _time.sleep(0.2)  # a wrongly spawned thread would hit the probe by now
        assert state["state"] == "ready"
        assert probe.calls == []

    def test_regenerate_waits_for_ongoing(self, speakers_dir, meetings_dir):
        from src.speakers.profile import regenerate_profile, start_regenerate
        from src.speakers.store import create_person

        person = create_person("Zhang")
        _seed_meeting("Kickoff", {"spk0": ["hello world"]}, {"spk0": person.id})

        slow = _SlowLLM(delay=0.4, profile_text="SLOW")
        with patch("src.speakers.profile._resolve_llm", lambda: slow):
            start_regenerate(person.id, force=True)
            fast = _FakeLLM(profile_text="FAST")
            profile = regenerate_profile(person.id, llm=fast)
        assert profile.text == "SLOW"  # waited for the background run
        assert fast.calls == []  # no duplicate work through the fast engine
        assert len(slow.extraction_calls) == 1


class TestExtractionSpeed:
    def test_window_extractions_run_concurrently(self, speakers_dir, meetings_dir):
        import time as _time

        from src.speakers.profile import regenerate_profile
        from src.speakers.store import create_person

        person = create_person("Zhang")
        for i in range(1, 7):
            _seed_meeting(f"Episode {i}", {"spk0": [f"talk {i}"]}, {"spk0": person.id})

        slow = _SlowLLM(delay=0.4)
        t0 = _time.monotonic()
        profile = regenerate_profile(person.id, llm=slow)
        elapsed = _time.monotonic() - t0
        assert profile.source_count == 5
        # 5 delayed extractions + 1 aggregation sequentially ≈ 2.4s;
        # a pool of 3 must land well under that.
        assert elapsed < 1.8, f"extraction took {elapsed:.2f}s — not concurrent"


class TestRobustExtraction:
    def test_think_wrapped_json_parses(self, speakers_dir, meetings_dir):
        from src.speakers.profile import get_card, regenerate_profile
        from src.speakers.store import create_person

        person = create_person("Zhang")
        mid = _seed_meeting("Kickoff", {"spk0": ["hello world"]}, {"spk0": person.id})

        class _Thinky(_FakeLLM):
            def generate(self, prompt: str, **kwargs) -> str:
                self.calls.append(prompt)
                if "pattern evidence" in prompt.lower():
                    return (
                        "<think>the braces { maybe } confuse parsers</think>\n"
                        '```json\n{"spk0": ["direct; cost-focused"]}\n```'
                    )
                return self.profile_text

        profile = regenerate_profile(person.id, llm=_Thinky())
        assert profile.text == "PROFILE(default)"
        card = get_card(mid)
        assert card is not None
        assert card.speakers.get("spk0") == ["direct; cost-focused"]

    def test_unparseable_output_not_cached_and_stays_dirty(self, speakers_dir, meetings_dir):
        from src.speakers.profile import get_card, is_dirty, regenerate_profile
        from src.speakers.store import create_person

        person = create_person("Zhang")
        mid = _seed_meeting("Kickoff", {"spk0": ["hello world"]}, {"spk0": person.id})

        class _Garbage(_FakeLLM):
            def generate(self, prompt: str, **kwargs) -> str:
                self.calls.append(prompt)
                if "pattern evidence" in prompt.lower():
                    return "sorry, I cannot produce that structure right now"
                return self.profile_text

        profile = regenerate_profile(person.id, llm=_Garbage())
        assert profile.source_count == 0  # nothing carded
        assert get_card(mid) is None  # failure must NOT poison the cache
        assert is_dirty(person.id) is True  # retried next time

    def test_card_hash_is_salted_against_stale_cache(self, speakers_dir, meetings_dir):
        import hashlib

        from src.speakers import profile as profile_mod
        from src.speakers.models import MeetingObservations
        from src.speakers.profile import regenerate_profile
        from src.speakers.store import create_person

        person = create_person("Zhang")
        mid = _seed_meeting("Kickoff", {"spk0": ["hello world"]}, {"spk0": person.id})

        from src.meeting.store import get_transcript

        transcript = get_transcript(mid)
        by_slot: dict[str, list[str]] = {}
        for seg in transcript.segments:
            if seg.speaker_id:
                by_slot.setdefault(seg.speaker_id, []).append(seg.text)
        blob = json.dumps({k: "\n".join(v) for k, v in sorted(by_slot.items())}, ensure_ascii=False)
        stale_hash = hashlib.sha256(blob.encode("utf-8")).hexdigest()  # unsalted v1 hash
        profile_mod.save_card(
            MeetingObservations(meeting_id=mid, input_hash=stale_hash, speakers={"spk0": ["stale"]})
        )

        fake = _FakeLLM()
        profile = regenerate_profile(person.id, llm=fake)
        # The unsalted card must be treated as outdated → re-extracted.
        assert len(fake.extraction_calls) == 1
        card = profile_mod.get_card(mid)
        assert card.input_hash != stale_hash
        assert card.speakers.get("spk0") != ["stale"]
        assert profile.source_count == 1
