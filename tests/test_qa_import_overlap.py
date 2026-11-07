"""Safe source-boundary edits retain only unchanged, validated import decisions."""

import json
from io import BytesIO

import openpyxl
import pytest

from src.qa import store
from tests.test_qa import create, mutate, qa_fixture  # noqa: F401


def patch(qa, detail, plan, **changes):
    client, base = qa
    response = client.patch(f"{base}/lists/{detail['id']}/imports/{plan['id']}", json={"plan_revision": plan["plan_revision"], **changes})
    assert response.status_code == 200, response.text
    return response.json()


def prepared(qa, monkeypatch):
    from src.services import services

    monkeypatch.setattr(services, "llm", None)
    detail = mutate(qa, create(qa), "/questions", {"text": "Describe the specimen cabinet access."})
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Register"
    for row in [
        ["Ref", "Question", "Response", "Coordinator"],
        ["R-2", "Explain who may open the specimen cabinet.", "Keep a signed access log.", "Storage team"],
        ["R-3", "Confirm its locking mechanism.", "", "Facilities team"],
        ["", "Use the room guide when responding.", "", "Guidance only"],
        ["R-5", "Provide the annex seating plan.", "", "Visitor team"],
    ]:
        sheet.append(row)
    other = workbook.create_sheet("Alternative")
    for row in [
        ["Ref", "Question", "Response", "Coordinator"],
        ["R-2", "Request the pond inspection record.", "It is held by the caretaker.", "Pond team"],
        ["R-3", "Confirm the garden gate hours.", "", "Reception"],
        ["R-4", "Provide the visitor route map.", "", "Visitor team"],
    ]:
        other.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    client, base = qa
    response = client.post(f"{base}/lists/{detail['id']}/imports", files={"file": ("example.xlsx", buffer.getvalue())}, data={"expected_revision": detail["revision"]})
    assert response.status_code == 200, response.text
    plan = response.json()
    plan = patch(qa, detail, plan, region={**plan["region"], "end_row": 4})
    plan = patch(qa, detail, plan, row_actions={"3": "child", "4": "note"}, parents={"3": "2"})
    qid, cid = detail["questions"][0]["id"], detail["columns"][0]["id"]
    plan = patch(qa, detail, plan, matches={"2": qid, "3": "new"}, column_matches={"2": cid})
    choices = {conflict["id"]: "incoming" for conflict in plan["review"]["conflicts"]}
    plan = patch(qa, detail, plan, content_choices=choices, blank_columns=["2"])
    return detail, plan, qid, cid


@pytest.mark.parametrize("boundary", ["region", "header"])
def test_safe_end_row_extension_or_unchanged_header_keeps_manual_overlaps_and_conflict_choices(qa, monkeypatch, boundary):
    detail, plan, qid, cid = prepared(qa, monkeypatch)
    previous_choices = {
        conflict["id"]: plan["content_choices"][conflict["id"]]
        for conflict in plan["review"]["conflicts"] if conflict["kind"] == "question"
    }
    assert previous_choices
    changes = {"region": {**plan["region"], "end_row": 5}} if boundary == "region" else {"header_row": 1}
    updated = patch(qa, detail, plan, **changes)
    assert updated["region"]["end_row"] == 5
    assert updated["manual_row_actions"] == {"3": "child", "4": "note"}
    assert updated["manual_parents"] == {"3": "2"}
    assert updated["matches"]["2"] == qid and updated["matches"]["3"] == "new"
    assert updated["manual_matches"] == {"2": qid, "3": "new"}
    assert updated["column_matches"]["2"] == cid
    assert updated["manual_column_matches"] == {"2": cid}
    assert all(updated["content_choices"].get(key) == value for key, value in previous_choices.items())
    assert set(updated["content_choices"]) <= {conflict["id"] for conflict in updated["review"]["conflicts"]}
    assert not updated.get("blank_columns"), "A whole-column clear must not silently expand to new rows"
    assert next(row for row in updated["table_rows"] if row["key"] == "3")["parent_key"] == "2"
    assert next(row for row in updated["table_rows"] if row["key"] == "4")["kind"] == "note"


def test_unchanged_question_scope_keeps_valid_content_and_blank_clear_choices(qa, monkeypatch):
    detail, plan, _, _ = prepared(qa, monkeypatch)
    updated = patch(qa, detail, plan, region=plan["region"])
    assert updated["content_choices"] == plan["content_choices"]
    assert updated["blank_columns"] == ["2"]


def test_shrinking_source_region_removes_decisions_for_rows_outside_it(qa, monkeypatch):
    detail, plan, qid, cid = prepared(qa, monkeypatch)
    updated = patch(qa, detail, plan, region={**plan["region"], "end_row": 2})
    assert updated.get("manual_row_actions", {}) == {}
    assert updated.get("manual_parents", {}) == {}
    assert updated["manual_matches"] == {"2": qid}
    assert updated["manual_column_matches"] == {"2": cid}
    assert set(updated["matches"]) == {"2"}


def test_header_boundary_removing_a_parent_never_leaves_an_invalid_manual_edge(qa, monkeypatch):
    detail, plan, _, _ = prepared(qa, monkeypatch)
    updated = patch(qa, detail, plan, region={**plan["region"], "header_end_row": 2}, mapping=plan["mapping"])
    assert not updated.get("manual_parents")
    assert not updated.get("parents", {}).get("3")
    assert next(row for row in updated["table_rows"] if row["key"] == "3")["kind"] == "review"
    assert "2" not in updated.get("manual_matches", {})


def test_removing_a_parent_invalidates_dependent_descendant_edges_without_raising(qa, monkeypatch):
    detail, plan, _, _ = prepared(qa, monkeypatch)
    plan = patch(qa, detail, plan, row_actions={"4": "child"}, parents={"4": "3"})
    updated = patch(qa, detail, plan, region={**plan["region"], "header_end_row": 2}, mapping=plan["mapping"])
    assert not updated.get("manual_parents")
    assert all(next(row for row in updated["table_rows"] if row["key"] == key)["kind"] == "review" for key in ["3", "4"])


def test_reclassifying_child_as_note_discards_its_previous_manual_parent(qa, monkeypatch):
    detail, plan, _, _ = prepared(qa, monkeypatch)
    updated = patch(qa, detail, plan, row_actions={"3": "note"})
    assert "3" not in updated.get("manual_parents", {})
    assert not updated.get("parents", {}).get("3")
    assert updated["manual_row_actions"]["3"] == "note"
    assert next(row for row in updated["table_rows"] if row["key"] == "3")["kind"] == "note"


def test_sheet_changes_clear_source_bound_decisions_despite_matching_row_coordinates(qa, monkeypatch):
    detail, plan, _, _ = prepared(qa, monkeypatch)
    updated = patch(qa, detail, plan, sheet="Alternative", header_row=1)
    assert not updated.get("manual_row_actions")
    assert not updated.get("manual_parents")
    assert not updated.get("manual_matches")
    assert not updated.get("manual_column_matches")
    assert not updated.get("content_choices")
    assert not updated.get("blank_columns")


def test_role_changes_clear_affected_question_and_reply_column_decisions(qa, monkeypatch):
    detail, plan, _, _ = prepared(qa, monkeypatch)
    updated = patch(qa, detail, plan, mapping={"question": 1, "number": 0, "replies": [3], "extras": [2]})
    assert not updated.get("manual_row_actions")
    assert not updated.get("manual_parents")
    assert not updated.get("manual_matches")
    assert not updated.get("manual_column_matches")
    assert not updated.get("content_choices")
    assert not updated.get("blank_columns")


def test_changed_physical_row_invalidates_only_its_own_source_bound_decisions(qa, monkeypatch):
    detail, plan, _, cid = prepared(qa, monkeypatch)
    # A stored source snapshot from a previous parser no longer agrees with the original cell.
    next(row for row in plan["table_rows"] if row["key"] == "2")["values"][1] = "An outdated source snapshot."
    with store.connection("col_qa", "update") as conn, conn:
        conn.execute("UPDATE qa_imports SET plan_json=? WHERE id=?", (json.dumps(plan), plan["id"]))
    updated = patch(qa, detail, plan, region={**plan["region"], "end_row": 5})
    assert "2" not in updated.get("manual_matches", {})
    assert not updated.get("manual_parents")
    assert updated["manual_row_actions"]["4"] == "note"
    assert updated["manual_column_matches"]["2"] == cid
    assert next(row for row in updated["table_rows"] if row["key"] == "3")["kind"] == "review"
