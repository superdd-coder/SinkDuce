"""Q&A business invariants using invented content and isolated collection storage."""

from importlib.util import find_spec

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_qa_domain_exists():
    assert find_spec("src.qa") is not None, "Q&A domain has not been implemented"


@pytest.fixture(name="qa")
def qa_fixture(tmp_path, monkeypatch):
    if find_spec("src.qa") is None:
        pytest.skip("Domain presence test covers missing implementation")
    from src.collections import store as collections
    from src.file_mgmt import store as files
    from src.qa.routes import router

    monkeypatch.setattr(collections, "COLLECTIONS_DIR", tmp_path)
    monkeypatch.setattr(files, "COLLECTIONS_DIR", tmp_path)
    files._backfill_done.clear()
    collections.create_collection_meta("col_qa", "Example Library")
    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)
    return client, "/api/collections/col_qa/qa"


def create(qa):
    client, base = qa
    r = client.post(
        base + "/lists",
        json={"title": "Example checklist", "columns": ["A response", "B response"]},
    )
    assert r.status_code == 201, r.text
    return r.json()


def mutate(qa, data, path, body, method="post"):
    client, base = qa
    r = getattr(client, method)(
        f"{base}/lists/{data['id']}{path}", json={"expected_revision": data["revision"], **body}
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_blank_list_created_by_name_has_one_default_reply_column(qa):
    client, base = qa
    response = client.post(base + "/lists", json={"title": "Orchard checklist"})
    assert response.status_code == 201, response.text
    detail = response.json()
    assert [column["label"] for column in detail["columns"] if column["included"]] == ["Reply"]
    detail = mutate(qa, detail, "/questions", {"text": "Confirm the orchard opening hours."})
    assert [(reply["label"], reply["body"]) for reply in detail["questions"][0]["replies"]] == [
        ("Reply", "")
    ]


def test_blank_list_shared_columns_and_saved_replies(qa):
    d = create(qa)
    d = mutate(qa, d, "/questions", {"text": "Explain the orchard sensor calibration."})
    first = d["questions"][0]["id"]
    d = mutate(qa, d, "/questions", {"text": "Provide the greenhouse inspection log."})
    assert all(len(q["replies"]) == 2 for q in d["questions"])
    d = mutate(qa, d, "/columns", {"labels": ["A follow up", "B follow up"]})
    assert all(len(q["replies"]) == 4 for q in d["questions"])
    shared_column = d["columns"][-1]["id"]
    d = mutate(qa, d, f"/columns/{shared_column}", {"label": "Inspection notes"}, "patch")
    assert all(
        next(r for r in q["replies"] if r["column_id"] == shared_column)["label"]
        == "Inspection notes"
        for q in d["questions"]
    )
    column = d["columns"][0]["id"]
    d = mutate(
        qa,
        d,
        f"/questions/{first}/replies/{column}",
        {"body": "Calibration uses an independent reference."},
        "put",
    )
    assert d["questions"][0]["status"] == "waiting"
    assert d["questions"][0]["replies"][0]["body"].startswith("Calibration")
    client, base = qa
    assert client.get(f"{base}/lists/{d['id']}").json() == d


def test_versions_freeze_status_names_and_no_future_questions(qa):
    d = create(qa)
    d = mutate(qa, d, "/questions", {"text": "Confirm the observatory opening hours."})
    qid = d["questions"][0]["id"]
    d = mutate(qa, d, f"/questions/{qid}", {"status": "closed"}, "patch")
    old = d["version"]["id"]
    d = mutate(qa, d, "/versions", {})
    d = mutate(qa, d, f"/questions/{qid}", {"status": "waiting"}, "patch")
    d = mutate(qa, d, f"/columns/{d['columns'][0]['id']}", {"label": "Revised header"}, "patch")
    d = mutate(qa, d, "/questions", {"text": "Provide the telescope maintenance guide."})
    client, base = qa
    history = client.get(f"{base}/lists/{d['id']}?version_id={old}").json()
    assert len(history["questions"]) == 1
    assert history["questions"][0]["status"] == "closed"
    assert history["columns"][0]["label"] == "A response"
    r = client.patch(
        f"{base}/lists/{d['id']}/questions/{qid}",
        json={"expected_revision": d["revision"], "version_id": old, "status": "review"},
    )
    assert r.status_code == 409


def test_omission_survives_multiple_versions_and_can_be_restored(qa):
    d = create(qa)
    d = mutate(qa, d, "/questions", {"text": "Provide the seed storage protocol."})
    qid = d["questions"][0]["id"]
    d = mutate(qa, d, "/versions", {})
    d = mutate(qa, d, f"/questions/{qid}/membership", {"included": False})
    missing_since = d["questions"][0]["absent_since"]
    d = mutate(qa, d, "/versions", {})
    assert d["questions"][0]["included"] is False
    assert d["questions"][0]["absent_since"] == missing_since == 2
    d = mutate(qa, d, "/versions", {})
    d = mutate(qa, d, f"/questions/{qid}/membership", {"included": True, "restore_replies": False})
    assert d["questions"][0]["id"] == qid
    assert d["questions"][0]["included"] is True
    assert d["questions"][0]["status"] == "waiting"


def test_clearing_reply_is_distinct_from_removing_column(qa):
    d = create(qa)
    d = mutate(qa, d, "/questions", {"text": "Explain the weather station data gap."})
    qid = d["questions"][0]["id"]
    cid = d["columns"][0]["id"]
    d = mutate(
        qa, d, f"/questions/{qid}/replies/{cid}", {"body": "The sensor was being serviced."}, "put"
    )
    d = mutate(qa, d, "/versions", {})
    d = mutate(qa, d, f"/questions/{qid}/replies/{cid}", {"body": ""}, "put")
    reply = d["questions"][0]["replies"][0]
    assert reply["body"] == "" and reply["historical_body"] == "The sensor was being serviced."
    assert reply["included"] is True
    d = mutate(qa, d, f"/columns/{cid}", {"included": False}, "patch")
    assert d["questions"][0]["replies"][0]["included"] is False


def test_stale_edits_and_cross_list_ids_rejected(qa):
    d = create(qa)
    stale = d["revision"]
    d = mutate(qa, d, "/questions", {"text": "Confirm the aquarium lighting schedule."})
    client, base = qa
    r = client.post(
        f"{base}/lists/{d['id']}/questions",
        json={"text": "Lost update", "expected_revision": stale},
    )
    assert r.status_code == 409
    other = create(qa)
    r = client.patch(
        f"{base}/lists/{other['id']}/questions/{d['questions'][0]['id']}",
        json={"expected_revision": other["revision"], "status": "closed"},
    )
    assert r.status_code == 404


def test_invalid_status_rejected_without_mutation(qa):
    d = create(qa)
    d = mutate(qa, d, "/questions", {"text": "Explain the archive cooling policy."})
    client, base = qa
    r = client.patch(
        f"{base}/lists/{d['id']}/questions/{d['questions'][0]['id']}",
        json={"expected_revision": d["revision"], "status": "missing"},
    )
    assert r.status_code == 422
    assert client.get(f"{base}/lists/{d['id']}").json()["revision"] == d["revision"]


def test_project_question_overview_excludes_omitted_and_archived(qa):
    first = create(qa)
    first = mutate(qa, first, "/questions", {"text": "Provide the terrarium humidity log."})
    second = create(qa)
    second = mutate(qa, second, "/questions", {"text": "Confirm the aviary feeding schedule."})
    second = mutate(
        qa, second, f"/questions/{second['questions'][0]['id']}/membership", {"included": False}
    )
    client, base = qa
    r = client.get(base + "/questions")
    assert r.status_code == 200
    assert [q["list_id"] for q in r.json()] == [first["id"]]
    assert len(client.get(base + "/questions?include_omitted=true").json()) == 2


def test_readonly_actor_cannot_write_or_see_edit_controls(qa):
    from src.identity import Actor, reset_actor, set_actor

    d = create(qa)
    token = set_actor(Actor(id="test-reader", kind="key", role_id="visitor"))
    try:
        client, base = qa
        response = client.get(f"{base}/lists/{d['id']}")
        assert response.status_code == 200, response.text
        assert response.json()["can_edit"] is False
        r = client.post(
            f"{base}/lists/{d['id']}/questions",
            json={"expected_revision": d["revision"], "text": "Attempted write"},
        )
        assert r.status_code == 403
    finally:
        reset_actor(token)
