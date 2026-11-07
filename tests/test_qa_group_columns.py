"""Subject columns create groups independently of the question type vocabulary."""

from io import BytesIO

import openpyxl

from tests.test_qa import create, mutate, qa_fixture  # noqa: F401
from tests.test_qa_excel import commit
from tests.test_qa_flexible import upload_blob
from tests.test_qa_import_merge_flow import patch


def group_workbook(*, merged=False, bold=False, question_types=False, single=False):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Type", "Question", "Reply"])
    sheet.append(["Gallery access", "Describe the entrance route.", "Use the east door."])
    if not single:
        sheet.append(["Gallery access", "Confirm the desk opening time.", None])
        sheet.append(["Archive storage", "Explain the cabinet checks.", None])
    if merged:
        sheet.merge_cells("A2:A3")
    if bold:
        sheet["A2"].font = openpyxl.styles.Font(bold=True)
        sheet["A3"] = None
    if question_types:
        sheet["D1"] = "Question Type"
        sheet["D2"] = "Confirm"
    raw = BytesIO()
    book.save(raw)
    return raw.getvalue()


def use_role(qa, detail, plan, role):
    return patch(
        qa,
        detail,
        plan,
        mapping={"question": 1, "replies": [2], role: 0},
    )


def test_automatic_group_mapping_reads_vertical_merges_and_creates_no_new_types(qa):
    detail = create(qa)
    plan = upload_blob(qa, detail, group_workbook(merged=True))
    assert plan["mapping"]["group"] == 0
    assert plan["mapping"]["type"] is None
    assert [row["group"] for row in plan["rows"]] == [
        "Gallery access",
        "Gallery access",
        "Archive storage",
    ]
    assert [row["group_key"] for row in plan["rows"]] == ["column:2", "column:2", "column:4"]
    assert not plan["table_rows"][1]["values"][0]
    saved = commit(qa, detail, plan)
    assert [item["text"] for item in saved["outline"] if item["kind"] == "group"] == [
        "Gallery access",
        "Archive storage",
    ]
    assert all(q["type_origin"] == "default" for q in saved["questions"])
    assert {o["label"] for o in saved["options"]["types"]} == {
        "Clarify",
        "Provide",
        "Confirm",
        "Revise",
        "Other",
    }


def test_reimport_as_groups_resets_unmodified_imported_types_and_keeps_explicit_types(qa):
    raw = group_workbook(bold=True)
    detail = create(qa)
    old = commit(qa, detail, use_role(qa, detail, upload_blob(qa, detail, raw), "type"))
    qid = old["questions"][0]["id"]
    old_type = old["questions"][1]["type"]
    old_vid = old["version"]["id"]
    old = mutate(qa, old, f"/questions/{qid}", {"type": "Confirm"}, "patch")
    plan = use_role(qa, old, upload_blob(qa, old, raw), "group")
    assert [row["group"] for row in plan["rows"]] == [
        "Gallery access",
        "Gallery access",
        "Archive storage",
    ]
    saved = commit(qa, old, plan)
    assert [(q["type"], q["type_origin"]) for q in saved["questions"]] == [
        ("Confirm", "manual"),
        ("Other", "default"),
        ("Other", "default"),
    ]
    assert {o["label"] for o in saved["options"]["types"]} == {
        "Clarify",
        "Provide",
        "Confirm",
        "Revise",
        "Other",
    }
    client, base = qa
    frozen = client.get(f"{base}/lists/{old['id']}?version_id={old_vid}").json()
    assert frozen["questions"][1]["type"] == old_type


def test_regrouping_allows_a_separate_type_column_with_blank_cells(qa):
    detail = create(qa)
    raw = group_workbook(question_types=True)
    plan = upload_blob(qa, detail, raw)
    plan = patch(
        qa, detail, plan, mapping={"question": 1, "replies": [2], "type": 0, "extras": [3]}
    )
    old = commit(qa, detail, plan)
    plan = upload_blob(qa, old, raw)
    plan = patch(qa, old, plan, mapping={"question": 1, "replies": [2], "group": 0, "type": 3})
    saved = commit(qa, old, plan)
    assert [(q["type"], q["type_origin"]) for q in saved["questions"]] == [
        ("Confirm", "manual"),
        ("", "default"),
        ("", "default"),
    ]
    assert all(
        o["label"] not in {"Gallery access", "Archive storage"} for o in saved["options"]["types"]
    )


def test_regrouping_preserves_a_same_label_type_chosen_before_the_original_import(qa):
    detail = create(qa)
    options = detail["options"]
    options["types"].append({"id": "gallery", "label": "Gallery access", "color": "#397b60"})
    detail = mutate(qa, detail, "/options", {"options": options}, "patch")
    detail = mutate(
        qa, detail, "/questions", {"text": "Describe the entrance route.", "type": "gallery"}
    )
    raw = group_workbook(single=True)
    plan = use_role(qa, detail, upload_blob(qa, detail, raw), "type")
    old = commit(qa, detail, patch(qa, detail, plan, column_matches={"2": "new"}))
    saved = commit(qa, old, use_role(qa, old, upload_blob(qa, old, raw), "group"))
    assert [(q["type"], q["type_origin"]) for q in saved["questions"]] == [
        ("gallery", "manual"),
    ]


def test_regrouping_with_no_default_option_leaves_type_unassigned(qa):
    detail = create(qa)
    options = detail["options"]
    options["types"] = []
    detail = mutate(qa, detail, "/options", {"options": options}, "patch")
    raw = group_workbook(single=True)
    old = commit(qa, detail, use_role(qa, detail, upload_blob(qa, detail, raw), "type"))
    saved = commit(qa, old, use_role(qa, old, upload_blob(qa, old, raw), "group"))
    assert (saved["questions"][0]["type"], saved["questions"][0]["type_origin"]) == ("", "default")
    assert saved["options"]["types"] == []
