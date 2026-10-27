"""Meetings of fully-archived collections disappear from enumeration.

A meeting hidden iff its allocated_collections list is non-empty AND every
member collection is archived. Meetings in at least one active collection
stay visible (multi-collection allocation is the norm — spec §3.5).

TDD for the collection archive feature — spec:
development_doc/2026-09-13-collection-archive-design.md
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.collections import store as collections_store
from src.meeting.models import Meeting


@pytest.fixture(autouse=True)
def _archived(monkeypatch):
    monkeypatch.setattr(collections_store, "archived_collection_ids", lambda: {"col_arch"})


def _meeting(mid: str, title: str, *, cols: list[str] | None = None, archived: bool = False):
    return Meeting(
        id=mid,
        title=title,
        allocated_collections=list(cols or []),
        transcript_index_status="ready",
        archived=archived,
    )


class TestVisibleMeetingIdsCollectionArchive:
    def test_fully_archived_allocation_hidden(self):
        from src.meeting.catalog import visible_meeting_ids

        meetings = [
            _meeting("m1", "In archived only", cols=["col_arch"]),
            _meeting("m2", "In active", cols=["col_active"]),
        ]
        with patch("src.meeting.store.list_meetings", return_value=meetings):
            assert visible_meeting_ids() == ["m2"]

    def test_mixed_allocation_stays_visible(self):
        from src.meeting.catalog import visible_meeting_ids

        meetings = [_meeting("m1", "Mixed", cols=["col_active", "col_arch"])]
        with patch("src.meeting.store.list_meetings", return_value=meetings):
            assert visible_meeting_ids() == ["m1"]

    def test_unarchive_restores_visibility_purely(self):
        from src.meeting.catalog import visible_meeting_ids

        meetings = [_meeting("m1", "Was hidden", cols=["col_arch"])]
        with (
            patch("src.meeting.store.list_meetings", return_value=meetings),
            patch(collections_store.__name__ + ".archived_collection_ids", lambda: set()),
        ):
            assert visible_meeting_ids() == ["m1"]

    def test_unallocated_meeting_unchanged(self):
        from src.meeting.catalog import visible_meeting_ids

        meetings = [_meeting("m1", "Never ingested", cols=[])]
        with patch("src.meeting.store.list_meetings", return_value=meetings):
            assert visible_meeting_ids() == ["m1"]

    def test_deleted_collection_reference_does_not_hide(self):
        # A deleted collection is not an archived one — meeting stays visible.
        from src.meeting.catalog import visible_meeting_ids

        meetings = [_meeting("m1", "Points at deleted col", cols=["col_deleted"])]
        with patch("src.meeting.store.list_meetings", return_value=meetings):
            assert visible_meeting_ids() == ["m1"]

    def test_explicit_collections_branch_untouched(self):
        from src.meeting.catalog import visible_meeting_ids

        meetings = [_meeting("m1", "In archived only", cols=["col_arch"])]
        with patch("src.meeting.store.list_meetings", return_value=meetings):
            assert visible_meeting_ids(collections=["col_arch"]) == ["m1"]

    def test_explicit_meeting_id_branch_untouched(self):
        from src.meeting.catalog import visible_meeting_ids

        m1 = _meeting("m1", "In archived only", cols=["col_arch"])
        with (
            patch("src.meeting.store.list_meetings", return_value=[m1]),
            patch("src.meeting.store.get_meeting", return_value=m1),
        ):
            assert visible_meeting_ids(meeting_id="m1") == ["m1"]
