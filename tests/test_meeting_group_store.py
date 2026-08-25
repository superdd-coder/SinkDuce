"""Meeting Group JSON store: members, stable n, no SQL."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture
def groups_dir(tmp_path):
    root = tmp_path / "meeting_groups"
    root.mkdir()
    with patch("src.meeting.group_store.GROUPS_DIR", root):
        yield root


def test_create_first_member_is_n1(groups_dir):
    from src.meeting.group_store import create_group, get_group

    g = create_group(title="Pricing", meeting_id="m_a")
    assert g.title == "Pricing"
    assert g.members[0].meeting_id == "m_a"
    assert g.members[0].n == 1
    loaded = get_group(g.id)
    assert loaded is not None
    assert loaded.members[0].n == 1
    assert (groups_dir / f"{g.id}.json").exists()


def test_empty_title_uses_first_meeting_and_count(groups_dir):
    from src.meeting.group_store import create_group

    g = create_group(title="  ", meeting_id="m_a", meeting_title="Kickoff")
    assert g.title == "Kickoff 等 1 场"


def test_add_member_increments_n_and_duplicate_is_noop(groups_dir):
    from src.meeting.group_store import add_member, create_group, get_group

    g = create_group(title="G", meeting_id="m_a")
    g2 = add_member(g.id, "m_b")
    assert [m.n for m in g2.members] == [1, 2]
    assert [m.meeting_id for m in g2.members] == ["m_a", "m_b"]
    again = add_member(g.id, "m_a")
    assert [m.meeting_id for m in again.members] == ["m_a", "m_b"]
    assert get_group(g.id).members[0].n == 1


def test_remove_member_does_not_renumber(groups_dir):
    from src.meeting.group_store import add_member, create_group, remove_member

    g = create_group(title="G", meeting_id="m_a")
    add_member(g.id, "m_b")
    add_member(g.id, "m_c")
    out = remove_member(g.id, "m_b")
    assert [m.meeting_id for m in out.members] == ["m_a", "m_c"]
    assert [m.n for m in out.members] == [1, 3]


def test_group_roster_lists_id_n_speakers_and_unindexed(groups_dir, tmp_path):
    from unittest.mock import patch

    from src.meeting.group_store import add_member, create_group
    from src.meeting.models import Meeting
    from src.chatbox.meeting_context import build_group_ephemeral_context

    g = create_group(title="Line", meeting_id="m1", meeting_title="Kickoff")
    add_member(g.id, "m2")

    def get_meeting(mid):
        if mid == "m1":
            return Meeting(
                id="m1",
                title="Kickoff",
                speaker_names={"0": "Jetro"},
                transcript_index_status="ready",
            )
        return Meeting(
            id="m2",
            title="Follow-up",
            speaker_names={"1": "Alex"},
            transcript_index_status="building",
        )

    with patch("src.meeting.store.get_meeting", side_effect=get_meeting):
        text = build_group_ephemeral_context(g.id)
    assert "id: m1" in text
    assert "Kickoff" in text
    assert "Jetro" in text
    assert "Follow-up" in text
    assert "unindexed" in text.lower()
    assert "Follow-up" in text


def test_list_touch_delete(groups_dir):
    from src.meeting.group_store import (
        create_group,
        delete_group,
        get_group,
        list_groups,
        touch_chat,
    )

    a = create_group(title="A", meeting_id="m_1")
    create_group(title="B", meeting_id="m_2")
    ids = {g.id for g in list_groups()}
    assert a.id in ids
    before = a.last_chat_at
    touched = touch_chat(a.id)
    assert touched.last_chat_at >= before
    assert delete_group(a.id) is True
    assert get_group(a.id) is None
    assert delete_group(a.id) is False
