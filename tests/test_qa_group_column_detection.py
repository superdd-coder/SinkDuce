"""Group recognition separates topic structure from additional fields."""

from io import BytesIO

import openpyxl
import pytest
from openpyxl.styles import Font
from openpyxl.worksheet.datavalidation import DataValidation

from src.qa import intelligence, workbook_reader


def workbook(headers, labels, *, merged=(), validation=None, bold=(), section_before=None):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(headers)
    for i, label in enumerate(labels, start=1):
        if i == section_before:
            sheet.append(["Exhibition records"])
        sheet.append(
            [label, f"Describe the equipment for gallery station {i}.", None, "Confirm"][
                : len(headers)
            ]
        )
    for address in merged:
        sheet.merge_cells(address)
    for address in bold:
        sheet[address].font = Font(bold=True)
    if validation:
        dropdown = DataValidation(type="list", formula1='"' + ",".join(validation) + '"')
        sheet.add_data_validation(dropdown)
        dropdown.add(f"A2:A{sheet.max_row}")
    out = BytesIO()
    book.save(out)
    raw = out.getvalue()
    return raw, workbook_reader.read_workbook(raw)


@pytest.mark.parametrize("heading", ["Group", "Topic", "Theme", "Section", "主题", "问题分组"])
def test_explicit_topic_header_maps_to_group(heading):
    _, parsed = workbook([heading, "Question", "Reply"], ["Storage", "Safety"])
    result = workbook_reader.default_mapping(parsed)
    assert result.get("group") == 0
    assert "type" not in result and 0 not in result["extras"]


@pytest.mark.parametrize("heading", ["Type", "Category"])
def test_ambiguous_header_is_group_when_body_forms_named_topic_blocks(heading):
    _, parsed = workbook(
        [heading, "Question", "Reply", "Question Type"],
        ["Collection storage", "Collection storage", "Visitor facilities", "Visitor facilities"],
    )
    result = workbook_reader.default_mapping(parsed)
    assert result.get("group") == 0
    assert result["extras"] == [3]


def test_vertical_topic_merge_supports_group_even_without_repeated_cell_values():
    _, parsed = workbook(
        ["Type", "Question", "Reply"],
        ["Storage", None, "Security", None],
        merged=("A2:A3", "A4:A5"),
    )
    result = workbook_reader.default_mapping(parsed)
    assert result.get("group") == 0
    assert "type" not in result


def test_topic_blocks_can_include_singleton_groups():
    _, parsed = workbook(
        ["Type", "Question", "Reply"],
        [
            "Collection storage",
            "Visitor facilities",
            "Visitor facilities",
            "Visitor facilities",
            "Safety systems",
        ],
    )
    assert workbook_reader.default_mapping(parsed).get("group") == 0


@pytest.mark.parametrize(
    "bold,section_before,group", [(True, None, 0), (False, None, None), (True, 2, None)]
)
def test_sparse_theme_blocks_need_bold_anchors_and_do_not_cross_sections(
    bold, section_before, group
):
    _, parsed = workbook(
        ["Type", "Question", "Reply"],
        ["Collection storage", None, "Visitor facilities", None, "Safety systems"],
        bold=(("A2", "A5") if section_before else ("A2", "A4")) if bold else (),
        section_before=section_before,
    )
    assert workbook_reader.default_mapping(parsed).get("group") == group


@pytest.mark.parametrize(
    "heading,labels,validation",
    [
        ("Type", ["Clarify", "Clarify", "Confirm", "Confirm"], None),
        ("Type", ["Class one", "Class one", "Class two", "Class two"], None),
        ("Category", ["Wooden objects", "Stone objects", "Metal objects"], None),
        (
            "Category",
            [
                "Collection storage",
                "Visitor facilities",
                "Collection storage",
                "Visitor facilities",
            ],
            None,
        ),
        (
            "Question Type",
            [
                "Collection storage",
                "Collection storage",
                "Visitor facilities",
                "Visitor facilities",
            ],
            None,
        ),
        (
            "Type",
            [
                "Collection storage",
                "Collection storage",
                "Visitor facilities",
                "Visitor facilities",
            ],
            ["Collection storage", "Visitor facilities"],
        ),
    ],
)
def test_weak_or_action_oriented_columns_stay_additional_fields(heading, labels, validation):
    _, parsed = workbook([heading, "Question", "Reply"], labels, validation=validation)
    result = workbook_reader.default_mapping(parsed)
    assert result.get("group") is None
    assert result["extras"] == [0]


def test_section_value_is_inherited_as_an_additional_field():
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Index", "Type", "Question", "Reply"])
    sheet.append([1, "Exhibition", None, None])
    sheet["B2"].font = Font(bold=True)
    sheet.append(["1.1", None, "Describe the gallery audio equipment.", None])
    out = BytesIO()
    book.save(out)
    parsed = workbook_reader.read_workbook(out.getvalue())
    mapping = workbook_reader.default_mapping(parsed)
    table, rows = workbook_reader.classify_rows(parsed, mapping)
    assert mapping.get("group") is None and mapping["extras"] == [1]
    assert table[0]["kind"] == "section"
    assert rows[0]["effective_values"][1] == "Exhibition"


def suggest(monkeypatch, parsed, **roles):
    def generate(_system, _packet, schema):
        return schema.model_validate(
            {"header_row": parsed["header_row"], "question": 1, "replies": [2], **roles}
        )

    monkeypatch.setattr(intelligence, "generate_json", generate)


def test_ai_cannot_override_explicit_group_header(monkeypatch):
    raw, parsed = workbook(["Topic", "Question", "Reply", "Question Type"], ["Storage", "Security"])
    suggest(monkeypatch, parsed, type=0)
    _, result, status = intelligence.map_headers(raw, parsed)
    assert status == "ready"
    assert result.get("group") == 0 and result["extras"] == [3]


def test_ai_preserves_action_labels_as_an_additional_field_beside_group(monkeypatch):
    raw, parsed = workbook(["Topic", "Question", "Reply", "Question Type"], ["Storage", "Security"])
    suggest(monkeypatch, parsed, group=0, type=3)
    _, result, status = intelligence.map_headers(raw, parsed)
    assert status == "ready"
    assert result.get("group") == 0 and result["extras"] == [3]


def test_ai_cannot_turn_an_explicit_reply_header_into_group(monkeypatch):
    raw, parsed = workbook(["Area", "Question", "Reply"], ["Storage", "Security"])
    suggest(monkeypatch, parsed, group=2, replies=[])
    _, result, status = intelligence.map_headers(raw, parsed)
    assert status == "manual"
    assert result.get("group") is None and result["replies"] == [2]


def test_ai_cannot_override_merged_topic_structure(monkeypatch):
    raw, parsed = workbook(
        ["Type", "Question", "Reply"],
        ["Storage", None, "Security", None],
        merged=("A2:A3", "A4:A5"),
    )
    suggest(monkeypatch, parsed, type=0)
    _, result, status = intelligence.map_headers(raw, parsed)
    assert status == "ready"
    assert result.get("group") == 0 and "type" not in result


@pytest.mark.parametrize(
    "labels", [["Clarify", "Clarify", "Confirm", "Confirm"], ["Cabinet wood", "Gallery glass"]]
)
def test_ai_group_suggestion_does_not_override_an_action_or_uncertain_type(monkeypatch, labels):
    raw, parsed = workbook(["Type", "Question", "Reply"], labels)
    suggest(monkeypatch, parsed, group=0)
    _, result, status = intelligence.map_headers(raw, parsed)
    assert status == "ready"
    assert result.get("group") is None and result["extras"] == [0]


def test_ai_can_suggest_a_group_when_an_unfamiliar_header_has_structure(monkeypatch):
    raw, parsed = workbook(
        ["Area", "Question", "Reply"],
        ["Storage", None, "Security", None],
        merged=("A2:A3", "A4:A5"),
    )
    suggest(monkeypatch, parsed, group=0)
    _, result, status = intelligence.map_headers(raw, parsed)
    assert status == "ready"
    assert result.get("group") == 0 and "type" not in result


@pytest.mark.parametrize("index", [1, 8])
def test_ai_group_suggestion_cannot_share_question_index_or_escape_region(monkeypatch, index):
    raw, parsed = workbook(["Topic", "Question", "Reply"], ["Storage", "Security"])
    suggest(monkeypatch, parsed, group=index)
    _, result, status = intelligence.map_headers(raw, parsed)
    assert status == "manual"
    assert result.get("group") == 0 and result["question"] == 1
