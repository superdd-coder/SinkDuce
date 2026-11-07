"""Column grouping round-trips through the original workbook layout."""

from io import BytesIO

import openpyxl
import pytest
from fastapi import HTTPException
from openpyxl.styles import Font

from src.qa import metadata, outline
from src.qa.workbook_layout import write_original


def group_case(labels, *, effective=None, inheritance=None, merge=None, physical_label=None):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Register"
    sheet.append(["Theme", "Question"])
    sheet.freeze_panes = "B2"
    effective = labels if effective is None else effective
    inheritance = [""] * len(labels) if inheritance is None else inheritance
    first_question = 3 if physical_label else 2
    region = {
        "start_column": 1,
        "end_column": 2,
        "header_end_row": 1,
        "end_row": len(labels) + first_question - 1,
    }
    mapping = {"group": 0, "question": 1, "replies": [], "extras": []}
    plan = {"id": "gallery-import", "region": region, "mapping": mapping, "rows": []}
    meta = metadata.defaults()
    layout = meta["layout"] = {
        "sheet": "Register",
        "header_row": 1,
        "region": region,
        "mapping": mapping,
        "rows": {},
        "columns": {},
        "baseline": {},
    }
    snapshot = {"columns": [], "questions": []}
    identities = {}
    if physical_label:
        sheet.append([physical_label, None])
        sheet["A2"].font = Font(bold=True)
    run = ""
    previous = None
    for r, (raw, label, inherited) in enumerate(
        zip(labels, effective, inheritance), first_question
    ):
        if label != previous:
            run = f"column:{r}" if label else ""
            if physical_label and label == physical_label:
                run = "2"
        previous = label
        question = f"Describe gallery procedure {r}."
        sheet.append([raw, question])
        qid = identities[str(r)] = f"question-{r}"
        values = [raw or "", question]
        plan["rows"].append(
            {
                "key": str(r),
                "kind": "question",
                "values": values,
                "group_key": run,
                "group": label or "",
                "group_inherited": inherited,
                "source_rows": [r],
            }
        )
        layout["rows"][qid] = [r]
        layout["baseline"][qid] = values
        snapshot["questions"].append(
            {
                "id": qid,
                "included": True,
                "text": question,
                "external_number": "",
                "status": "",
                "type": "",
                "replies": [],
            }
        )
    if merge:
        sheet.merge_cells(merge)
    if physical_label:
        plan["table_rows"] = [
            {"key": "2", "kind": "section", "values": [physical_label, ""]},
            *plan["rows"],
        ]
    outline.from_plan(meta, plan, identities)
    return workbook, snapshot, meta, plan, identities


def export_sheet(workbook, snapshot, meta):
    raw = BytesIO()
    workbook.save(raw)
    output = write_original(raw.getvalue(), snapshot, meta)
    return openpyxl.load_workbook(BytesIO(output))["Register"]


def test_column_runs_create_distinct_virtual_groups_and_no_export_heading_rows():
    workbook, snapshot, meta, _, _ = group_case(
        ["Collections", "Collections", "Visits", "Collections"]
    )
    groups = [item for item in meta["outline"] if item["kind"] == "group"]
    questions = [item for item in meta["outline"] if item["kind"] == "question"]

    assert [item["text"] for item in groups] == ["Collections", "Visits", "Collections"]
    assert all(not item["source_rows"] for item in groups)
    assert [item["group_id"] for item in questions] == [
        groups[0]["id"],
        groups[0]["id"],
        groups[1]["id"],
        groups[2]["id"],
    ]
    sheet = export_sheet(workbook, snapshot, meta)
    assert list(sheet.values) == list(workbook.active.values)
    assert sheet.freeze_panes == "B2"


def test_moving_question_to_column_group_updates_only_its_group_cell():
    workbook, snapshot, meta, _, identities = group_case(["Collections", "Collections", "Visits"])
    target = next(item for item in meta["outline"] if item.get("text") == "Visits")
    outline.move(meta["outline"], identities["2"], group_id=target["id"])

    sheet = export_sheet(workbook, snapshot, meta)
    assert list(sheet.values) == [
        ("Theme", "Question"),
        ("Collections", "Describe gallery procedure 3."),
        ("Visits", "Describe gallery procedure 4."),
        ("Visits", "Describe gallery procedure 2."),
    ]


@pytest.mark.parametrize("kind", ["merge", "bold"])
def test_renaming_sparse_group_updates_anchor_and_preserves_covered_blank(kind):
    workbook, snapshot, meta, _, _ = group_case(
        ["Collections", None],
        effective=["Collections", "Collections"],
        inheritance=["", kind],
        merge="A2:A3" if kind == "merge" else None,
    )
    if kind == "bold":
        workbook.active["A2"].font = Font(bold=True)
    unchanged = export_sheet(workbook, snapshot, meta)
    assert list(unchanged.values) == list(workbook.active.values)
    group = next(item for item in meta["outline"] if item["kind"] == "group")
    group["text"] = "Collections care"

    sheet = export_sheet(workbook, snapshot, meta)
    assert sheet["A2"].value == "Collections care"
    assert sheet["A3"].value is None
    assert ("A2:A3" in sheet.merged_cells) == (kind == "merge")
    assert sheet["A2"].font.bold == (kind == "bold")


@pytest.mark.parametrize("kind", ["merge", "bold"])
def test_export_rejects_assigning_one_member_of_sparse_source_run_to_another_group(kind):
    workbook, snapshot, meta, _, identities = group_case(
        ["Collections", None, "Visits"],
        effective=["Collections", "Collections", "Visits"],
        inheritance=["", kind, ""],
        merge="A2:A3" if kind == "merge" else None,
    )
    if kind == "bold":
        workbook.active["A2"].font = Font(bold=True)
    target = next(item for item in meta["outline"] if item.get("text") == "Visits")
    member = next(item for item in meta["outline"] if item["id"] == identities["3"])
    member["group_id"] = target["id"]

    with pytest.raises(HTTPException) as error:
        export_sheet(workbook, snapshot, meta)
    assert error.value.detail["code"] == "unsupported_layout_change"


def test_export_rejects_deleting_merged_group_anchor_while_a_member_remains():
    workbook, snapshot, meta, _, _ = group_case(
        ["Collections", None],
        effective=["Collections", "Collections"],
        inheritance=["", "merge"],
        merge="A2:A3",
    )
    snapshot["questions"][0]["included"] = False

    with pytest.raises(HTTPException) as error:
        export_sheet(workbook, snapshot, meta)
    assert error.value.detail["code"] == "unsupported_layout_change"


def test_physical_heading_rename_updates_same_label_column_alias_but_not_blank_fallback():
    workbook, snapshot, meta, _, _ = group_case(
        ["Collections", None],
        effective=["Collections", "Collections"],
        physical_label="Collections",
    )
    group = next(item for item in meta["outline"] if item["kind"] == "group")
    assert group["source_rows"] == [2]
    group["text"] = "Collections care"

    sheet = export_sheet(workbook, snapshot, meta)
    assert [sheet[f"A{r}"].value for r in (2, 3, 4)] == [
        "Collections care",
        "Collections care",
        None,
    ]


def test_complete_bold_sparse_groups_can_reorder_without_filling_inherited_cells():
    workbook, snapshot, meta, _, _ = group_case(
        ["Collections", None, "Visits", None],
        effective=["Collections", "Collections", "Visits", "Visits"],
        inheritance=["", "bold", "", "bold"],
    )
    for address in ("A2", "A4"):
        workbook.active[address].font = Font(bold=True)
    group = next(item for item in meta["outline"] if item.get("text") == "Collections")
    outline.move(meta["outline"], group["id"])

    sheet = export_sheet(workbook, snapshot, meta)
    assert list(sheet.values) == [
        ("Theme", "Question"),
        ("Visits", "Describe gallery procedure 4."),
        (None, "Describe gallery procedure 5."),
        ("Collections", "Describe gallery procedure 2."),
        (None, "Describe gallery procedure 3."),
    ]
    assert sheet["A2"].font.bold and sheet["A4"].font.bold
