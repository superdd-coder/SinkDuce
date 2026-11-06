"""Shared OOXML formulas must keep the references of each original cell."""

from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile

import openpyxl
import pytest
from fastapi import HTTPException

from src.qa.workbook_layout import parse, serialize, tag, write_original


def _case(*, reorder=True):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Gallery"
    sheet.append(["Question", "Units", "Calculation"])
    related = workbook.create_sheet("Review")
    unrelated = workbook.create_sheet("Notes")
    questions, outline, baselines = [], [], {}
    for index, value in enumerate((4, 9, 2), start=2):
        question_id = f"room-{index}"
        text = f"Confirm the display plan for room {index}."
        sheet.append([text, value, f"=$B{index}+$B$2"])
        related[f"A{index}"] = f"=Gallery!B{index}+1"
        related[f"C{index}"] = f"=D{index}+2"
        unrelated[f"A{index}"] = f"=B{index}+3"
        questions.append(
            {
                "id": question_id,
                "included": True,
                "text": text,
                "external_number": "",
                "status": "",
                "type": "",
                "replies": [],
            }
        )
        outline.append({"id": question_id, "kind": "question", "source_rows": [index]})
        baselines[question_id] = [text, str(value), ""]
    metadata = {
        "layout": {
            "sheet": "Gallery",
            "header_row": 1,
            "region": {"start_column": 1, "end_column": 3, "header_end_row": 1, "end_row": 4},
            "mapping": {"question": 0},
            "rows": {item["id"]: item["source_rows"] for item in outline},
            "columns": {},
            "header_labels": {},
            "baseline": baselines,
            "options": {"statuses": [], "types": []},
            "outline": deepcopy(outline),
        },
        "options": {"statuses": [], "types": []},
        "values": {},
        "outline": outline[2:] + outline[:2] if reorder else outline,
    }
    snapshot = {"questions": questions, "columns": []}
    raw = BytesIO()
    workbook.save(raw)
    return raw.getvalue(), snapshot, metadata


def _shared_workbook(source, *, broken=None):
    output = BytesIO()
    with ZipFile(BytesIO(source)) as original, ZipFile(output, "w") as result:
        for name in original.namelist():
            data = original.read(name)
            groups = {
                "xl/worksheets/sheet1.xml": ["C"],
                "xl/worksheets/sheet2.xml": ["A", "C"],
                "xl/worksheets/sheet3.xml": ["A"],
            }.get(name)
            if groups:
                root = parse(data)
                cells = {cell.get("r"): cell.find(tag("f")) for cell in root.iter(tag("c"))}
                for group_id, col in enumerate(groups):
                    for row in range(2, 5):
                        formula = cells[f"{col}{row}"]
                        formula.set("t", "shared")
                        formula.set("si", str(group_id))
                        if row == 2:
                            formula.set("ref", f"{col}2:{col}4")
                        else:
                            formula.text = None
                if name.endswith("sheet1.xml"):
                    first = cells["C2"]
                    if broken == "missing-master":
                        first.text = None
                    elif broken == "duplicate-master":
                        cells["C3"].text = "$B3+$B$2"
                        cells["C3"].set("ref", "C2:C4")
                    elif broken == "outside-range":
                        first.set("ref", "C2:C3")
                    elif broken in ("array", "dataTable"):
                        first.set("t", broken)
                        for row in range(2, 5):
                            cells[f"C{row}"].attrib.pop("si", None)
                            if row > 2:
                                cells[f"C{row}"].getparent().remove(cells[f"C{row}"])
                data = serialize(root)
            result.writestr(name, data)
    return output.getvalue()


def _formulas(blob, sheet):
    with ZipFile(BytesIO(blob)) as package:
        root = parse(package.read(f"xl/worksheets/sheet{sheet}.xml"))
    return {
        node.getparent().get("r"): (node.text, dict(node.attrib)) for node in root.iter(tag("f"))
    }


def test_structural_export_expands_shared_formulas_at_original_coordinates_before_reordering():
    raw, snapshot, metadata = _case()
    output = write_original(_shared_workbook(raw), snapshot, metadata)
    formulas = _formulas(output, 1)
    assert formulas == {f"C{row}": (f"$B{row}+$B$3", {}) for row in range(2, 5)}
    sheet = openpyxl.load_workbook(BytesIO(output))["Gallery"]
    assert [sheet[f"B{row}"].value for row in range(2, 5)] == [2, 4, 9]


def test_only_affected_shared_formula_groups_on_other_sheets_are_expanded():
    raw, snapshot, metadata = _case()
    # Keep the master's referenced row in place. Changes to followers still
    # require expanding their group before rewriting references.
    first, second, third = metadata["layout"]["outline"]
    metadata["outline"] = [first, third, second]
    source = _shared_workbook(raw)
    output = write_original(source, snapshot, metadata)
    formulas = _formulas(output, 2)
    assert [formulas[f"A{row}"] for row in range(2, 5)] == [
        ("Gallery!B2+1", {}),
        ("Gallery!B4+1", {}),
        ("Gallery!B3+1", {}),
    ]
    original_formulas = _formulas(source, 2)
    assert [formulas[f"C{row}"] for row in range(2, 5)] == [
        original_formulas[f"C{row}"] for row in range(2, 5)
    ]
    with ZipFile(BytesIO(source)) as original, ZipFile(BytesIO(output)) as result:
        assert original.read("xl/worksheets/sheet3.xml") == result.read("xl/worksheets/sheet3.xml")


@pytest.mark.parametrize(
    "broken", ["missing-master", "duplicate-master", "outside-range", "array", "dataTable"]
)
def test_structural_export_rejects_formula_groups_it_cannot_safely_expand(broken):
    raw, snapshot, metadata = _case()
    with pytest.raises(HTTPException) as exc:
        write_original(_shared_workbook(raw, broken=broken), snapshot, metadata)
    assert exc.value.detail["code"] == "unsupported_layout_change"


def test_export_without_structural_changes_preserves_the_shared_group():
    raw, snapshot, metadata = _case(reorder=False)
    source = _shared_workbook(raw)
    result = write_original(source, snapshot, metadata)
    assert _formulas(result, 1) == _formulas(source, 1)
