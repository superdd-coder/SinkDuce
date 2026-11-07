"""An import correction moves the current row without rewriting old versions."""

from tests.test_qa import create, mutate, qa_fixture  # noqa: F401
from tests.test_qa_excel import commit, upload


def test_correction_preserves_later_reply_and_old_export(qa):
    client, base = qa
    d = create(qa)
    d = mutate(qa, d, "/questions", {"text": "Explain the orchard irrigation plan."})
    d = mutate(qa, d, "/questions", {"text": "Explain the greenhouse irrigation plan."})
    old, target = d["questions"]
    d = mutate(
        qa, d, f"/questions/{target['id']}", {"status": "closed", "type": "Clarify"}, "patch"
    )
    original_version = d["version"]["id"]
    plan = upload(
        qa,
        d,
        [
            ["Question", "A response"],
            ["Explain the covered garden watering plan.", "Initial response."],
        ],
    )
    row = plan["rows"][0]["key"]
    r = client.patch(
        f"{base}/lists/{d['id']}/imports/{plan['id']}",
        json={
            "plan_revision": plan["plan_revision"],
            "matches": {row: old["id"]},
            "removed_questions": [target["id"]],
        },
    )
    assert r.status_code == 200, r.text
    plan = r.json()
    conflicts = plan["review"]["conflicts"]
    assert any(conflict["kind"] == "question" for conflict in conflicts)
    r = client.patch(
        f"{base}/lists/{d['id']}/imports/{plan['id']}",
        json={
            "plan_revision": plan["plan_revision"],
            "content_choices": {conflict["id"]: "incoming" for conflict in conflicts},
        },
    )
    assert r.status_code == 200, r.text
    d = commit(qa, d, r.json())
    cid = next(c["id"] for c in d["columns"] if c["label"] == "A response")
    d = mutate(
        qa,
        d,
        f"/questions/{old['id']}/replies/{cid}",
        {"body": "Additional notes saved after import."},
        "put",
    )
    from src.qa import service, store

    with store.connection("col_qa", "update") as conn, conn:
        service.save_annotation(
            conn,
            d["version"]["id"],
            old["id"],
            {"summary": "Watering plan", "reply_summary": "Additional notes", "type": "Provide"},
        )
    eid = client.post(
        f"{base}/lists/{d['id']}/exports", json={"expected_revision": d["revision"]}
    ).json()["id"]
    download = f"{base}/lists/{d['id']}/exports/{eid}/download"
    frozen_export = client.get(download).content
    path = f"{base}/lists/{d['id']}/imports/{plan['id']}/corrections"
    r = client.post(
        path,
        json={
            "expected_revision": d["revision"],
            "row_key": row,
            "target": target["id"],
            "drafts": "keep",
        },
    )
    assert r.status_code == 200, r.text
    preview = r.json()
    assert preview["target_text"] == target["text"]
    assert preview["replies"][0]["body"] == "Additional notes saved after import."
    r = client.post(path + "/apply", json={"preview_id": preview["id"]})
    assert r.status_code == 200, r.text
    current = r.json()
    restored = next(q for q in current["questions"] if q["id"] == target["id"])
    assert restored["included"] is True
    assert restored["status"] == "closed"
    assert restored["type"] == "Clarify"
    assert restored["latest_reply"] == "Additional notes saved after import."
    released = next(q for q in current["questions"] if q["id"] == old["id"])
    assert released["included"] is False and released["text"] == old["text"]
    assert client.get(download).content == frozen_export
    frozen = client.get(f"{base}/lists/{d['id']}?version_id={original_version}").json()
    assert frozen["questions"][0]["text"] == old["text"]
    # Future exact matches no longer use the withdrawn association to the old identity.
    again = upload(
        qa,
        current,
        [["Question", "A response"], ["Explain the covered garden watering plan.", "New reply."]],
    )
    assert again["matches"][again["rows"][0]["key"]] == target["id"]
