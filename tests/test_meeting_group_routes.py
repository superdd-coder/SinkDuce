"""HTTP routes for Meeting Groups."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


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


def test_create_list_and_meeting_groups(client):
    http, meeting = client
    res = http.post("/meeting-groups", json={"meeting_id": meeting.id, "title": "Line A"})
    assert res.status_code == 200
    body = res.json()
    assert body["title"] == "Line A"
    assert body["members"][0]["meeting_id"] == meeting.id
    assert body["members"][0]["n"] == 1

    listed = http.get("/meeting-groups")
    assert listed.status_code == 200
    assert any(g["id"] == body["id"] for g in listed.json())

    mine = http.get(f"/meetings/{meeting.id}/groups")
    assert mine.status_code == 200
    assert mine.json()[0]["id"] == body["id"]


def test_create_requires_existing_meeting(client):
    http, _meeting = client
    res = http.post("/meeting-groups", json={"meeting_id": "nope"})
    assert res.status_code == 404


def test_add_member_and_delete_group_drops_session(client):
    http, meeting = client
    other = None
    from src.meeting.store import create_meeting

    other = create_meeting("Follow-up")
    created = http.post("/meeting-groups", json={"meeting_id": meeting.id, "title": "G"})
    gid = created.json()["id"]
    added = http.post(f"/meeting-groups/{gid}/members", json={"meeting_id": other.id})
    assert added.status_code == 200
    assert [m["n"] for m in added.json()["members"]] == [1, 2]

    with patch("src.meeting.routes._delete_group_session") as drop:
        gone = http.delete(f"/meeting-groups/{gid}")
        assert gone.status_code == 200
        drop.assert_called_once_with(gid)


def test_add_member_starts_index_when_missing(client):
    http, meeting = client
    from src.meeting.store import create_meeting, update_meeting

    other = create_meeting("Follow-up")
    update_meeting(other.id, transcript_index_status="")
    gid = http.post("/meeting-groups", json={"meeting_id": meeting.id, "title": "G"}).json()["id"]
    with (
        patch("src.meeting.store.get_sentences", return_value=[{"text": "hi"}]),
        patch("src.meeting.routes.task_manager.create_task") as create_task,
    ):
        res = http.post(f"/meeting-groups/{gid}/members", json={"meeting_id": other.id})
    assert res.status_code == 200
    create_task.assert_called()
    assert create_task.call_args.kwargs["task_type"] == "meeting_transcript_index"
    assert create_task.call_args.kwargs["meeting_id"] == other.id


def test_create_group_starts_index_when_missing(client):
    http, meeting = client
    from src.meeting.store import update_meeting

    update_meeting(meeting.id, transcript_index_status="")
    with (
        patch("src.meeting.store.get_sentences", return_value=[{"text": "hi"}]),
        patch("src.meeting.routes.task_manager.create_task") as create_task,
    ):
        res = http.post("/meeting-groups", json={"meeting_id": meeting.id, "title": "G"})
    assert res.status_code == 200
    create_task.assert_called()
    assert create_task.call_args.kwargs["task_type"] == "meeting_transcript_index"
    assert create_task.call_args.kwargs["meeting_id"] == meeting.id


def test_add_member_skips_index_when_ready(client):
    http, meeting = client
    from src.meeting.store import create_meeting, update_meeting

    other = create_meeting("Follow-up")
    update_meeting(other.id, transcript_index_status="ready")
    gid = http.post("/meeting-groups", json={"meeting_id": meeting.id, "title": "G"}).json()["id"]
    with patch("src.meeting.routes.task_manager.create_task") as create_task:
        res = http.post(f"/meeting-groups/{gid}/members", json={"meeting_id": other.id})
    assert res.status_code == 200
    create_task.assert_not_called()


def test_delete_meeting_removes_from_groups(client):
    http, meeting = client
    from src.meeting.store import create_meeting, delete_meeting

    g = http.post("/meeting-groups", json={"meeting_id": meeting.id, "title": "G"}).json()
    delete_meeting(meeting.id)
    remaining = http.get(f"/meeting-groups/{g['id']}")
    assert remaining.status_code == 200
    assert remaining.json()["members"] == []
