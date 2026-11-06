"""Ordered correspondence, using invented botanical garden records."""

from io import BytesIO

import openpyxl
import pytest
from fastapi import HTTPException
from openpyxl.styles import Alignment, Font, PatternFill

from tests.test_qa import create, mutate, qa_fixture  # noqa: F401
from tests.test_qa_excel import commit
from tests.test_qa_flexible import exported, upload_blob


def test_reordered_whole_row_references_preserve_membership_or_reject():
    from src.qa.workbook_layout import GridMap

    grid = GridMap([], [], 5, 0, 4, 0)
    grid.row_mapping = {2: 4, 3: 5, 4: 2, 5: 3}
    grid.row_end, grid.row_delta = 5, 0
    assert grid.reference("$2:$3") == "$4:$5"
    with pytest.raises(HTTPException) as exc:
        grid.reference("$2:$4")
    assert exc.value.detail["code"] == "unsupported_layout_change"


def test_missing_child_stays_with_parent_when_incoming_parent_changes_group(qa):
    d = imported(qa)
    parent, child, other = d["questions"]
    wb = openpyxl.load_workbook(BytesIO(workbook()))
    ws = wb["Register"]
    values = [c.value for c in ws[4]]
    ws.unmerge_cells("A6:D6")
    ws.delete_rows(4, 2)
    ws.merge_cells("A4:D4")
    ws.append(values)
    raw = BytesIO()
    wb.save(raw)
    p = upload_blob(qa, d, raw.getvalue())
    d = commit(qa, d, p)
    current = {q["id"]: q for q in d["questions"]}
    assert not current[child["id"]]["included"]
    assert (
        current[child["id"]]["group_id"]
        == current[parent["id"]]["group_id"]
        == current[other["id"]]["group_id"]
    )
    ids = [item["id"] for item in d["outline"]]
    assert ids.index(child["id"]) == ids.index(parent["id"]) + 1


def workbook():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Register"
    ws.append(["No", "Question", "Reply", "Owner"])
    ws.append(["Seed room"])
    ws.merge_cells("A2:D2")
    ws["A2"].font = Font(bold=True)
    ws.append(["Read the attached room guidance before responding."])
    ws.merge_cells("A3:D3")
    ws.append(["1", "Describe the seed cabinet.", "Cabinet has four shelves.", "Garden desk"])
    ws.append(["1.1", "Confirm its humidity setting.", "Thirty percent.", "Garden desk"])
    ws["B5"].alignment = Alignment(indent=1)
    ws.append(["Glasshouse"])
    ws.merge_cells("A6:D6")
    ws["A6"].font = Font(bold=True)
    ws.append(["2", "Explain the watering schedule.", "Water on alternate days.", "Plant team"])
    ws["B4"].fill = PatternFill("solid", fgColor="CCEEDD")
    ws["B7"].fill = PatternFill("solid", fgColor="EEDDCC")
    wb.create_sheet("Cover")["A1"] = "=Register!B4"
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def imported(qa):
    d = create(qa)
    p = upload_blob(qa, d, workbook())
    client, base = qa
    r = client.patch(
        f"{base}/lists/{d['id']}/imports/{p['id']}",
        json={
            "plan_revision": p["plan_revision"],
            "row_actions": {"3": "note"},
        },
    )
    assert r.status_code == 200, r.text
    return commit(qa, d, r.json())


def test_outline_preserves_notes_groups_and_independent_child_replies(qa):
    d = imported(qa)
    assert [i["kind"] for i in d["outline"]] == [
        "group",
        "note",
        "question",
        "question",
        "group",
        "question",
    ]
    parent, child, other = d["questions"]
    assert child["parent_id"] == parent["id"]
    assert child["group_id"] == parent["group_id"] != other["group_id"]
    assert child["replies"][0]["body"] == "Thirty percent."
    assert d["outline"][1]["text"].startswith("Read the attached")
    assert {f["label"]: f["value"] for f in child["fields"]} == {"Owner": "Garden desk"}


def test_move_parent_moves_children_and_preserves_frozen_version(qa):
    d = imported(qa)
    old = d["version"]["id"]
    parent, child, other = d["questions"]
    d = mutate(qa, d, "/versions", {})
    d = mutate(qa, d, f"/outline/{parent['id']}/move", {"group_id": other["group_id"]})
    questions = {q["id"]: q for q in d["questions"]}
    assert (
        questions[parent["id"]]["group_id"]
        == questions[child["id"]]["group_id"]
        == other["group_id"]
    )
    assert questions[child["id"]]["parent_id"] == parent["id"]
    client, base = qa
    history = client.get(f"{base}/lists/{d['id']}?version_id={old}").json()
    assert history["questions"][0]["group_id"] == parent["group_id"]


def test_parent_cycle_and_foreign_group_rejected_atomically(qa):
    d = imported(qa)
    parent, child, _ = d["questions"]
    client, base = qa
    for payload in ({"parent_id": child["id"]}, {"group_id": "missing-group"}):
        r = client.post(
            f"{base}/lists/{d['id']}/outline/{parent['id']}/move",
            json={"expected_revision": d["revision"], **payload},
        )
        assert r.status_code == 422, r.text
    assert client.get(f"{base}/lists/{d['id']}").json()["revision"] == d["revision"]


def test_group_addition_note_move_and_excel_roundtrip(qa):
    d = imported(qa)
    parent, child, other = d["questions"]
    d = mutate(
        qa,
        d,
        "/questions",
        {"text": "Provide the cabinet inventory.", "group_id": parent["group_id"]},
    )
    note = next(i for i in d["outline"] if i["kind"] == "note")
    d = mutate(
        qa,
        d,
        f"/outline/{note['id']}/move",
        {"group_id": other["group_id"], "before_id": other["id"]},
    )
    raw = exported(qa, d)
    wb = openpyxl.load_workbook(BytesIO(raw))
    ws = wb["Register"]
    contents = [ws.cell(r, 2).value or ws.cell(r, 1).value for r in range(2, ws.max_row + 1)]
    assert contents == [
        "Seed room",
        parent["text"],
        child["text"],
        "Provide the cabinet inventory.",
        "Glasshouse",
        note["text"],
        other["text"],
    ]
    assert ws["B5"].fill.fgColor.rgb == "00CCEEDD"
    assert wb["Cover"]["A1"].value == "=Register!B3"
    p = upload_blob(qa, d, raw)
    assert p["matches"]["3"] == parent["id"]
    assert next(r for r in p["rows"] if r["key"] == "4")["parent_key"] == "3"


def test_blank_list_group_child_and_note_export(qa):
    d = mutate(qa, create(qa), "/outline", {"kind": "group", "text": "Plant records"})
    gid = d["outline"][0]["id"]
    d = mutate(
        qa,
        d,
        "/outline",
        {"kind": "note", "text": "Use the revised nursery register.", "group_id": gid},
    )
    d = mutate(qa, d, "/questions", {"text": "Describe the potting bench.", "group_id": gid})
    parent = d["questions"][0]["id"]
    d = mutate(qa, d, "/questions", {"text": "Confirm the bench dimensions.", "parent_id": parent})
    ws = openpyxl.load_workbook(BytesIO(exported(qa, d))).active
    assert ws["A5"].value == "1" and ws["B5"].value == "Plant records"
    assert ws["A6"].value == "Use the revised nursery register."
    assert ws["B8"].alignment.indent == 1


def test_removing_parent_omits_children_and_restoring_retains_relations(qa):
    d = imported(qa)
    parent, child, _ = d["questions"]
    d = mutate(qa, d, f"/questions/{parent['id']}/membership", {"included": False})
    assert not next(q for q in d["questions"] if q["id"] == child["id"])["included"]
    d = mutate(
        qa, d, f"/questions/{parent['id']}/membership", {"included": True, "restore_replies": True}
    )
    child_now = next(q for q in d["questions"] if q["id"] == child["id"])
    assert child_now["included"] and child_now["parent_id"] == parent["id"]


def test_move_can_be_undone_without_erasing_later_edits(qa):
    d = imported(qa)
    original = d["outline"]
    parent, _, other = d["questions"]
    d = mutate(qa, d, f"/outline/{parent['id']}/move", {"group_id": other["group_id"]})
    d = mutate(qa, d, "/outline/undo", {})
    assert d["outline"] == original
    d = mutate(qa, d, f"/outline/{parent['id']}/move", {"group_id": other["group_id"]})
    d = mutate(
        qa, d, f"/questions/{parent['id']}", {"text": "Describe the updated cabinet."}, "patch"
    )
    client, base = qa
    r = client.post(
        f"{base}/lists/{d['id']}/outline/undo", json={"expected_revision": d["revision"]}
    )
    assert r.status_code == 409


def test_external_group_rename_uses_matched_members_and_keeps_parent_identity(qa):
    d = imported(qa)
    old_group = d["questions"][0]["group_id"]
    wb = openpyxl.load_workbook(BytesIO(workbook()))
    wb.active["A2"] = "Propagation room"
    out = BytesIO()
    wb.save(out)
    d = commit(qa, d, upload_blob(qa, d, out.getvalue()))
    assert d["questions"][0]["group_id"] == old_group
    assert d["questions"][0]["group"] == "Propagation room"


def test_new_group_and_deleted_note_export_without_dropping_other_cells(qa):
    d = imported(qa)
    d = mutate(qa, d, "/outline", {"kind": "group", "text": "Tool room"})
    gid = next(i["id"] for i in d["outline"] if i.get("text") == "Tool room")
    d = mutate(qa, d, "/questions", {"text": "Describe the tool rack.", "group_id": gid})
    note = next(i for i in d["outline"] if i["kind"] == "note")
    d = mutate(qa, d, f"/outline/{note['id']}", {"remove": True}, "patch")
    p = upload_blob(qa, d, exported(qa, d))
    last = p["rows"][-1]
    assert last["text"] == "Describe the tool rack." and last["group"] == "Tool room"
    assert all(r["kind"] != "note" for r in p["table_rows"])


def test_detaching_child_does_not_change_its_external_number_or_recreate_parent(qa):
    d = imported(qa)
    child = d["questions"][1]
    d = mutate(qa, d, f"/outline/{child['id']}/move", {"parent_id": ""})
    p = upload_blob(qa, d, exported(qa, d))
    row = next(r for r in p["rows"] if r["text"] == child["text"])
    assert next(q for q in d["questions"] if q["id"] == child["id"])["external_number"] == "1.1"
    assert row["values"][p["mapping"]["number"]] == "1.2"
    assert not row.get("parent_key")
