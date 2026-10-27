"""Meeting / Group archive + merged-list backend contracts.

Archive keeps all data on disk; it only closes the LLM query-tool doors
(catalog / transcript lookup / summary read / MCP direct reads).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.meeting.models import Meeting


@pytest.fixture
def client(tmp_path):
    from src.meeting.routes import router
    from src.meeting.store import create_meeting

    groups = tmp_path / "meeting_groups"
    meetings = tmp_path / "meetings"
    groups.mkdir()
    meetings.mkdir()
    app = FastAPI()
    app.include_router(router)
    with (
        patch("src.meeting.group_store.GROUPS_DIR", groups),
        patch("src.meeting.store.MEETINGS_DIR", meetings),
        patch("src.identity.authorize", lambda *a, **k: None),
        patch("src.identity.get_actor", lambda: None),
    ):
        m = create_meeting("Kickoff")
        yield TestClient(app), m


def _meeting(mid: str, title: str, *, cols: list[str] | None = None, archived: bool = False):
    return Meeting(
        id=mid,
        title=title,
        allocated_collections=list(cols or []),
        transcript_index_status="ready",
        archived=archived,
    )


class TestMeetingArchiveRoutes:
    def test_archive_then_unarchive_roundtrip(self, client):
        http, meeting = client
        res = http.post(f"/meetings/{meeting.id}/archive")
        assert res.status_code == 200
        assert res.json()["archived"] is True

        from src.meeting.store import get_meeting

        assert get_meeting(meeting.id).archived is True

        res = http.post(f"/meetings/{meeting.id}/unarchive")
        assert res.status_code == 200
        assert res.json()["archived"] is False
        assert get_meeting(meeting.id).archived is False

    def test_archive_unknown_meeting_404(self, client):
        http, _meeting = client
        assert http.post("/meetings/nope/archive").status_code == 404
        assert http.post("/meetings/nope/unarchive").status_code == 404


class TestGroupArchiveRoutes:
    def test_group_archive_cascades_to_members(self, client):
        http, meeting = client
        from src.meeting.store import create_meeting, get_meeting

        other = create_meeting("Follow-up")
        gid = http.post("/meeting-groups", json={"meeting_id": meeting.id, "title": "G"}).json()["id"]
        http.post(f"/meeting-groups/{gid}/members", json={"meeting_id": other.id})

        res = http.post(f"/meeting-groups/{gid}/archive")
        assert res.status_code == 200
        assert res.json()["archived"] is True
        assert get_meeting(meeting.id).archived is True
        assert get_meeting(other.id).archived is True

        res = http.post(f"/meeting-groups/{gid}/unarchive")
        assert res.status_code == 200
        assert res.json()["archived"] is False
        assert get_meeting(meeting.id).archived is False
        assert get_meeting(other.id).archived is False

    def test_unarchiving_one_member_unarchives_group_only(self, client):
        """Unarchiving a single member pulls the group card out of the
        archive; the other members keep their archived state."""
        http, meeting = client
        from src.meeting.store import create_meeting, get_meeting
        from src.meeting.group_store import get_group

        other = create_meeting("Follow-up")
        gid = http.post("/meeting-groups", json={"meeting_id": meeting.id, "title": "G"}).json()["id"]
        http.post(f"/meeting-groups/{gid}/members", json={"meeting_id": other.id})
        http.post(f"/meeting-groups/{gid}/archive")

        res = http.post(f"/meetings/{other.id}/unarchive")
        assert res.status_code == 200
        assert get_group(gid).archived is False
        assert get_meeting(other.id).archived is False
        assert get_meeting(meeting.id).archived is True

    def test_group_archive_unknown_404(self, client):
        http, _meeting = client
        assert http.post("/meeting-groups/nope/archive").status_code == 404
        assert http.post("/meeting-groups/nope/unarchive").status_code == 404


class TestGroupRenameAndCascadeDelete:
    def test_rename_group(self, client):
        http, meeting = client
        gid = http.post("/meeting-groups", json={"meeting_id": meeting.id, "title": "G"}).json()["id"]
        res = http.patch(f"/meeting-groups/{gid}", json={"title": "Renamed"})
        assert res.status_code == 200
        assert res.json()["title"] == "Renamed"

    def test_rename_group_empty_title_400(self, client):
        http, meeting = client
        gid = http.post("/meeting-groups", json={"meeting_id": meeting.id, "title": "G"}).json()["id"]
        assert http.patch(f"/meeting-groups/{gid}", json={"title": "  "}).status_code == 400

    def test_rename_unknown_group_404(self, client):
        http, _meeting = client
        assert http.patch("/meeting-groups/nope", json={"title": "X"}).status_code == 404

    def test_delete_group_cascades_to_member_meetings(self, client):
        http, meeting = client
        from src.meeting.store import create_meeting, get_meeting

        other = create_meeting("Follow-up")
        gid = http.post("/meeting-groups", json={"meeting_id": meeting.id, "title": "G"}).json()["id"]
        http.post(f"/meeting-groups/{gid}/members", json={"meeting_id": other.id})

        res = http.delete(f"/meeting-groups/{gid}")
        assert res.status_code == 200
        assert get_meeting(meeting.id) is None
        assert get_meeting(other.id) is None
        assert http.get(f"/meeting-groups/{gid}").status_code == 404

    def test_delete_group_keep_meetings_opt_out(self, client):
        http, meeting = client
        from src.meeting.store import create_meeting, get_meeting

        other = create_meeting("Follow-up")
        gid = http.post("/meeting-groups", json={"meeting_id": meeting.id, "title": "G"}).json()["id"]
        http.post(f"/meeting-groups/{gid}/members", json={"meeting_id": other.id})

        res = http.delete(f"/meeting-groups/{gid}?delete_meetings=false")
        assert res.status_code == 200
        assert get_meeting(meeting.id) is not None
        assert get_meeting(other.id) is not None
        assert http.get(f"/meeting-groups/{gid}").status_code == 404


class TestCatalogHidesArchived:
    def test_default_scope_excludes_archived(self):
        from src.meeting.catalog import visible_meeting_ids

        meetings = [_meeting("m1", "A"), _meeting("m2", "B", archived=True)]
        with patch("src.meeting.store.list_meetings", return_value=meetings):
            assert visible_meeting_ids() == ["m1"]

    def test_collection_scope_excludes_archived(self):
        from src.meeting.catalog import visible_meeting_ids

        meetings = [
            _meeting("m1", "In", cols=["col_a"]),
            _meeting("m2", "Archived", cols=["col_a"], archived=True),
        ]
        with patch("src.meeting.store.list_meetings", return_value=meetings):
            assert visible_meeting_ids(collection="col_a") == ["m1"]

    def test_single_meeting_scope_blocks_archived(self):
        from src.meeting.catalog import visible_meeting_ids

        archived = _meeting("m1", "A", archived=True)
        with patch("src.meeting.store.get_meeting", return_value=archived):
            assert visible_meeting_ids(meeting_id="m1") == []

    def test_group_scope_excludes_archived_members(self):
        from src.meeting.catalog import visible_meeting_ids
        from src.meeting.models import MeetingGroup, MeetingGroupMember

        group = MeetingGroup(
            id="g1",
            title="G",
            members=[
                MeetingGroupMember(meeting_id="m1", n=1),
                MeetingGroupMember(meeting_id="m2", n=2),
            ],
        )
        meetings = [_meeting("m1", "A"), _meeting("m2", "B", archived=True)]
        with (
            patch("src.meeting.group_store.get_group", return_value=group),
            patch(
                "src.meeting.store.get_meeting",
                side_effect=lambda mid: next(m for m in meetings if m.id == mid),
            ),
        ):
            assert visible_meeting_ids(group_id="g1") == ["m1"]

    def test_summary_read_blocks_archived(self):
        from src.meeting.catalog import read_meeting_summary_json

        archived = _meeting("m1", "A", archived=True)
        with (
            patch("src.meeting.store.list_meetings", return_value=[archived]),
            patch("src.meeting.store.get_meeting", return_value=archived),
        ):
            data = json.loads(read_meeting_summary_json("m1"))
        assert "error" in data

    def test_catalog_rows_of_visible_only(self):
        from src.meeting.catalog import catalog_tool_json

        meetings = [_meeting("m1", "A"), _meeting("m2", "B", archived=True)]
        with patch("src.meeting.store.list_meetings", return_value=meetings):
            data = json.loads(catalog_tool_json())
        assert [r["meeting_id"] for r in data["meetings"]] == ["m1"]


class TestTranscriptLookupBlocksArchived:
    def test_lookup_json_archived_meeting_returns_error(self):
        from src.meeting.transcript_index import lookup_json_and_keys

        archived = _meeting("m1", "A", archived=True)
        with patch("src.meeting.store.get_meeting", return_value=archived):
            raw, found, hits = lookup_json_and_keys("m1", "anything")
        data = json.loads(raw)
        assert data["hit_count"] == 0
        assert "archived" in data["error"]
        assert found == set()
        assert hits == []


class TestMcpArchivedGating:
    def test_list_meetings_excludes_archived_by_default(self):
        import asyncio

        from src.mcp.tools import meetings as mcp_meetings

        meetings = [_meeting("m1", "A"), _meeting("m2", "B", archived=True)]
        with patch("src.meeting.store.list_meetings", return_value=meetings):
            live = asyncio.run(mcp_meetings.list_meetings())
            everything = asyncio.run(mcp_meetings.list_meetings(include_archived=True))
        live_ids = [item["id"] for item in json.loads(live)["meetings"]]
        all_ids = [item["id"] for item in json.loads(everything)["meetings"]]
        assert live_ids == ["m1"]
        assert all_ids == ["m1", "m2"]

    def test_get_transcript_archived_errs(self):
        import asyncio

        from src.mcp.tools import meetings as mcp_meetings

        archived = _meeting("m1", "A", archived=True)
        with patch("src.meeting.store.get_meeting", return_value=archived):
            raw = asyncio.run(mcp_meetings.get_meeting_transcript("m1"))
        assert "archived" in json.loads(raw)["error"]

    def test_get_section_archived_errs(self):
        import asyncio

        from src.mcp.common import err
        from src.mcp.tools import meetings as mcp_meetings

        archived = _meeting("m1", "A", archived=True)
        with patch("src.meeting.store.get_meeting", return_value=archived):
            raw = asyncio.run(mcp_meetings.get_section("m1", "general"))
        assert "archived" in raw
