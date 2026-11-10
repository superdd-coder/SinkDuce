"""Import decisions use invented records and preserve existing correspondence."""

import json

from tests.test_qa import create, mutate, qa_fixture  # noqa: F401
from tests.test_qa_excel import upload


def test_ai_identity_analysis_can_commit_without_optional_summary_fields(qa, monkeypatch):
    from src.services import services

    monkeypatch.setattr(services, "llm", None)
    d = mutate(qa, create(qa), "/questions", {"text": "List the available garden tools."})
    p = upload(qa, d, [["Question", "Reply"], ["When is the pond inspected?", "Every morning."]])

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = json.loads(prompt.split("\n", 1)[1])
            if packet.get("stage") == "structure":
                return json.dumps(
                    {"rows": [{"key": r["key"], "kind": "question"} for r in packet["rows"]]}
                )
            if packet.get("stage") == "identity":
                return json.dumps(
                    {
                        "rows": [
                            {
                                "key": r["key"],
                                "target": "new",
                                "certainty": "high",
                                "reason": "Different request.",
                            }
                            for r in packet["rows"]
                        ]
                    }
                )
            if packet.get("stage") == "columns":
                return json.dumps(
                    {
                        "columns": [
                            {"key": r["key"], "target": "new", "certainty": "high"}
                            for r in packet["incoming"]
                        ]
                    }
                )
            raise RuntimeError("Optional summary provider unavailable")

    monkeypatch.setattr(services, "llm", LLM())
    client, base = qa
    url = f"{base}/lists/{d['id']}/imports/{p['id']}"
    analyzed = client.post(url + "/analyze", json={"plan_revision": p["plan_revision"]})
    assert analyzed.status_code == 200, analyzed.text
    p = analyzed.json()
    assert p["matches"] == {"2": "new"}
    assert "type" not in p["suggestions"]["2"]
    first = client.post(url + "/commit", json={"plan_revision": p["plan_revision"]})
    assert first.status_code == 200, first.text
    saved = first.json()
    q = next(q for q in saved["questions"] if q["included"])
    assert q["text"] == "When is the pond inspected?"
    assert q["latest_reply"] == "Every morning."
    assert q["summary"] == ""
    again = client.post(url + "/commit", json={"plan_revision": p["plan_revision"]})
    assert again.status_code == 200
    assert again.json()["version"]["id"] == saved["version"]["id"]
    assert client.get(f"{base}/lists/{d['id']}").json()["revision"] == saved["revision"]


def test_commit_rejects_nonquestion_parent_in_a_saved_plan_without_partial_writes(qa, monkeypatch):
    from src.qa import store
    from src.services import services

    monkeypatch.setattr(services, "llm", None)
    d = create(qa)
    p = upload(
        qa,
        d,
        [
            ["Question", "Reply"],
            ["Garden notes", ""],
            ["Confirm the seed cabinet lock.", "Checked."],
        ],
    )
    client, base = qa
    url = f"{base}/lists/{d['id']}/imports/{p['id']}"
    updated = client.patch(
        url,
        json={
            "plan_revision": p["plan_revision"],
            "row_actions": {"2": "section", "3": "question"},
        },
    )
    assert updated.status_code == 200, updated.text
    p = updated.json()
    p["rows"][0]["parent_key"] = "2"
    with store.connection("col_qa", "update") as conn, conn:
        conn.execute("UPDATE qa_imports SET plan_json=? WHERE id=?", (json.dumps(p), p["id"]))
    response = client.post(url + "/commit", json={"plan_revision": p["plan_revision"]})
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_parent"
    assert client.get(f"{base}/lists/{d['id']}").json()["revision"] == d["revision"]
    assert client.get(url).json()["committed_version_id"] is None


def choose_new_questions(qa, d, plan):
    client, base = qa
    response = client.patch(
        f"{base}/lists/{d['id']}/imports/{plan['id']}",
        json={
            "plan_revision": plan["plan_revision"],
            "matches": {key: "new" for key in plan["matches"]},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_resolving_source_rows_preserves_confirmed_matches_and_column_mapping(qa, monkeypatch):
    from src.services import services

    monkeypatch.setattr(services, "llm", None)
    d = mutate(qa, create(qa), "/questions", {"text": "Describe the seed room."})
    p = upload(
        qa,
        d,
        [
            ["Request text", "Response"],
            ["Explain the seed storage room.", "It is temperature controlled."],
            ["When is the pond inspected?", "Every morning."],
        ],
    )
    client, base = qa
    url = f"{base}/lists/{d['id']}/imports/{p['id']}"

    def patch(**changes):
        nonlocal p
        result = client.patch(url, json={"plan_revision": p["plan_revision"], **changes})
        assert result.status_code == 200, result.text
        p = result.json()

    patch(mapping={"question": 0, "replies": [1]}, row_actions={"3": "review"})
    qid, cid = d["questions"][0]["id"], d["columns"][0]["id"]
    patch(matches={"2": qid}, column_matches={"1": cid})
    patch(row_actions={"3": "question"}, parents={"3": ""})
    assert p["matches"]["2"] == qid
    assert p["column_matches"]["1"] == cid
    assert p["mapping_valid"] is True
    assert len(p["rows"]) == 2
    patch(matches={"2": qid, "3": "new"})
    conflicts = p["review"]["conflicts"]
    assert any(conflict["kind"] == "question" for conflict in conflicts)
    patch(content_choices={conflict["id"]: "incoming" for conflict in conflicts})
    committed = client.post(url + "/commit", json={"plan_revision": p["plan_revision"]})
    assert committed.status_code == 200, committed.text
    questions = committed.json()["questions"]
    assert len([q for q in questions if q["included"]]) == 2
    assert next(q for q in questions if q["id"] == qid)["text"] == "Explain the seed storage room."


def test_column_role_changes_keep_reviewed_rows_and_question_links_when_compatible(qa):
    detail = mutate(qa, create(qa), "/questions", {"text": "Describe the storage cabinet."})
    plan = upload(
        qa,
        detail,
        [
            ["Question", "A response", "Cabinet code"],
            ["Workshop access", "", ""],
            ["Describe the revised storage cabinet.", "A new lock was installed.", "C-7"],
        ],
    )
    client, base = qa
    url = f"{base}/lists/{detail['id']}/imports/{plan['id']}"

    def patch(**changes):
        nonlocal plan
        result = client.patch(url, json={"plan_revision": plan["plan_revision"], **changes})
        assert result.status_code == 200, result.text
        plan = result.json()

    patch(row_actions={"2": "section"})
    question_id = detail["questions"][0]["id"]
    patch(matches={"3": question_id})
    patch(mapping={**plan["mapping"], "number": 2, "extras": []})

    assert plan["mapping"]["number"] == 2
    assert plan["table_rows"][0]["kind"] == "section"
    assert plan["matches"] == {"3": question_id}
    assert plan["rows"][0]["group"] == "Workshop access"

    patch(mapping={**plan["mapping"], "number": None, "replies": [1, 2]})
    assert plan["table_rows"][0]["kind"] == "section"
    assert plan["matches"] == {"3": None}


def test_new_questions_with_only_empty_historical_rounds_do_not_require_column_matching(qa):
    d = mutate(qa, create(qa), "/questions", {"text": "Describe the nursery entrance."})
    p = upload(
        qa,
        d,
        [["Question", "Reply"], ["Provide the pond inspection record.", "Recorded yesterday."]],
    )
    p = choose_new_questions(qa, d, p)
    assert p["column_matches"] == {"1": "new"}
    client, base = qa
    resumed = client.get(f"{base}/lists/{d['id']}/imports/{p['id']}").json()
    assert resumed["column_matches"] == {"1": "new"}


def test_new_questions_receive_fresh_round_even_when_old_round_was_previously_populated(qa):
    d = mutate(qa, create(qa), "/questions", {"text": "Describe the nursery entrance."})
    qid, cid = d["questions"][0]["id"], d["columns"][0]["id"]
    d = mutate(qa, d, f"/questions/{qid}/replies/{cid}", {"body": "Use the north gate."}, "put")
    d = mutate(qa, d, f"/questions/{qid}/replies/{cid}", {"body": ""}, "put")
    p = upload(
        qa,
        d,
        [["Question", "Reply"], ["Provide the pond inspection record.", "Recorded yesterday."]],
    )
    p = choose_new_questions(qa, d, p)
    assert p["column_matches"] == {"1": "new"}
    assert p["review"]["kept_columns"] == []


def test_resuming_an_old_pending_review_resolves_empty_rounds_without_committing(qa):
    from src.qa import store

    d = mutate(qa, create(qa), "/questions", {"text": "Describe the nursery entrance."})
    p = upload(
        qa,
        d,
        [["Question", "Reply"], ["Provide the pond inspection record.", "Recorded yesterday."]],
    )
    # Model an already-saved review from before the empty-round default existed.
    p["matches"] = {key: "new" for key in p["matches"]}
    p["column_matches"] = {"1": None}
    with store.connection("col_qa", "update") as conn, conn:
        conn.execute("UPDATE qa_imports SET plan_json=? WHERE id=?", (json.dumps(p), p["id"]))
    client, base = qa
    resumed = client.get(base + "/imports/pending").json()[0]["plan"]
    assert resumed["column_matches"] == {"1": "new"}
    assert resumed["plan_revision"] == p["plan_revision"]
    assert resumed["committed_version_id"] is None
    assert client.get(f"{base}/lists/{d['id']}").json()["revision"] == d["revision"]
