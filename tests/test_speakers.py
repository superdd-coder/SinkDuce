"""People gallery + CAM++ voiceprint matching (unit tests)."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest


@pytest.fixture
def speakers_dir(tmp_path):
    root = tmp_path / "speakers"
    root.mkdir()
    with patch("src.speakers.store.SPEAKERS_DIR", root):
        yield root


class TestSpeakerStore:
    def test_create_and_get_person(self, speakers_dir):
        from src.speakers.store import create_person, get_person

        person = create_person("Zhang Wei", disambiguator="Engineering")
        assert person.id
        assert person.display_name == "Zhang Wei"
        assert person.disambiguator == "Engineering"
        assert person.centroid == []
        assert person.recent == []
        assert person.speech_sec == 0.0
        assert person.last_meeting_id is None

        fetched = get_person(person.id)
        assert fetched is not None
        assert fetched.display_name == "Zhang Wei"
        assert fetched.disambiguator == "Engineering"
        assert (speakers_dir / f"{person.id}.json").is_file()

    def test_get_person_missing_returns_none(self, speakers_dir):
        from src.speakers.store import get_person

        assert get_person("nonexistent") is None

    def test_list_people_sorted_by_display_name(self, speakers_dir):
        from src.speakers.store import create_person, list_people

        create_person("Ray")
        create_person("Herman")
        names = [p.display_name for p in list_people()]
        assert names == ["Herman", "Ray"]

    def test_list_people_filter_q(self, speakers_dir):
        from src.speakers.store import create_person, list_people

        create_person("Zhang Wei", disambiguator="Engineering")
        create_person("Ray")
        hits = list_people(q="eng")
        assert [p.display_name for p in hits] == ["Zhang Wei"]

    def test_person_public_dict_omits_embeddings(self, speakers_dir):
        from src.speakers.models import Enrollment
        from src.speakers.store import create_person, get_person, person_public_dict, update_person

        person = create_person("Zhang Wei")
        update_person(
            person.id,
            centroid=[0.1] * 192,
            recent=[
                Enrollment(
                    meeting_id="m1",
                    speaker_id="spk0",
                    embedding=[0.2] * 192,
                    speech_sec=12.0,
                    enrolled_at="2026-08-18T00:00:00+00:00",
                )
            ],
            speech_sec=12.0,
            last_meeting_id="m1",
            last_speaker_id="spk0",
        )
        public = person_public_dict(get_person(person.id))
        assert "centroid" not in public
        assert public["has_voiceprint"] is True
        assert public["display_name"] == "Zhang Wei"
        assert public["last_meeting_id"] == "m1"
        assert public["is_me"] is False
        dumped = str(public)
        assert "0.1" not in dumped
        assert "embedding" not in dumped

    def test_update_person_rename(self, speakers_dir):
        from src.speakers.store import create_person, get_person, update_person

        person = create_person("Old")
        update_person(person.id, display_name="New", disambiguator="HK")
        fetched = get_person(person.id)
        assert fetched.display_name == "New"
        assert fetched.disambiguator == "HK"

    def test_set_me_is_exclusive(self, speakers_dir):
        from src.speakers.store import (
            create_person,
            get_me_person_id,
            person_public_dict,
            set_me_person_id,
        )

        a = create_person("Aaron")
        b = create_person("Jethro")
        assert get_me_person_id() is None
        set_me_person_id(a.id)
        assert get_me_person_id() == a.id
        assert person_public_dict(a)["is_me"] is True
        assert person_public_dict(b)["is_me"] is False
        set_me_person_id(b.id)
        assert get_me_person_id() == b.id
        assert person_public_dict(a)["is_me"] is False
        assert person_public_dict(b)["is_me"] is True
        set_me_person_id(None)
        assert get_me_person_id() is None

    def test_set_me_rejects_missing(self, speakers_dir):
        from src.speakers.store import set_me_person_id

        with pytest.raises(FileNotFoundError):
            set_me_person_id("missingpersonmissingpersonmissingp")

    def test_delete_person_clears_me(self, speakers_dir):
        from src.speakers.store import (
            create_person,
            delete_person,
            get_me_person_id,
            set_me_person_id,
        )

        person = create_person("Aaron")
        set_me_person_id(person.id)
        delete_person(person.id)
        assert get_me_person_id() is None

    def test_delete_person(self, speakers_dir):
        from src.speakers.store import create_person, delete_person, get_person

        person = create_person("Gone")
        assert delete_person(person.id) is True
        assert get_person(person.id) is None
        assert delete_person(person.id) is False

    def test_list_skips_corrupt_file(self, speakers_dir):
        from src.speakers.store import create_person, list_people

        create_person("Ok")
        (speakers_dir / "deadbeefdeadbeefdeadbeefdeadbeef.json").write_text(
            "{not-json", encoding="utf-8"
        )
        people = list_people()
        assert [p.display_name for p in people] == ["Ok"]


def _unit(values: list[float]):
    arr = np.asarray(values, dtype=np.float32)
    n = float(np.linalg.norm(arr))
    return arr / n


class TestSlotVector:
    def test_drops_short_and_zero_embeddings(self):
        from src.speakers.service import build_slot_vector

        segs = [(0.0, 1.0), (1.0, 5.0), (5.0, 9.0)]
        embs = [
            _unit([1.0, 0.0, 0.0, 0.0]),
            _unit([1.0, 0.0, 0.0, 0.0]),
            np.zeros(4, dtype=np.float32),
        ]
        vec, speech_sec = build_slot_vector(segs, embs)
        assert speech_sec == pytest.approx(4.0)
        assert vec is not None
        assert vec[0] == pytest.approx(1.0, abs=1e-5)

    def test_duration_weighted_mean(self):
        from src.speakers.service import build_slot_vector

        segs = [(0.0, 2.0), (2.0, 8.0)]
        embs = [
            _unit([1.0, 0.0]),
            _unit([1.0, 0.4]),
        ]
        vec, speech_sec = build_slot_vector(segs, embs)
        assert speech_sec == pytest.approx(8.0)
        assert vec is not None
        # longer segment pulls the second component up vs the short [1, 0]
        short_only = _unit([1.0, 0.0])
        assert vec[1] > short_only[1]

    def test_drops_outlier_far_from_cluster_mean(self):
        from src.speakers.service import build_slot_vector

        segs = [(0.0, 3.0), (3.0, 6.0), (6.0, 9.0)]
        embs = [
            _unit([1.0, 0.0]),
            _unit([0.99, 0.1]),
            _unit([-1.0, 0.0]),
        ]
        vec, speech_sec = build_slot_vector(segs, embs)
        assert speech_sec == pytest.approx(6.0)
        assert vec is not None
        assert vec[0] > 0.9

    def test_all_skipped_returns_none(self):
        from src.speakers.service import build_slot_vector

        vec, speech_sec = build_slot_vector([(0.0, 1.0)], [_unit([1.0, 0.0])])
        assert vec is None
        assert speech_sec == 0.0


class TestMatchSlot:
    def test_auto_when_clear_winner(self):
        from src.speakers.models import Person
        from src.speakers.service import AUTO_MIN_COS, match_slot

        winner = Person(id="a", display_name="A", centroid=_unit([1.0, 0.0]).tolist())
        other = Person(id="b", display_name="B", centroid=_unit([0.5, 0.866]).tolist())
        result = match_slot(_unit([1.0, 0.0]).tolist(), [winner, other])
        assert result.auto is True
        assert result.selected_id == "a"
        assert result.score >= AUTO_MIN_COS
        assert [t.person_id for t in result.top] == ["a", "b"]

    def test_auto_at_floor_with_margin(self):
        from src.speakers.models import Person
        from src.speakers.service import match_slot

        winner = Person(id="a", display_name="A", centroid=_unit([0.72, 0.694]).tolist())
        other = Person(id="b", display_name="B", centroid=_unit([0.2, 0.98]).tolist())
        result = match_slot(_unit([1.0, 0.0]).tolist(), [winner, other])
        assert result.top[0].score == pytest.approx(0.72, abs=1e-2)
        assert result.auto is True
        assert result.selected_id == "a"

    def test_no_auto_when_top_two_close(self):
        from src.speakers.models import Person
        from src.speakers.service import AUTO_MIN_COS, match_slot

        a = Person(id="a", display_name="A", centroid=_unit([1.0, 0.0]).tolist())
        b = Person(id="b", display_name="B", centroid=_unit([0.995, 0.1]).tolist())
        result = match_slot(_unit([1.0, 0.0]).tolist(), [a, b])
        assert result.auto is False
        assert result.selected_id is None
        assert result.top[0].score > AUTO_MIN_COS

    def test_no_auto_below_floor(self):
        from src.speakers.models import Person
        from src.speakers.service import match_slot

        a = Person(id="a", display_name="A", centroid=_unit([0.69, 0.724]).tolist())
        result = match_slot(_unit([1.0, 0.0]).tolist(), [a])
        assert result.top[0].score == pytest.approx(0.69, abs=1e-2)
        assert result.auto is False
        assert result.selected_id is None
        assert len(result.top) == 1

    def test_top_capped_at_three(self):
        from src.speakers.models import Person
        from src.speakers.service import match_slot

        people = [
            Person(id=str(i), display_name=str(i), centroid=_unit([1.0, float(i) * 0.01]).tolist())
            for i in range(5)
        ]
        result = match_slot(_unit([1.0, 0.0]).tolist(), people)
        assert len(result.top) == 3

    def test_skips_people_without_centroid(self):
        from src.speakers.models import Person
        from src.speakers.service import match_slot

        bare = Person(id="x", display_name="X")
        result = match_slot(_unit([1.0, 0.0]).tolist(), [bare])
        assert result.top == []
        assert result.auto is False

    def test_score_uses_best_recent_slot_if_closer_than_centroid(self):
        from src.speakers.models import Enrollment, Person
        from src.speakers.service import match_slot

        winner = Person(
            id="a",
            display_name="A",
            centroid=_unit([1.0, 0.0]).tolist(),
            recent=[
                Enrollment(
                    meeting_id="phone",
                    speaker_id="0",
                    embedding=_unit([0.0, 1.0]).tolist(),
                    speech_sec=20.0,
                )
            ],
        )
        other = Person(
            id="b",
            display_name="B",
            # Closer to the query than A's centroid ([1,0] → cos 0), but
            # far enough that max(centroid, recent) still clears AUTO_MIN_MARGIN.
            centroid=_unit([0.5, 0.8]).tolist(),
        )
        result = match_slot(_unit([0.0, 1.0]).tolist(), [winner, other])
        assert result.top[0].person_id == "a"
        assert result.top[0].score == pytest.approx(1.0, abs=1e-5)
        assert result.top[1].person_id == "b"
        assert result.top[1].score < result.top[0].score - 0.08
        assert result.auto is True
        assert result.selected_id == "a"


class TestEnrollRebind:
    def test_first_meeting_sets_centroid(self, speakers_dir):
        from src.speakers.service import enroll
        from src.speakers.store import create_person, get_person

        person = create_person("Zhang")
        emb = _unit([1.0, 0.0, 0.0]).tolist()
        enroll(person.id, "m1", "spk0", emb, speech_sec=20.0)
        fetched = get_person(person.id)
        assert len(fetched.recent) == 1
        assert fetched.recent[0].meeting_id == "m1"
        assert fetched.speech_sec == pytest.approx(20.0)
        assert fetched.last_meeting_id == "m1"
        assert fetched.last_speaker_id == "spk0"
        assert fetched.centroid[0] == pytest.approx(1.0, abs=1e-5)

    def test_centroid_caps_each_meeting_weight_at_60s(self, speakers_dir):
        from src.speakers.service import enroll
        from src.speakers.store import create_person, get_person

        person = create_person("Zhang")
        enroll(person.id, "long", "spk0", _unit([1.0, 0.0]).tolist(), 500.0)
        enroll(person.id, "cap", "spk0", _unit([0.0, 1.0]).tolist(), 60.0)
        fetched = get_person(person.id)
        assert fetched.speech_sec == pytest.approx(560.0)
        assert fetched.centroid[0] == pytest.approx(fetched.centroid[1], abs=0.05)

    def test_same_meeting_replaces_row(self, speakers_dir):
        from src.speakers.service import enroll
        from src.speakers.store import create_person, get_person

        person = create_person("Zhang")
        enroll(person.id, "m1", "spk0", _unit([1.0, 0.0]).tolist(), 10.0)
        enroll(person.id, "m1", "spk0", _unit([0.0, 1.0]).tolist(), 12.0)
        fetched = get_person(person.id)
        assert len(fetched.recent) == 1
        assert fetched.speech_sec == pytest.approx(12.0)
        assert fetched.centroid[1] == pytest.approx(1.0, abs=1e-5)

    def test_recent_capped_at_eight(self, speakers_dir):
        from src.speakers.service import enroll
        from src.speakers.store import create_person, get_person

        person = create_person("Zhang")
        for i in range(9):
            enroll(person.id, f"m{i}", "spk0", _unit([1.0, 0.0]).tolist(), 10.0)
        fetched = get_person(person.id)
        assert len(fetched.recent) == 8
        assert fetched.recent[0].meeting_id == "m1"
        assert fetched.recent[-1].meeting_id == "m8"

    def test_thin_first_enroll_skips_centroid(self, speakers_dir):
        from src.speakers.service import enroll
        from src.speakers.store import create_person, get_person

        person = create_person("Zhang")
        enroll(person.id, "m1", "spk0", _unit([1.0, 0.0]).tolist(), speech_sec=3.0)
        fetched = get_person(person.id)
        assert fetched.centroid == []
        assert fetched.recent == []
        assert fetched.last_meeting_id is None

    def test_rebind_moves_meeting_and_recomputes(self, speakers_dir):
        from src.speakers.service import enroll, rebind
        from src.speakers.store import create_person, get_person

        zhang = create_person("Zhang")
        ray = create_person("Ray")
        emb_a = _unit([1.0, 0.0]).tolist()
        emb_b = _unit([0.0, 1.0]).tolist()
        enroll(zhang.id, "m0", "spk0", emb_a, 10.0)
        enroll(zhang.id, "m1", "spk0", emb_b, 10.0)
        rebind(zhang.id, ray.id, "m1", "spk0", emb_b, 10.0)
        zhang = get_person(zhang.id)
        ray = get_person(ray.id)
        assert [r.meeting_id for r in zhang.recent] == ["m0"]
        assert zhang.centroid[0] == pytest.approx(1.0, abs=1e-5)
        assert [r.meeting_id for r in ray.recent] == ["m1"]
        assert ray.centroid[1] == pytest.approx(1.0, abs=1e-5)
        assert zhang.speech_sec == pytest.approx(10.0)
        assert ray.speech_sec == pytest.approx(10.0)


@pytest.fixture
def meeting_and_speakers(tmp_path, speakers_dir):
    meetings_dir = tmp_path / "meetings"
    meetings_dir.mkdir()
    with patch("src.meeting.store.MEETINGS_DIR", meetings_dir):
        yield meetings_dir


def _seed_transcript(meeting, segments):
    from src.meeting.models import TranscriptionResult
    from src.meeting.store import save_transcript

    result = TranscriptionResult(
        text=" ".join(s.text for s in segments),
        segments=segments,
    )
    save_transcript(meeting.id, result)


class TestAttachAfterTranscription:
    def test_auto_selects_clear_match(self, meeting_and_speakers):
        from src.meeting.models import TranscriptSegment
        from src.meeting.store import create_meeting, get_meeting
        from src.speakers.service import attach_after_transcription, enroll
        from src.speakers.store import create_person

        person = create_person("Zhang")
        enroll(person.id, "prior", "spk0", _unit([1.0, 0.0, 0.0]).tolist(), 20.0)
        meeting = create_meeting("New")
        _seed_transcript(
            meeting,
            [
                TranscriptSegment(start=0.0, end=4.0, text="hello", speaker_id="spk0"),
                TranscriptSegment(start=4.0, end=8.0, text="there", speaker_id="spk0"),
            ],
        )
        embs = [_unit([1.0, 0.0, 0.0]), _unit([1.0, 0.0, 0.0])]
        attach_after_transcription(meeting.id, segment_embeddings=embs)
        fetched = get_meeting(meeting.id)
        assert fetched.speaker_people["spk0"] == person.id
        assert fetched.speaker_names["spk0"] == "Zhang"
        assert fetched.speaker_matches["spk0"].auto is True
        assert fetched.speaker_matches["spk0"].enrolled is False
        assert fetched.speaker_matches["spk0"].top[0].person_id == person.id
        assert fetched.speaker_slots is not None
        assert fetched.speaker_slots["spk0"].embedding
        assert fetched.speaker_slots["spk0"].speech_sec == pytest.approx(8.0)

    def test_assign_uses_saved_slot_without_cam(self, meeting_and_speakers):
        from src.meeting.models import TranscriptSegment
        from src.meeting.store import create_meeting, get_meeting
        from src.speakers.service import assign_speaker, attach_after_transcription
        from src.speakers.store import create_person, get_person

        meeting = create_meeting("Saved slot")
        _seed_transcript(
            meeting,
            [TranscriptSegment(start=0.0, end=10.0, text="hello", speaker_id="spk0")],
        )
        attach_after_transcription(
            meeting.id, segment_embeddings=[_unit([0.0, 1.0])]
        )
        person = create_person("Ray")

        def boom(*_a, **_k):
            raise AssertionError("saved slot must not re-run CAM++")

        with patch("src.speakers.service.try_embed_meeting_segments", boom):
            with patch("src.speakers.service._schedule_enroll_from_audio", boom):
                assign_speaker(meeting.id, "spk0", person.id)
        stored = get_person(person.id)
        assert stored.centroid[1] == pytest.approx(1.0, abs=1e-5)
        assert stored.last_meeting_id == meeting.id
        assert get_meeting(meeting.id).speaker_matches["spk0"].enrolled is True

    def test_no_embeddings_leaves_unmatched(self, meeting_and_speakers):
        from src.meeting.models import TranscriptSegment
        from src.meeting.store import create_meeting, get_meeting
        from src.speakers.service import attach_after_transcription

        meeting = create_meeting("Bare")
        _seed_transcript(
            meeting,
            [TranscriptSegment(start=0.0, end=4.0, text="hi", speaker_id="spk0")],
        )
        attach_after_transcription(meeting.id)
        fetched = get_meeting(meeting.id)
        assert not fetched.speaker_people
        assert fetched.speaker_matches == {} or fetched.speaker_matches is None

    def test_discard_clears_speaker_fields(self, meeting_and_speakers):
        from src.meeting.models import TranscriptSegment
        from src.meeting.store import create_meeting, discard_recording, get_meeting
        from src.speakers.service import attach_after_transcription, enroll
        from src.speakers.store import create_person

        person = create_person("Zhang")
        enroll(person.id, "prior", "spk0", _unit([1.0, 0.0]).tolist(), 20.0)
        meeting = create_meeting("Soon gone")
        _seed_transcript(
            meeting,
            [TranscriptSegment(start=0.0, end=4.0, text="hi", speaker_id="spk0")],
        )
        attach_after_transcription(
            meeting.id, segment_embeddings=[_unit([1.0, 0.0])]
        )
        discard_recording(meeting.id)
        fetched = get_meeting(meeting.id)
        assert fetched.speaker_names is None
        assert fetched.speaker_people is None
        assert fetched.speaker_matches is None
        assert fetched.speaker_slots is None


class TestAssignAndCommit:
    def test_assign_enrolls_immediately(self, meeting_and_speakers):
        from src.meeting.models import TranscriptSegment
        from src.meeting.store import create_meeting, get_meeting
        from src.speakers.service import assign_speaker
        from src.speakers.store import create_person, get_person

        person = create_person("Ray")
        meeting = create_meeting("Live")
        _seed_transcript(
            meeting,
            [TranscriptSegment(start=0.0, end=10.0, text="hello", speaker_id="spk0")],
        )
        assign_speaker(
            meeting.id,
            "spk0",
            person.id,
            segment_embeddings=[_unit([0.0, 1.0])],
        )
        fetched = get_meeting(meeting.id)
        assert fetched.speaker_people["spk0"] == person.id
        assert fetched.speaker_names["spk0"] == "Ray"
        assert fetched.speaker_matches["spk0"].enrolled is True
        assert fetched.speaker_matches["spk0"].auto is False
        stored = get_person(person.id)
        assert stored.last_meeting_id == meeting.id
        assert stored.centroid[1] == pytest.approx(1.0, abs=1e-5)

    def test_commit_enrolls_auto_slots(self, meeting_and_speakers):
        from src.meeting.models import TranscriptSegment
        from src.meeting.store import create_meeting, get_meeting
        from src.speakers.service import attach_after_transcription, commit_pending, enroll
        from src.speakers.store import create_person, get_person

        person = create_person("Zhang")
        enroll(person.id, "prior", "spk0", _unit([1.0, 0.0]).tolist(), 20.0)
        meeting = create_meeting("Auto")
        _seed_transcript(
            meeting,
            [TranscriptSegment(start=0.0, end=10.0, text="hello", speaker_id="spk0")],
        )
        embs = [_unit([1.0, 0.0])]
        attach_after_transcription(meeting.id, segment_embeddings=embs)
        assert get_meeting(meeting.id).speaker_matches["spk0"].enrolled is False
        commit_pending(meeting.id, segment_embeddings=embs)
        fetched = get_meeting(meeting.id)
        assert fetched.speaker_matches["spk0"].enrolled is True
        stored = get_person(person.id)
        assert any(r.meeting_id == meeting.id for r in stored.recent)

    def test_reassign_unbinds_previous(self, meeting_and_speakers):
        from src.meeting.models import TranscriptSegment
        from src.meeting.store import create_meeting
        from src.speakers.service import assign_speaker
        from src.speakers.store import create_person, get_person

        zhang = create_person("Zhang")
        ray = create_person("Ray")
        meeting = create_meeting("Switch")
        _seed_transcript(
            meeting,
            [TranscriptSegment(start=0.0, end=10.0, text="hello", speaker_id="spk0")],
        )
        embs = [_unit([1.0, 0.0])]
        assign_speaker(meeting.id, "spk0", zhang.id, segment_embeddings=embs)
        assign_speaker(meeting.id, "spk0", ray.id, segment_embeddings=embs)
        assert get_person(zhang.id).recent == []
        assert get_person(ray.id).last_meeting_id == meeting.id

    def test_unassign_removes_meeting_vector_and_recomputes(self, meeting_and_speakers):
        from src.meeting.models import TranscriptSegment
        from src.meeting.store import create_meeting, get_meeting
        from src.speakers.service import assign_speaker, enroll
        from src.speakers.store import create_person, get_person

        person = create_person("Zhang")
        enroll(person.id, "prior", "spk0", _unit([1.0, 0.0]).tolist(), 20.0)
        meeting = create_meeting("Drop")
        _seed_transcript(
            meeting,
            [TranscriptSegment(start=0.0, end=10.0, text="hello", speaker_id="spk0")],
        )
        assign_speaker(
            meeting.id, "spk0", person.id, segment_embeddings=[_unit([0.0, 1.0])]
        )
        stored = get_person(person.id)
        assert any(r.meeting_id == meeting.id for r in stored.recent)
        assign_speaker(meeting.id, "spk0", None)
        stored = get_person(person.id)
        assert all(r.meeting_id != meeting.id for r in stored.recent)
        assert stored.centroid[0] == pytest.approx(1.0, abs=1e-5)
        assert stored.speech_sec == pytest.approx(20.0)
        fetched = get_meeting(meeting.id)
        assert "spk0" not in (fetched.speaker_people or {})
        assert not (fetched.speaker_names or {}).get("spk0")
        assert fetched.speaker_matches["spk0"].cleared is True
        assert fetched.speaker_matches["spk0"].enrolled is False

    def test_unassign_last_enrollment_clears_centroid(self, meeting_and_speakers):
        from src.meeting.models import TranscriptSegment
        from src.meeting.store import create_meeting
        from src.speakers.service import assign_speaker
        from src.speakers.store import create_person, get_person

        person = create_person("Zhang")
        meeting = create_meeting("Only")
        _seed_transcript(
            meeting,
            [TranscriptSegment(start=0.0, end=10.0, text="hello", speaker_id="spk0")],
        )
        assign_speaker(
            meeting.id, "spk0", person.id, segment_embeddings=[_unit([0.0, 1.0])]
        )
        assign_speaker(meeting.id, "spk0", None)
        stored = get_person(person.id)
        assert stored.recent == []
        assert stored.centroid == []
        assert stored.speech_sec == pytest.approx(0.0)
        assert stored.last_meeting_id is None

    def test_unassign_blocks_rematch_auto(self, meeting_and_speakers):
        from src.meeting.models import TranscriptSegment
        from src.meeting.store import create_meeting, get_meeting
        from src.speakers.service import apply_matches_from_slots, assign_speaker, enroll
        from src.speakers.store import create_person

        person = create_person("Zhang")
        enroll(person.id, "prior", "spk0", _unit([1.0, 0.0]).tolist(), 20.0)
        meeting = create_meeting("Stay clear")
        _seed_transcript(
            meeting,
            [TranscriptSegment(start=0.0, end=10.0, text="hello", speaker_id="spk0")],
        )
        assign_speaker(
            meeting.id, "spk0", person.id, segment_embeddings=[_unit([1.0, 0.0])]
        )
        assign_speaker(meeting.id, "spk0", None)
        apply_matches_from_slots(meeting.id)
        fetched = get_meeting(meeting.id)
        assert not (fetched.speaker_people or {})
        assert fetched.speaker_matches["spk0"].cleared is True
        assert fetched.speaker_matches["spk0"].auto is False

    def test_assign_new_person_does_not_decode_audio(self, meeting_and_speakers):
        from src.meeting.models import TranscriptSegment
        from src.meeting.store import create_meeting, get_meeting
        from src.speakers.service import assign_speaker
        from src.speakers.store import get_person

        meeting = create_meeting("Name only")
        _seed_transcript(
            meeting,
            [TranscriptSegment(start=0.0, end=4.0, text="hi", speaker_id="spk0")],
        )

        def boom(*_a, **_k):
            raise AssertionError("assign must not decode meeting audio")

        with patch("src.speakers.service.try_embed_meeting_segments", boom):
            assign_speaker(
                meeting.id,
                "spk0",
                None,
                new_person={"display_name": "Herman"},
            )
        fetched = get_meeting(meeting.id)
        assert fetched.speaker_names["spk0"] == "Herman"
        assert fetched.speaker_people["spk0"]
        assert fetched.speaker_matches["spk0"].auto is False
        assert fetched.speaker_matches["spk0"].enrolled is False
        stored = get_person(fetched.speaker_people["spk0"])
        assert stored.last_meeting_id == meeting.id
        assert stored.last_speaker_id == "spk0"


class TestAudioPathAndPreview:
    def test_resolve_docker_data_path(self, tmp_path, monkeypatch):
        from src.speakers.service import resolve_meeting_audio_path

        data = tmp_path / "data"
        audio = data / "meetings" / "abc" / "recording.webm"
        audio.parent.mkdir(parents=True)
        audio.write_bytes(b"RIFF")
        monkeypatch.setattr("src.config.DATA_DIR", data)
        found = resolve_meeting_audio_path(
            "/app/data/meetings/abc/recording.webm"
        )
        assert found is not None
        assert found.resolve() == audio.resolve()

    def test_preview_uses_last_meeting_without_recent(self, meeting_and_speakers):
        from src.meeting.models import TranscriptSegment
        from src.meeting.store import create_meeting, update_meeting
        from src.speakers.service import assign_speaker, pick_preview
        from src.speakers.store import create_person, get_person

        person = create_person("Aaron")
        meeting = create_meeting("Old")
        _seed_transcript(
            meeting,
            [
                TranscriptSegment(start=0.0, end=1.0, text="no", speaker_id="0"),
                TranscriptSegment(start=2.0, end=6.0, text="yes", speaker_id="0"),
            ],
        )
        audio = meeting_and_speakers / meeting.id / "recording.webm"
        audio.write_bytes(b"RIFF")
        update_meeting(meeting.id, audio_path=f"/app/data/meetings/{meeting.id}/recording.webm")
        data_root = meeting_and_speakers.parent
        with patch("src.config.DATA_DIR", data_root):
            assign_speaker(meeting.id, "0", person.id)
            preview = pick_preview(person.id)
        stored = get_person(person.id)
        assert stored.last_meeting_id == meeting.id
        assert preview is not None
        assert preview["meeting_id"] == meeting.id
        assert preview["end"] - preview["start"] >= 3.0


class TestEmbedFromAudio:
    def test_attach_uses_injected_embedder(self, meeting_and_speakers):
        from src.meeting.models import TranscriptSegment
        from src.meeting.store import create_meeting, get_meeting
        from src.speakers.service import attach_after_transcription, enroll
        from src.speakers.store import create_person

        person = create_person("Zhang")
        enroll(person.id, "prior", "spk0", _unit([1.0, 0.0]).tolist(), 20.0)
        meeting = create_meeting("Cloud")
        _seed_transcript(
            meeting,
            [TranscriptSegment(start=0.0, end=10.0, text="hi", speaker_id="spk0")],
        )
        waveform = np.ones(16000 * 10, dtype=np.float32)

        def embed_fn(chunk, sample_rate=16000):
            return _unit([1.0, 0.0])

        attach_after_transcription(
            meeting.id,
            waveform=waveform,
            sample_rate=16000,
            embed_fn=embed_fn,
        )
        fetched = get_meeting(meeting.id)
        assert fetched.speaker_people["spk0"] == person.id


class TestCampplusCache:
    def test_embedder_loaded_once(self, monkeypatch):
        from src.speakers import service as svc

        calls = {"n": 0}

        class Fake:
            def embed(self, *a, **k):
                return _unit([1.0, 0.0])

        def fake_try_load(*_a, **_k):
            calls["n"] += 1
            return Fake()

        monkeypatch.setattr(svc, "_embedder", None)
        monkeypatch.setattr(
            "src.meeting.transcription.onnx.campplus.try_load_campplus",
            fake_try_load,
        )
        monkeypatch.setattr(
            "src.meeting.transcription.onnx.paths.onnx_cache_dir",
            lambda *_a, **_k: __import__("pathlib").Path("/tmp"),
        )
        monkeypatch.setattr(
            "src.meeting.transcription.onnx.paths.resolve_hf_snapshot",
            lambda *_a, **_k: None,
        )
        a = svc._load_campplus_embedder()
        b = svc._load_campplus_embedder()
        assert a is b
        assert calls["n"] == 1

    def test_backfill_writes_all_slots_once(self, meeting_and_speakers):
        from src.meeting.models import TranscriptSegment
        from src.meeting.store import create_meeting, get_meeting
        from src.speakers.service import enroll_slot_from_audio
        from src.speakers.store import create_person

        meeting = create_meeting("Old multi")
        _seed_transcript(
            meeting,
            [
                TranscriptSegment(start=0.0, end=10.0, text="a", speaker_id="spk0"),
                TranscriptSegment(start=10.0, end=20.0, text="b", speaker_id="spk1"),
            ],
        )
        p0 = create_person("A")
        p1 = create_person("B")
        embs = [_unit([1.0, 0.0]), _unit([0.0, 1.0])]
        calls = {"n": 0}

        def fake_embed(mid):
            calls["n"] += 1
            return embs

        with patch("src.speakers.service.try_embed_meeting_segments", fake_embed):
            enroll_slot_from_audio(meeting.id, "spk0", p0.id)
            enroll_slot_from_audio(meeting.id, "spk1", p1.id)
        assert calls["n"] == 1
        slots = get_meeting(meeting.id).speaker_slots
        assert "spk0" in slots and "spk1" in slots
        fetched = get_meeting(meeting.id)
        assert fetched.speaker_slots_status == "ready"
        assert fetched.speaker_slots_ms is not None

    def test_backfill_matches_unassigned_speakers(self, meeting_and_speakers):
        from src.meeting.models import TranscriptSegment
        from src.meeting.store import create_meeting, get_meeting
        from src.speakers.service import enroll, enroll_slot_from_audio
        from src.speakers.store import create_person

        known = create_person("Aaron")
        enroll(known.id, "prior", "spk0", _unit([0.0, 1.0]).tolist(), 20.0)
        meeting = create_meeting("Old match")
        _seed_transcript(
            meeting,
            [
                TranscriptSegment(start=0.0, end=10.0, text="a", speaker_id="spk0"),
                TranscriptSegment(start=10.0, end=20.0, text="b", speaker_id="spk1"),
            ],
        )
        picked = create_person("Manual")
        from src.meeting.store import update_meeting

        update_meeting(meeting.id, speaker_people={"spk0": picked.id})
        embs = [_unit([1.0, 0.0]), _unit([0.0, 1.0])]
        with patch("src.speakers.service.try_embed_meeting_segments", lambda _mid: embs):
            enroll_slot_from_audio(meeting.id, "spk0", picked.id)
        fetched = get_meeting(meeting.id)
        assert fetched.speaker_people["spk0"] == picked.id
        assert fetched.speaker_matches["spk1"].top
        assert fetched.speaker_matches["spk1"].top[0].person_id == known.id
        assert fetched.speaker_people.get("spk1") == known.id

    def test_rematch_unassigned_when_gallery_updates(self, meeting_and_speakers):
        from src.meeting.models import TranscriptSegment
        from src.meeting.store import create_meeting, get_meeting, update_meeting
        from src.speakers.models import SpeakerSlotVector
        from src.speakers.service import apply_matches_from_slots, enroll
        from src.speakers.store import create_person

        meeting = create_meeting("Open later")
        _seed_transcript(
            meeting,
            [TranscriptSegment(start=0.0, end=10.0, text="hi", speaker_id="spk0")],
        )
        update_meeting(
            meeting.id,
            speaker_slots={
                "spk0": SpeakerSlotVector(embedding=_unit([0.0, 1.0]).tolist(), speech_sec=10.0),
            },
        )
        apply_matches_from_slots(meeting.id)
        assert not (get_meeting(meeting.id).speaker_people or {})

        person = create_person("Later")
        enroll(person.id, "other", "spk0", _unit([0.0, 1.0]).tolist(), 20.0)
        apply_matches_from_slots(meeting.id)
        fetched = get_meeting(meeting.id)
        assert fetched.speaker_people["spk0"] == person.id
        assert fetched.speaker_matches["spk0"].auto is True

    def test_resolve_assignee_from_meeting_name(self, meeting_and_speakers):
        from src.meeting.models import TranscriptSegment
        from src.meeting.store import create_meeting, update_meeting
        from src.speakers.service import assign_speaker, resolve_assignee_person_id
        from src.speakers.store import create_person

        person = create_person("Jethro")
        meeting = create_meeting("Assign")
        _seed_transcript(
            meeting,
            [TranscriptSegment(start=0.0, end=10.0, text="hi", speaker_id="0")],
        )
        assign_speaker(
            meeting.id, "0", person.id, segment_embeddings=[_unit([1.0, 0.0])]
        )
        update_meeting(
            meeting.id,
            tabs=[
                {
                    "tab_id": "tab_general",
                    "todo_candidates": [
                        {
                            "candidate_id": "c1",
                            "title": "Send quotes",
                            "assignee_label": "Jethro",
                        }
                    ],
                }
            ],
        )
        assert resolve_assignee_person_id(
            meeting_id=meeting.id, candidate_id="c1"
        ) == person.id
        assert resolve_assignee_person_id(assignee_label="Jethro") == person.id


class TestPreview:
    def test_preview_picks_long_segment_from_last_meeting(self, meeting_and_speakers):
        from src.meeting.models import TranscriptSegment
        from src.meeting.store import create_meeting
        from src.speakers.service import assign_speaker, pick_preview
        from src.speakers.store import create_person

        person = create_person("Ray")
        meeting = create_meeting("Hear me")
        _seed_transcript(
            meeting,
            [
                TranscriptSegment(start=0.0, end=1.0, text="no", speaker_id="spk0"),
                TranscriptSegment(start=2.0, end=6.5, text="yes", speaker_id="spk0"),
                TranscriptSegment(start=7.0, end=12.0, text="more", speaker_id="spk0"),
            ],
        )
        assign_speaker(
            meeting.id,
            "spk0",
            person.id,
            segment_embeddings=[
                _unit([1.0, 0.0]),
                _unit([1.0, 0.0]),
                _unit([1.0, 0.0]),
            ],
        )
        audio = meeting_and_speakers / meeting.id / "rec.wav"
        audio.write_bytes(b"RIFF")
        from src.meeting.store import update_meeting

        update_meeting(meeting.id, audio_path=str(audio))
        preview = pick_preview(person.id)
        assert preview is not None
        assert preview["meeting_id"] == meeting.id
        assert preview["speaker_id"] == "spk0"
        assert preview["end"] - preview["start"] >= 3.0

    def test_preview_exclude_picks_other_segment(self, meeting_and_speakers):
        from src.meeting.models import TranscriptSegment
        from src.meeting.store import create_meeting, update_meeting
        from src.speakers.service import assign_speaker, pick_preview
        from src.speakers.store import create_person

        person = create_person("Ray")
        meeting = create_meeting("Two lines")
        _seed_transcript(
            meeting,
            [
                TranscriptSegment(start=2.0, end=6.0, text="one", speaker_id="spk0"),
                TranscriptSegment(start=8.0, end=13.0, text="two", speaker_id="spk0"),
            ],
        )
        audio = meeting_and_speakers / meeting.id / "rec.wav"
        audio.write_bytes(b"RIFF")
        update_meeting(meeting.id, audio_path=str(audio))
        assign_speaker(
            meeting.id,
            "spk0",
            person.id,
            segment_embeddings=[_unit([1.0, 0.0]), _unit([1.0, 0.0])],
        )
        starts = {
            pick_preview(
                person.id,
                exclude_meeting_id=meeting.id,
                exclude_start=2.0,
            )["start"]
            for _ in range(8)
        }
        assert 8.0 in starts
        assert 2.0 not in starts

    def test_list_and_create(self, speakers_dir):
        import asyncio

        from src.speakers.routes import create_speaker, list_speakers
        from src.speakers.store import create_person

        create_person("Ray")
        rows = asyncio.get_event_loop().run_until_complete(list_speakers())
        assert rows[0]["display_name"] == "Ray"
        assert "centroid" not in rows[0]
        created = asyncio.get_event_loop().run_until_complete(
            create_speaker({"display_name": "Zhang", "disambiguator": "HK"})
        )
        assert created["display_name"] == "Zhang"
        assert created["has_voiceprint"] is False

    def test_person_detail_includes_meetings(self, meeting_and_speakers, speakers_dir):
        import asyncio

        from src.meeting.models import TranscriptSegment
        from src.meeting.store import create_meeting
        from src.speakers.routes import get_speaker
        from src.speakers.service import assign_speaker
        from src.speakers.store import create_person

        person = create_person("Aaron")
        meeting = create_meeting("Site visit")
        _seed_transcript(
            meeting,
            [TranscriptSegment(start=0.0, end=10.0, text="hi", speaker_id="0")],
        )
        assign_speaker(
            meeting.id, "0", person.id, segment_embeddings=[_unit([1.0, 0.0])]
        )
        detail = asyncio.get_event_loop().run_until_complete(get_speaker(person.id))
        assert detail["display_name"] == "Aaron"
        assert detail["meeting_count"] >= 1
        assert detail["meetings"][0]["meeting_id"] == meeting.id
        assert detail["meetings"][0]["title"] == "Site visit"
        assert "centroid" not in detail


def _seg(start: float, end: float, speaker_id: str = "spk0"):
    from src.meeting.models import TranscriptSegment

    return TranscriptSegment(
        start=start, end=end, text="x", speaker_id=speaker_id
    )


class TestCloudEmbedSample:
    def test_picks_longest_until_budget(self):
        from src.speakers.service import select_embed_windows

        # 12 × 6s = 72s; budget 60s, clip cap 8s → ten full 6s clips
        segs = [_seg(i * 6.0, (i + 1) * 6.0) for i in range(12)]
        picked = select_embed_windows(segs)["spk0"]
        assert len(picked) == 10
        used = sum(c1 - c0 for _i, c0, c1 in picked)
        assert used == pytest.approx(60.0)

    def test_caps_long_clip_at_center_8s(self):
        from src.speakers.service import select_embed_windows

        picked = select_embed_windows([_seg(0.0, 30.0)])["spk0"]
        assert len(picked) == 1
        _i, c0, c1 = picked[0]
        assert c1 - c0 == pytest.approx(8.0)
        assert c0 == pytest.approx(11.0)
        assert c1 == pytest.approx(19.0)

    def test_uses_all_when_shorter_than_budget(self):
        from src.speakers.service import select_embed_windows

        segs = [_seg(0.0, 2.0), _seg(2.0, 4.5), _seg(5.0, 7.0)]
        picked = select_embed_windows(segs)["spk0"]
        assert len(picked) == 3
        used = sum(c1 - c0 for _i, c0, c1 in picked)
        assert used == pytest.approx(6.5)

    def test_skips_segments_under_min(self):
        from src.speakers.service import select_embed_windows

        segs = [_seg(0.0, 1.0), _seg(1.0, 3.5)]
        picked = select_embed_windows(segs)["spk0"]
        assert len(picked) == 1
        assert picked[0][0] == 1

    def test_samples_each_speaker_independently(self):
        from src.speakers.service import select_embed_windows

        segs = [
            _seg(0.0, 10.0, "spk0"),
            _seg(10.0, 13.0, "spk1"),
            _seg(13.0, 16.0, "spk1"),
        ]
        windows = select_embed_windows(segs)
        assert "spk0" in windows and "spk1" in windows
        assert len(windows["spk1"]) == 2

    def test_sampled_embed_skips_unselected_clips(self):
        from src.speakers.service import embed_selected_windows

        segs = [_seg(float(i), float(i + 3)) for i in range(0, 60, 3)]
        assert len(segs) == 20
        wav = np.ones(16000 * 60, dtype=np.float32)
        calls = {"n": 0}

        def embed_fn(chunk, sample_rate=16000):
            calls["n"] += 1
            return _unit([1.0, 0.0])

        embed_selected_windows(segs, wav, 16000, embed_fn)
        # 20 × 3s = 60s; max 12 clips → 12 embeds, not 20
        assert calls["n"] == 12
        assert calls["n"] < len(segs)

    def test_thin_speech_builds_slot_but_does_not_auto(
        self, meeting_and_speakers
    ):
        from src.meeting.store import create_meeting, get_meeting
        from src.speakers.service import attach_after_transcription, enroll
        from src.speakers.store import create_person

        person = create_person("Zhang")
        enroll(person.id, "prior", "spk0", _unit([1.0, 0.0]).tolist(), 20.0)
        meeting = create_meeting("Thin")
        _seed_transcript(meeting, [_seg(0.0, 4.0)])
        attach_after_transcription(
            meeting.id, segment_embeddings=[_unit([1.0, 0.0])]
        )
        fetched = get_meeting(meeting.id)
        assert fetched.speaker_slots["spk0"].speech_sec == pytest.approx(4.0)
        assert fetched.speaker_matches["spk0"].top[0].person_id == person.id
        assert fetched.speaker_matches["spk0"].auto is False
        assert not (fetched.speaker_people or {})
