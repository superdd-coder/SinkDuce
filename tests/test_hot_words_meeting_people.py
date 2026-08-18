"""Meeting People system library, pins, and multi-select concat."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture
def hw_dirs(tmp_path):
    meetings = tmp_path / "meetings"
    speakers = tmp_path / "speakers"
    hot_words = tmp_path / "hot_words"
    meetings.mkdir()
    speakers.mkdir()
    hot_words.mkdir()
    with (
        patch("src.meeting.store.MEETINGS_DIR", meetings),
        patch("src.speakers.store.SPEAKERS_DIR", speakers),
        patch("src.hot_words.store.HOTWORDS_DIR", hot_words),
        patch("src.hot_words.store.SETTINGS_PATH", hot_words / "_settings.json"),
    ):
        yield {"meetings": meetings, "speakers": speakers, "hot_words": hot_words}


def _bind(meeting, person_id: str, speaker_id: str = "spk0"):
    from src.meeting.store import get_meeting, update_meeting

    current = get_meeting(meeting.id) or meeting
    people = dict(current.speaker_people or {})
    people[speaker_id] = person_id
    return update_meeting(meeting.id, speaker_people=people)


class TestMeetingPeopleLibrary:
    def test_system_library_always_listed(self, hw_dirs):
        from src.hot_words.store import MEETING_PEOPLE_ID, list_libraries

        libs = list_libraries()
        assert any(lib.id == MEETING_PEOPLE_ID for lib in libs)
        assert (hw_dirs["hot_words"] / f"{MEETING_PEOPLE_ID}.json").exists() is False

    def test_includes_name_only_after_two_meetings(self, hw_dirs):
        from src.hot_words.store import MEETING_PEOPLE_ID, get_library
        from src.meeting.store import create_meeting
        from src.speakers.store import create_person

        person = create_person("Xu Ye", disambiguator="GreenTech")
        guest = create_person("One Off")
        m1 = create_meeting("A")
        m2 = create_meeting("B")
        _bind(m1, person.id)
        _bind(m2, person.id)
        _bind(m1, guest.id, "spk1")

        lib = get_library(MEETING_PEOPLE_ID)
        assert lib is not None
        texts = [w.text for w in lib.words]
        assert texts == ["Xu Ye"]
        assert all(w.text != "GreenTech" for w in lib.words)
        assert "One Off" not in texts

    def test_duplicate_display_names_collapse(self, hw_dirs):
        from src.hot_words.store import MEETING_PEOPLE_ID, get_library
        from src.meeting.store import create_meeting
        from src.speakers.store import create_person

        a = create_person("Zhang Wei", disambiguator="Eng")
        b = create_person("zhang wei", disambiguator="Sales")
        m1 = create_meeting("A")
        m2 = create_meeting("B")
        _bind(m1, a.id)
        _bind(m2, a.id)
        _bind(m1, b.id, "spk1")
        _bind(m2, b.id, "spk1")
        lib = get_library(MEETING_PEOPLE_ID)
        assert len(lib.words) == 1

    def test_system_library_cannot_be_mutated(self, hw_dirs):
        from src.hot_words.store import MEETING_PEOPLE_ID, delete_library, update_library

        with pytest.raises(PermissionError):
            update_library(MEETING_PEOPLE_ID, name="Nope")
        with pytest.raises(PermissionError):
            delete_library(MEETING_PEOPLE_ID)


class TestPinsAndConcat:
    def test_default_pins_include_meeting_people(self, hw_dirs):
        from src.hot_words.store import MEETING_PEOPLE_ID, get_pinned_library_ids

        assert get_pinned_library_ids() == [MEETING_PEOPLE_ID]

    def test_migrates_old_default_into_pins(self, hw_dirs):
        from src.hot_words.store import (
            MEETING_PEOPLE_ID,
            create_library,
            get_pinned_library_ids,
            set_default_library_id,
        )

        lib = create_library("Jargon")
        set_default_library_id(lib.id)
        pins = get_pinned_library_ids()
        assert MEETING_PEOPLE_ID in pins
        assert lib.id in pins

    def test_set_pins_rejects_missing(self, hw_dirs):
        from src.hot_words.store import set_pinned_library_ids

        with pytest.raises(FileNotFoundError):
            set_pinned_library_ids(["missing-lib"])

    def test_concat_dedupes_max_weight(self, hw_dirs):
        from src.hot_words.models import HotWordItem
        from src.hot_words.store import collect_hot_words, create_library

        a = create_library(
            "A",
            words=[HotWordItem(text="COD", weight=4), HotWordItem(text="MBR", weight=3)],
        )
        b = create_library(
            "B",
            words=[HotWordItem(text="cod", weight=7), HotWordItem(text="OPEX", weight=5)],
        )
        words = collect_hot_words([a.id, b.id])
        by = {w["text"].casefold(): w for w in words}
        assert by["cod"]["weight"] == 7
        assert by["mbr"]["weight"] == 3
        assert by["opex"]["weight"] == 5

    def test_meeting_ids_fallback_to_single(self, hw_dirs):
        from src.hot_words.store import meeting_library_ids
        from src.meeting.models import Meeting

        old = Meeting(id="x", title="Old", hot_words_library_id="abc")
        assert meeting_library_ids(old) == ["abc"]
        none = Meeting(id="y", title="None")
        assert meeting_library_ids(none) == []
        multi = Meeting(
            id="z",
            title="Multi",
            hot_words_library_ids=["a", "b"],
            hot_words_library_id="a",
        )
        assert meeting_library_ids(multi) == ["a", "b"]

    def test_apply_pins_copies_to_new_meeting(self, hw_dirs):
        from src.hot_words.store import (
            MEETING_PEOPLE_ID,
            apply_pinned_libraries,
            create_library,
            set_pinned_library_ids,
        )
        from src.meeting.store import create_meeting, get_meeting

        jargon = create_library("Jargon")
        set_pinned_library_ids([MEETING_PEOPLE_ID, jargon.id])
        meeting = create_meeting("New")
        apply_pinned_libraries(meeting.id)
        fetched = get_meeting(meeting.id)
        assert fetched.hot_words_library_ids == [MEETING_PEOPLE_ID, jargon.id]
        assert fetched.hot_words_library_id == MEETING_PEOPLE_ID
