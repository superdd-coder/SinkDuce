"""Conservative import identities and lossless row structure on invented workbooks."""

import json
from io import BytesIO

import openpyxl
import pytest

from src.qa import column_alignment, pipeline
from src.qa.excel import read_workbook
from src.qa.workbook_reader import classify_rows
from tests.test_qa import create, mutate, qa_fixture  # noqa: F401
from tests.test_qa_excel import commit, upload


def alignment(monkeypatch, incoming, history, targets=None, labels=None):
    count = len(incoming[0])
    plan = {
        "rows": [
            {"key": str(i + 2), "values": [f"Describe cabinet {i}.", *values]}
            for i, values in enumerate(incoming)
        ],
        "matches": {str(i + 2): f"q-{i}" for i in range(len(incoming))},
        "headers": ["Question", *(labels or ["Revised reply"] * count)],
        "mapping": {"question": 0, "replies": list(range(1, count + 1)), "extras": []},
        "column_matches": {str(i): None for i in range(1, count + 1)},
    }
    columns = [{"id": cid, "label": "Response"} for cid in history]
    samples = {(f"q-{i}", cid): body for cid, bodies in history.items() for i, body in enumerate(bodies)}
    output = pipeline.ColumnResult.model_validate({"columns": [
        {"key": str(i), "target": target, "certainty": "high"}
        for i, target in enumerate(targets or [columns[0]["id"]], 1)
    ]})
    monkeypatch.setattr(pipeline, "cached_calls", lambda *args, **kwargs: [output])
    column_alignment.align(plan, {"fields": [], "values": {}}, columns, samples, {}, None)
    return plan


def test_renamed_reply_column_rejects_one_agreeing_and_one_contradictory_sample(monkeypatch):
    original = ["Cabinet keys are stored at the reception desk.", "The east nursery room uses a humidity sensor."]
    result = alignment(monkeypatch, [[original[0]], ["The proposed storage expansion has not been approved."]], {"c-a": original})
    assert result["column_matches"]["1"] is None


def test_renamed_reply_column_requires_unique_support_across_existing_rounds(monkeypatch):
    original = ["Cabinet keys are stored at the reception desk.", "The east nursery room uses a humidity sensor."]
    result = alignment(monkeypatch, [[body] for body in original], {"c-a": original, "c-b": original})
    assert result["column_matches"]["1"] is None


def test_renamed_reply_column_accepts_consistent_unique_multiple_question_evidence(monkeypatch):
    original = ["Cabinet keys are stored at the reception desk.", "The east nursery room uses a humidity sensor."]
    result = alignment(monkeypatch, [[body + " Confirmed."] for body in original], {"c-a": original, "c-b": ["A different access policy applies.", "The annex has mechanical ventilation."]})
    assert result["column_matches"]["1"] == "c-a"


def test_duplicate_reply_headers_can_be_reordered_only_with_unique_retained_contents(monkeypatch):
    first = ["Cabinet keys are stored at the reception desk.", "The east nursery room uses a humidity sensor."]
    second = ["Provide the access approval from the caretaker.", "Confirm the shelf dimensions against the room plan."]
    result = alignment(monkeypatch, [[b, a] for a, b in zip(first, second)], {"c-a": first, "c-b": second}, targets=["c-b", "c-a"], labels=["Response", "Response"])
    assert result["column_matches"] == {"1": "c-b", "2": "c-a"}


def test_single_changed_reply_is_not_enough_to_reuse_a_renamed_column(monkeypatch):
    original = "Cabinet keys are stored at the reception desk."
    result = alignment(monkeypatch, [[original + " Confirmed."]], {"c-a": [original]})
    assert result["column_matches"]["1"] is None


def test_reply_alignment_checks_beyond_the_eight_rows_sent_to_the_model(monkeypatch):
    original = [f"Fictional cabinet {i} has a separately recorded access procedure." for i in range(9)]
    incoming = [[body] for body in original]
    incoming[-1] = ["The caretaker requests a complete storage redesign."]
    result = alignment(monkeypatch, incoming, {"c-a": original})
    assert result["column_matches"]["1"] is None


def source(rows, *, merge=None):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    if merge:
        sheet.merge_cells(merge)
    buffer = BytesIO()
    workbook.save(buffer)
    return read_workbook(buffer.getvalue())


@pytest.mark.parametrize("kind", ["section", "note", "header"])
def test_reply_only_source_rows_cannot_be_silently_discarded_by_structure_ai(monkeypatch, kind):
    parsed = source([["Question", "Reply"], ["", "A reply whose question is missing."]])
    monkeypatch.setattr(pipeline, "cached_calls", lambda *args, **kwargs: [pipeline.StructureResult(rows=[pipeline.StructureRow(key="2", kind=kind)])])
    result = pipeline.recognize_structure(parsed, {"question": 0, "replies": [1]})
    assert result["row_actions"]["2"] == "review"
    assert result["rows"][0]["values"][1] == "A reply whose question is missing."


def test_bold_wide_reply_only_row_is_not_a_group_when_ai_is_unavailable():
    parsed = source([["Question", "Reply", "Owner", "Status"], ["", "A reply whose question is missing."]], merge="B2:D2")
    parsed["rows"][0]["bold"] = [2]
    table, questions = classify_rows(parsed, {"question": 0, "replies": [1]})
    assert table[0]["kind"] == "review"
    assert questions == []


def test_manual_note_choice_and_physical_continuations_remain_available(monkeypatch):
    parsed = source([["Question", "Reply"], ["", "An explanatory note supplied in this column."]])
    monkeypatch.setattr(pipeline, "cached_calls", lambda *args, **kwargs: [pipeline.StructureResult(rows=[pipeline.StructureRow(key="2", kind="section")])])
    result = pipeline.recognize_structure(parsed, {"question": 0, "replies": [1]}, manual={"2": "note"})
    assert result["row_actions"]["2"] == "note"
    continued = source([["Question", "Reply"], ["Describe cabinet access.", "First part."], ["", "Second part."]], merge="A2:A3")
    table, questions = classify_rows(continued, {"question": 0, "replies": [1]})
    assert table[1]["kind"] == "continuation"
    assert questions[0]["values"][1] == "First part.\nSecond part."


def upload_grouped(qa, data, groups):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Ref", "Question", "Response"])
    for number, name, text in groups:
        row = sheet.max_row + 1
        sheet.cell(row, 2, name)
        sheet.merge_cells(start_row=row, start_column=2, end_row=row, end_column=3)
        sheet.cell(row, 2).font = openpyxl.styles.Font(bold=True)
        sheet.append([number, text, ""])
    buffer = BytesIO()
    workbook.save(buffer)
    client, base = qa
    response = client.post(f"{base}/lists/{data['id']}/imports", files={"file": ("example.xlsx", buffer.getvalue())}, data={"expected_revision": data["revision"]})
    assert response.status_code == 200, response.text
    plan = response.json()
    # Explicit heading choices avoid relying on cosmetic heuristics in identity tests.
    response = client.patch(f"{base}/lists/{data['id']}/imports/{plan['id']}", json={"plan_revision": plan["plan_revision"], "row_actions": {str(i * 2 + 2): "section" for i in range(len(groups))}})
    assert response.status_code == 200, response.text
    return response.json()


def test_moved_questions_keep_identity_when_groups_order_and_display_numbers_change(qa, monkeypatch):
    from src.services import services

    monkeypatch.setattr(services, "llm", None)
    first = [("N-4", "Nursery", "Describe cabinet access."), ("N-9", "Annex", "Confirm the room dimensions.")]
    data = create(qa)
    data = commit(qa, data, upload_grouped(qa, data, first))
    identities = {question["text"]: question["id"] for question in data["questions"]}
    plan = upload_grouped(qa, data, [("N-4", "Nursery", first[1][2]), ("N-9", "Annex", first[0][2])])
    assert all(plan["matches"][row["key"]] == identities[row["text"]] for row in plan["rows"])


def test_duplicate_historical_question_text_stays_ambiguous_even_with_group_and_number_cues(qa, monkeypatch):
    from src.services import services

    monkeypatch.setattr(services, "llm", None)
    text = "Confirm the storage access arrangements."
    groups = [("N-4", "Nursery", text), ("N-9", "Annex", text)]
    data = create(qa)
    data = commit(qa, data, upload_grouped(qa, data, groups))
    plan = upload_grouped(qa, data, list(reversed(groups)))
    identities = [question["id"] for question in data["questions"]]

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = json.loads(prompt.split("\n", 1)[1])
            if packet.get("stage") == "identity":
                assert all("number" not in row and "external_number" not in row for row in packet["rows"])
                return json.dumps({"rows": [{"key": row["key"], "target": identities[index % 2], "certainty": "high"} for index, row in enumerate(packet["rows"])]})
            return json.dumps({"rows": [{"key": row["key"], "kind": row["proposed_kind"]} for row in packet["rows"]]})

    monkeypatch.setattr(services, "llm", LLM())
    client, base = qa
    response = client.post(f"{base}/lists/{data['id']}/imports/{plan['id']}/analyze", json={"plan_revision": plan["plan_revision"]})
    assert response.status_code == 200, response.text
    assert all(target is None for target in response.json()["matches"].values())


def test_analysis_uses_fresh_identity_for_unproven_reply_columns_and_refreshes_review(qa, monkeypatch):
    from src.qa import store
    from src.services import services

    monkeypatch.setattr(services, "llm", None)
    data = mutate(qa, create(qa), "/questions", {"text": "Describe cabinet access."})
    qid, cid = data["questions"][0]["id"], data["columns"][0]["id"]
    data = mutate(qa, data, f"/questions/{qid}/replies/{cid}", {"body": "A caretaker holds the cabinet key."}, "put")
    plan = upload(qa, data, [["Question", "Operator note"], ["Describe cabinet access.", "Request a complete access redesign."]])
    assert plan["column_matches"]["1"] is None
    plan.update(content_choices={"stale-choice": "keep"}, retained_questions=[qid], blank_columns=["1", "99"])
    with store.connection("col_qa", "update") as conn, conn:
        conn.execute("UPDATE qa_imports SET plan_json=? WHERE id=?", (json.dumps(plan), plan["id"]))

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = json.loads(prompt.split("\n", 1)[1])
            if packet.get("stage") == "columns":
                return json.dumps({"columns": [{"key": "1", "target": cid, "certainty": "high"}]})
            return json.dumps({"rows": [{"key": row["key"], "kind": row["proposed_kind"]} for row in packet["rows"]]})

    monkeypatch.setattr(services, "llm", LLM())
    client, base = qa
    response = client.post(f"{base}/lists/{data['id']}/imports/{plan['id']}/analyze", json={"plan_revision": plan["plan_revision"]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["column_matches"]["1"] == "new"
    assert not result.get("content_choices")
    assert result["retained_questions"] == []
    assert result["blank_columns"] == ["1"]
    assert "review" in result
    assert any(column["id"] == cid for column in result["review"]["kept_columns"])


def test_manual_question_assignment_survives_an_ai_competitor(qa, monkeypatch):
    from src.services import services

    monkeypatch.setattr(services, "llm", None)
    data = mutate(qa, create(qa), "/questions", {"text": "Describe the specimen cabinet access."})
    qid = data["questions"][0]["id"]
    plan = upload(qa, data, [["Question"], ["Describe the specimen cabinet access."], ["Describe the specimen cabinet access rules."]])
    client, base = qa
    path = f"{base}/lists/{data['id']}/imports/{plan['id']}"
    response = client.patch(path, json={"plan_revision": plan["plan_revision"], "matches": {"2": qid, "3": None}})
    assert response.status_code == 200, response.text
    plan = response.json()

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = json.loads(prompt.split("\n", 1)[1])
            if packet.get("stage") == "identity":
                return json.dumps({"rows": [{"key": row["key"], "target": qid, "certainty": "high"} for row in packet["rows"]]})
            return json.dumps({"rows": [{"key": row["key"], "kind": row["proposed_kind"]} for row in packet["rows"]]})

    monkeypatch.setattr(services, "llm", LLM())
    response = client.post(path + "/analyze", json={"plan_revision": plan["plan_revision"]})
    assert response.status_code == 200, response.text
    assert response.json()["matches"] == {"2": qid, "3": None}
