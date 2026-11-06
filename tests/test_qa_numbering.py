"""Display numbering follows the outline, independently of question identity."""

from io import BytesIO

import openpyxl
import pytest
from openpyxl.worksheet.views import Pane

from tests.test_qa import create, mutate, qa_fixture  # noqa: F401
from tests.test_qa_excel import commit, upload
from tests.test_qa_flexible import exported, sample, upload_blob
from tests.test_qa_outline import imported


def numbers(data):
    return {q["id"]: q["display_number"] for q in data["questions"] if q["included"]}


def test_blank_list_numbers_default_and_move_without_changing_identity(qa):
    d = mutate(qa, create(qa), "/questions", {"text": "Describe the herb rack."})
    first = d["questions"][0]["id"]
    d = mutate(qa, d, "/questions", {"text": "Confirm the planting schedule."})
    second = d["questions"][1]["id"]
    assert numbers(d) == {first: "1", second: "2"}
    d = mutate(qa, d, f"/outline/{second}/move", {"before_id": first})
    assert numbers(d) == {second: "1", first: "2"}
    ws = openpyxl.load_workbook(BytesIO(exported(qa, d))).active
    assert [ws.cell(r, 1).value for r in (5, 6)] == ["1", "2"]
    client, base = qa
    overview = client.get(f"{base}/questions").json()
    assert {q["id"]: q["display_number"] for q in overview} == numbers(d)


def test_imported_numbers_preserved_until_structure_changes_and_history_stays_frozen(qa):
    d = imported(qa)
    parent, child, other = d["questions"]
    assert numbers(d) == {parent["id"]: "1", child["id"]: "1.1", other["id"]: "2"}
    original = numbers(d)
    note = next(i for i in d["outline"] if i["kind"] == "note")
    d = mutate(
        qa,
        d,
        f"/outline/{note['id']}/move",
        {"before_id": other["id"], "group_id": other["group_id"]},
    )
    assert numbers(d) == original, "Notes do not consume a number"
    old = d["version"]["id"]
    d = mutate(qa, d, "/versions", {})
    d = mutate(qa, d, f"/outline/{parent['id']}/move", {"group_id": other["group_id"]})
    assert numbers(d) == {other["id"]: "2.1", parent["id"]: "2.2", child["id"]: "2.2.1"}
    assert next(q for q in d["questions"] if q["id"] == child["id"])["external_number"] == "1.1"
    client, base = qa
    history = client.get(f"{base}/lists/{d['id']}?version_id={old}").json()
    assert numbers(history) == original
    d = mutate(qa, d, "/outline/undo", {})
    assert numbers(d) == original


def test_nested_children_membership_and_groups_have_stable_hierarchical_numbers(qa):
    d = mutate(qa, create(qa), "/outline", {"kind": "group", "text": "Nursery equipment"})
    group = d["outline"][0]["id"]
    d = mutate(qa, d, "/questions", {"text": "Describe the planter.", "group_id": group})
    parent = d["questions"][0]["id"]
    d = mutate(qa, d, "/questions", {"text": "Confirm the planter depth.", "parent_id": parent})
    child = d["questions"][1]["id"]
    d = mutate(qa, d, "/questions", {"text": "Specify the measuring method.", "parent_id": child})
    leaf = d["questions"][2]["id"]
    d = mutate(qa, d, "/questions", {"text": "Describe the watering can.", "group_id": group})
    sibling = d["questions"][-1]["id"]
    assert numbers(d) == {parent: "1.1", child: "1.1.1", leaf: "1.1.1.1", sibling: "1.2"}
    assert d["outline"][0]["display_number"] == "1"
    d = mutate(qa, d, f"/questions/{parent}/membership", {"included": False})
    assert numbers(d) == {sibling: "1.1"}
    assert all(not q["display_number"] for q in d["questions"] if not q["included"])
    d = mutate(qa, d, f"/questions/{parent}/membership", {"included": True})
    # Restored locally-created questions are appended within their group.
    assert numbers(d)[leaf] == "1.2.1.1"


def test_original_number_column_is_updated_after_reorder_and_reimport_matches_text(qa):
    d = create(qa)
    d = commit(
        qa,
        d,
        upload(
            qa,
            d,
            [
                ["No", "Question", "Reply"],
                ["R-8", "Describe the moss tray.", ""],
                ["R-12", "Confirm the seed count.", ""],
            ],
        ),
    )
    first, second = d["questions"]
    assert numbers(d) == {first["id"]: "R-8", second["id"]: "R-12"}
    d = mutate(qa, d, f"/outline/{second['id']}/move", {"before_id": first["id"]})
    raw = exported(qa, d)
    ws = openpyxl.load_workbook(BytesIO(raw)).active
    assert [ws.cell(r, 1).value for r in (2, 3)] == ["1", "2"]
    p = upload_blob(qa, d, raw)
    assert p["matches"] == {"2": second["id"], "3": first["id"]}
    d = commit(qa, d, p)
    assert numbers(d) == {second["id"]: "1", first["id"]: "2"}


def test_group_number_cells_follow_reordered_groups_and_new_questions(qa):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["No", "Title", "Question", "Reply"])
    for row in [
        ["7", "Seed room"],
        ["7.1", "", "Describe the tray.", ""],
        ["9", "Potting room"],
        ["9.1", "", "Describe the bench.", ""],
    ]:
        ws.append(row)
    raw = BytesIO()
    wb.save(raw)
    d = create(qa)
    p = upload_blob(qa, d, raw.getvalue())
    client, base = qa
    r = client.patch(
        f"{base}/lists/{d['id']}/imports/{p['id']}",
        json={"plan_revision": p["plan_revision"], "row_actions": {"2": "section", "4": "section"}},
    )
    assert r.status_code == 200, r.text
    d = commit(qa, d, r.json())
    groups = [i for i in d["outline"] if i["kind"] == "group"]
    assert [g["display_number"] for g in groups] == ["7", "9"]
    d = mutate(qa, d, f"/outline/{groups[1]['id']}/move", {"before_id": groups[0]["id"]})
    d = mutate(
        qa, d, "/questions", {"text": "Confirm the room width.", "group_id": groups[1]["id"]}
    )
    d = mutate(qa, d, "/outline", {"kind": "group", "text": "Tool room"})
    group_id = d["outline"][-1]["id"]
    d = mutate(qa, d, "/questions", {"text": "Describe the shovel rack.", "group_id": group_id})
    ws = openpyxl.load_workbook(BytesIO(exported(qa, d))).active
    assert [ws.cell(r, 1).value for r in range(2, 9)] == ["1", "1.1", "1.2", "2", "2.1", "3", "3.1"]
    assert ws["B2"].value == "Potting room" and ws["B5"].value == "Seed room"
    assert ws["B7"].value == "Tool room"


@pytest.mark.parametrize("change", ["move", "remove", "split"])
def test_excel_view_coordinates_stay_fixed_when_content_moves(qa, change):
    wb = openpyxl.load_workbook(BytesIO(sample()))
    ws = wb["Register"]
    if change == "split":
        ws.sheet_view.pane = Pane(xSplit=1800, ySplit=2700, topLeftCell="D6", state="split")
    expected = str(ws.views)
    raw = BytesIO()
    wb.save(raw)
    d = create(qa)
    d = commit(qa, d, upload_blob(qa, d, raw.getvalue()))
    first, second = d["questions"]
    if change == "remove":
        d = mutate(qa, d, f"/questions/{first['id']}/membership", {"included": False})
    else:
        d = mutate(
            qa,
            d,
            f"/outline/{second['id']}/move",
            {"before_id": first["id"], "group_id": first["group_id"]},
        )
    result = openpyxl.load_workbook(BytesIO(exported(qa, d)))
    assert str(result["Register"].views) == expected
