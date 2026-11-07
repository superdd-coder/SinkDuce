"""Reply columns retained across imports remain usable in the exported workbook."""

from copy import deepcopy
from datetime import date
from io import BytesIO
from zipfile import ZipFile

import openpyxl
import pytest
from fastapi import HTTPException
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table

from src.qa.workbook_layout import parse, serialize, tag, write_original


def retained_case(*, existing_reply=False, hidden=False, trailing_fields=False, native_table=False):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Register"
    headers = ["Question"] + (["Response"] if existing_reply else [])
    values = ["Describe the gallery lighting schedule."] + (
        ["Lighting starts before opening."] if existing_reply else []
    )
    if trailing_fields:
        headers += ["Status", "Owner"]
        values += ["Open", "Gallery team"]
    sheet.append(headers)
    sheet.append(values)
    sheet.freeze_panes = "B2"
    if existing_reply:
        sheet.column_dimensions["B"].width = 29
        sheet.column_dimensions["B"].hidden = hidden
    if native_table:
        ref = f"A1:{openpyxl.utils.get_column_letter(len(headers))}2"
        sheet.add_table(Table(displayName="GalleryRegister", ref=ref))
        sheet.auto_filter.ref = ref
    source_columns = {"current": 1} if existing_reply else {}
    columns = (
        [{"id": "current", "label": "Response", "included": True}] if existing_reply else []
    ) + [{"id": "retained", "label": "Earlier response", "included": True}]
    snapshot = {
        "columns": columns,
        "questions": [
            {
                "id": "question",
                "included": True,
                "text": values[0],
                "external_number": "",
                "status": "",
                "type": "",
                "replies": [
                    {"column_id": "current", "body": "Lighting starts before opening."},
                    {"column_id": "retained", "body": "The lighting team will confirm."},
                ],
            }
        ],
    }
    metadata = {
        "layout": {
            "sheet": "Register",
            "header_row": 1,
            "region": {
                "start_column": 1,
                "end_column": len(headers),
                "header_end_row": 1,
                "end_row": 2,
            },
            "mapping": {"question": 0},
            "rows": {"question": [2]},
            "columns": source_columns,
            "header_labels": {"current": "Response"},
            "baseline": {"question": values},
            "options": {"statuses": [], "types": []},
        },
        "options": {"statuses": [], "types": []},
        "values": {},
    }
    return workbook, snapshot, metadata


def workbook_blob(workbook):
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def retained_export(**kwargs):
    workbook, snapshot, metadata = retained_case(**kwargs)
    return write_original(workbook_blob(workbook), snapshot, metadata)


def test_retained_reply_does_not_inherit_source_column_hidden_state():
    sheet = openpyxl.load_workbook(BytesIO(retained_export(existing_reply=True, hidden=True)))[
        "Register"
    ]

    assert sheet["C1"].value == "Earlier response"
    assert sheet["C2"].value == "The lighting team will confirm."
    assert sheet.column_dimensions["B"].hidden is True
    assert sheet.column_dimensions["C"].hidden is False
    assert sheet.column_dimensions["C"].width == 29
    assert sheet.freeze_panes == "B2"


def test_retained_reply_gets_readable_width_when_source_has_no_column_dimensions():
    blob = retained_export()
    sheet = openpyxl.load_workbook(BytesIO(blob))["Register"]

    assert sheet["B1"].value == "Earlier response"
    assert sheet["B2"].value == "The lighting team will confirm."
    assert sheet.column_dimensions["B"].width == 48
    assert sheet.column_dimensions["B"].hidden is False
    assert sheet.freeze_panes == "B2"
    with ZipFile(BytesIO(blob)) as package:
        root = parse(package.read("xl/worksheets/sheet1.xml"))
    assert list(root).index(root.find(tag("cols"))) < list(root).index(root.find(tag("sheetData")))


def test_retained_reply_is_inside_stored_dimension_and_streaming_read_range():
    blob = retained_export()
    with ZipFile(BytesIO(blob)) as package:
        root = parse(package.read("xl/worksheets/sheet1.xml"))

    assert root.find(tag("dimension")).get("ref") == "A1:B2"
    sheet = openpyxl.load_workbook(BytesIO(blob), read_only=True)["Register"]
    assert list(sheet.values) == [
        ("Question", "Earlier response"),
        ("Describe the gallery lighting schedule.", "The lighting team will confirm."),
    ]


def test_retained_reply_is_adjacent_to_previous_reply_and_keeps_reply_width():
    sheet = openpyxl.load_workbook(
        BytesIO(retained_export(existing_reply=True, trailing_fields=True))
    )["Register"]

    assert [cell.value for cell in sheet[1]] == [
        "Question",
        "Response",
        "Earlier response",
        "Status",
        "Owner",
    ]
    assert sheet["C2"].value == "The lighting team will confirm."
    assert sheet["D2"].value == "Open"
    assert sheet["E2"].value == "Gallery team"
    assert sheet.column_dimensions["C"].width == 29


def test_adjacent_reply_moves_formula_validation_and_filter_with_source_fields():
    workbook, snapshot, metadata = retained_case(existing_reply=True, trailing_fields=True)
    sheet = workbook["Register"]
    sheet["E2"] = "=LEN(D2)"
    workbook.create_sheet("Cover")["A1"] = "=Register!$D$2"
    validation = DataValidation(type="list", formula1='"Open,Closed"')
    sheet.add_data_validation(validation)
    validation.add("C2")
    sheet.auto_filter.ref = "A1:D2"
    sheet.auto_filter.add_filter_column(2, ["Open"])
    sheet["A4"] = "Reply notes"
    sheet.merge_cells("A4:B4")

    output = openpyxl.load_workbook(
        BytesIO(write_original(workbook_blob(workbook), snapshot, metadata))
    )
    sheet = output["Register"]
    assert sheet["F2"].value == "=LEN(E2)"
    assert output["Cover"]["A1"].value == "=Register!$E$2"
    assert str(sheet.data_validations.dataValidation[0].sqref) == "D2"
    assert sheet.auto_filter.ref == "A1:E2"
    assert sheet.auto_filter.filterColumn[0].colId == 3
    assert sheet.auto_filter.filterColumn[0].filters.filter == ["Open"]
    assert "A4:C4" in sheet.merged_cells


@pytest.mark.parametrize("existing_reply", [True, False])
def test_append_only_retained_reply_preserves_native_table_and_filter(existing_reply):
    workbook, snapshot, metadata = retained_case(
        existing_reply=existing_reply, trailing_fields=True, native_table=True
    )
    metadata["layout"]["outline"] = [{"id": "question", "kind": "question", "source_rows": [2]}]
    metadata["outline"] = deepcopy(metadata["layout"]["outline"])
    source = workbook_blob(workbook)
    blob = write_original(source, snapshot, metadata)
    sheet = openpyxl.load_workbook(BytesIO(blob))["Register"]

    assert len(sheet.tables) == 1
    table = sheet.tables["GalleryRegister"]
    expected_ref = "A1:D2" if existing_reply else "A1:C2"
    expected_headers = ["Question"] + (["Response"] if existing_reply else []) + ["Status", "Owner"]
    assert table.ref == expected_ref
    assert table.autoFilter.ref == expected_ref
    assert [column.name for column in table.tableColumns] == expected_headers
    assert sheet.auto_filter.ref == expected_ref
    assert sheet.freeze_panes == "B2"
    added_column = "E" if existing_reply else "D"
    assert sheet[f"{added_column}1"].value == "Earlier response"
    assert sheet[f"{added_column}2"].value == "The lighting team will confirm."
    with ZipFile(BytesIO(source)) as original, ZipFile(BytesIO(blob)) as result:
        assert original.read("xl/tables/table1.xml") == result.read("xl/tables/table1.xml")


@pytest.mark.parametrize(
    "change", ["delete_question", "add_question", "delete_reply", "reorder", "merge", "table_right"]
)
def test_table_append_keeps_rejecting_other_structural_changes(change):
    workbook, snapshot, metadata = retained_case(
        existing_reply=True, trailing_fields=True, native_table=True
    )
    sheet = workbook["Register"]
    if change == "delete_question":
        snapshot["questions"][0]["included"] = False
    elif change == "add_question":
        snapshot["questions"].append({**deepcopy(snapshot["questions"][0]), "id": "new_question"})
    elif change == "delete_reply":
        snapshot["columns"][0]["included"] = False
    elif change == "reorder":
        second = {**deepcopy(snapshot["questions"][0]), "id": "second_question"}
        snapshot["questions"].append(second)
        sheet.append(["Confirm the gallery closing procedure.", "Confirmed.", "Open", "Desk"])
        layout = metadata["layout"]
        layout["rows"][second["id"]] = [3]
        layout["baseline"][second["id"]] = [cell.value for cell in sheet[3]]
        layout["outline"] = [
            {"id": "question", "kind": "question", "source_rows": [2]},
            {"id": second["id"], "kind": "question", "source_rows": [3]},
        ]
        metadata["outline"] = list(reversed(layout["outline"]))
    elif change == "merge":
        sheet["A4"] = "Gallery register notes"
        sheet.merge_cells("A4:D4")
    else:
        sheet["G1"], sheet["H1"] = "Task", "Owner"
        sheet["G2"], sheet["H2"] = "Inventory", "Desk"
        sheet.add_table(Table(displayName="OtherRegister", ref="G1:H2"))

    with pytest.raises(HTTPException) as exc:
        write_original(workbook_blob(workbook), snapshot, metadata)
    assert exc.value.detail["code"] == "unsupported_layout_change"


def extra_field_case(source_value, selected):
    workbook, snapshot, metadata = retained_case(existing_reply=True, trailing_fields=True)
    workbook["Register"]["D2"] = source_value
    layout = metadata["layout"]
    layout["baseline"]["question"][3] = "" if source_value is None else str(source_value)
    layout["typed_baseline"] = {
        "question": [
            {"value": cell.value, "kind": cell.data_type} for cell in workbook["Register"][2]
        ]
    }
    layout["fields"] = {"owner": 3}
    metadata["values"] = {"question": {"owner": selected}}
    return workbook, snapshot, metadata


@pytest.mark.parametrize("typed_baseline", [True, False])
def test_preserved_local_extra_on_matched_row_is_exported(typed_baseline):
    workbook, snapshot, metadata = extra_field_case(None, {"value": "Archive crew", "kind": "s"})
    if not typed_baseline:
        metadata["layout"].pop("typed_baseline")

    blob = write_original(workbook_blob(workbook), snapshot, metadata)
    assert openpyxl.load_workbook(BytesIO(blob))["Register"]["E2"].value == "Archive crew"


@pytest.mark.parametrize(
    "source_value, selected, expected_value, expected_kind",
    [
        (None, {"value": 0, "kind": "n"}, 0, "n"),
        (True, {"value": False, "kind": "b"}, False, "b"),
        ("Later", {"value": "2035-02-09", "kind": "date"}, date(2035, 2, 9), "d"),
    ],
)
def test_selected_typed_extra_on_matched_row_retains_value_and_excel_type(
    source_value, selected, expected_value, expected_kind
):
    workbook, snapshot, metadata = extra_field_case(source_value, selected)

    blob = write_original(workbook_blob(workbook), snapshot, metadata)
    cell = openpyxl.load_workbook(BytesIO(blob))["Register"]["E2"]
    assert cell.value == expected_value
    assert cell.data_type == expected_kind


@pytest.mark.parametrize(
    "selected, expected_value, expected_kind",
    [
        ({"value": 7, "kind": "f"}, "=2+5", "f"),
        ({"value": 7, "kind": "n"}, 7, "n"),
        ({"value": 5, "kind": "f"}, 5, "n"),
    ],
)
def test_extra_formula_is_preserved_only_for_unchanged_typed_cached_value(
    selected, expected_value, expected_kind
):
    workbook, snapshot, metadata = extra_field_case("=2+5", selected)
    metadata["layout"]["typed_baseline"]["question"][3] = {"value": 7, "kind": "f"}
    metadata["layout"]["baseline"]["question"][3] = "7"
    with ZipFile(BytesIO(workbook_blob(workbook))) as source:
        parts = {name: source.read(name) for name in source.namelist()}
    root = parse(parts["xl/worksheets/sheet1.xml"])
    cell = next(cell for cell in root.iter(tag("c")) if cell.get("r") == "D2")
    cell.find(tag("v")).text = "7"
    parts["xl/worksheets/sheet1.xml"] = serialize(root)
    package = BytesIO()
    with ZipFile(package, "w") as output:
        for name, value in parts.items():
            output.writestr(name, value)

    blob = write_original(package.getvalue(), snapshot, metadata)
    cell = openpyxl.load_workbook(BytesIO(blob))["Register"]["E2"]
    assert cell.value == expected_value
    assert cell.data_type == expected_kind
    if expected_kind == "f":
        assert openpyxl.load_workbook(BytesIO(blob), data_only=True)["Register"]["E2"].value == 7


@pytest.mark.parametrize("local_value", ["Archive crew", "Preserved local owner"])
def test_inherited_extra_preserves_source_blank_until_the_selected_value_changes(local_value):
    workbook, snapshot, metadata = extra_field_case(None, {"value": local_value, "kind": "s"})
    sheet = workbook["Register"]
    sheet.insert_rows(2)
    sheet["A2"] = "Gallery operations"
    sheet["D2"] = "Archive crew"
    layout = metadata["layout"]
    layout["rows"]["question"] = [3]
    layout["region"]["end_row"] = 3
    inherited = deepcopy(layout["typed_baseline"]["question"])
    inherited[3] = {"value": "Archive crew", "kind": "s"}
    layout["effective_typed_baseline"] = {"question": inherited}

    blob = write_original(workbook_blob(workbook), snapshot, metadata)
    sheet = openpyxl.load_workbook(BytesIO(blob))["Register"]
    assert sheet["E2"].value == "Archive crew"
    assert sheet["E3"].value == (None if local_value == "Archive crew" else local_value)
