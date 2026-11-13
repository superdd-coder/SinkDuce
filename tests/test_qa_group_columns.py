"""Group imports retain original workbook data without question categories."""

import json
from io import BytesIO

import openpyxl

from tests.test_qa import create, mutate, qa_fixture  # noqa: F401
from tests.test_qa_excel import commit, upload
from tests.test_qa_flexible import exported, upload_blob
from tests.test_qa_import_merge_flow import patch


def group_workbook(*, action_column=False):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Type", "Question", "Reply"])
    sheet.append(["Gallery access", "Describe the entrance route.", "Use the east door."])
    sheet.append(["Gallery access", "Confirm the desk opening time.", None])
    sheet.append(["Archive storage", "Explain the cabinet checks.", None])
    sheet.merge_cells("A2:A3")
    if action_column:
        sheet["D1"] = "Question Type"
        sheet["D2"] = "Confirm"
    raw = BytesIO()
    book.save(raw)
    return raw.getvalue()


def test_group_names_do_not_create_question_categories(qa):
    detail = create(qa)
    plan = upload(
        qa,
        detail,
        [
            ["Topic", "Question", "Reply", "Category"],
            [
                "Display cases",
                "How are the case shelves cleaned?",
                "Use a dry cloth.",
                "Display cases",
            ],
            [
                "Workshop tools",
                "Where is the spare vise kept?",
                "In the second drawer.",
                "Workshop tools",
            ],
        ],
    )
    assert plan["mapping"]["group"] == 0
    assert plan["mapping"].get("type") is None
    assert plan["mapping"]["extras"] == [3]
    saved = commit(qa, detail, plan)
    assert [q["group"] for q in saved["questions"]] == ["Display cases", "Workshop tools"]
    assert [(f["label"], f["value"]) for f in saved["questions"][0]["fields"]] == [
        ("Category", "Display cases"),
    ]
    assert "types" not in saved["options"]
    assert "type" not in saved["questions"][0]


def test_automatic_group_mapping_reads_vertical_merges_and_creates_no_new_types(qa):
    detail = create(qa)
    plan = upload_blob(qa, detail, group_workbook())
    assert plan["mapping"]["group"] == 0
    assert "type" not in plan["mapping"]
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
    assert "types" not in saved["options"]


def test_resuming_old_import_keeps_category_column_as_an_additional_field(qa):
    from src.qa import store

    detail = create(qa)
    plan = upload_blob(qa, detail, group_workbook(action_column=True))
    with store.connection("col_qa", "update") as conn, conn:
        stored = json.loads(
            conn.execute("SELECT plan_json FROM qa_imports WHERE id=?", (plan["id"],)).fetchone()[0]
        )
        stored["mapping"].update(type=3, extras=[])
        stored["review"]["conflicts"].append({"id": "old-type-edit", "kind": "type"})
        stored["content_choices"] = {"old-type-edit": "keep"}
        conn.execute(
            "UPDATE qa_imports SET plan_json=? WHERE id=?", (json.dumps(stored), plan["id"])
        )
    client, base = qa
    resumed = client.get(f"{base}/lists/{detail['id']}/imports/{plan['id']}")
    assert resumed.status_code == 200
    plan = resumed.json()
    assert "type" not in plan["mapping"] and plan["mapping"]["extras"] == [3]
    assert not plan["content_choices"]
    saved = commit(qa, detail, plan)
    assert saved["questions"][0]["fields"][0]["value"] == "Confirm"
    assert saved["questions"][1]["fields"][0]["value"] is None


def test_legacy_manual_mapping_retains_category_cell_values(qa):
    detail = create(qa)
    plan = upload_blob(qa, detail, group_workbook(action_column=True))
    plan = patch(qa, detail, plan, mapping={"question": 1, "replies": [2], "group": 0, "type": 3})
    assert "type" not in plan["mapping"] and plan["mapping"]["extras"] == [3]
    saved = commit(qa, detail, plan)
    assert saved["questions"][0]["group"] == "Gallery access"
    assert saved["questions"][0]["fields"][0]["value"] == "Confirm"


def test_original_layout_export_preserves_stored_legacy_category_values(qa):
    from src.qa import metadata, store

    detail = create(qa)
    detail = commit(qa, detail, upload_blob(qa, detail, group_workbook(action_column=True)))
    vid = detail["version"]["id"]
    qid = detail["questions"][0]["id"]
    with store.connection("col_qa", "update") as conn, conn:
        meta = metadata.read(conn, vid)
        meta["fields"] = []
        meta["layout"]["fields"] = {}
        meta["layout"]["mapping"].update(type=3, extras=[])
        meta["options"]["types"] = [
            {"id": "legacy", "label": "Check inventory", "color": "#397b60"}
        ]
        meta["layout"]["options"]["types"] = meta["options"]["types"]
        conn.execute(
            "UPDATE qa_version_questions SET type='legacy',type_origin='manual' WHERE version_id=? AND question_id=?",
            (vid, qid),
        )
        metadata.save(conn, vid, meta)
    detail = mutate(qa, detail, f"/questions/{qid}", {"status": "closed"}, "patch")
    assert "type" not in detail["questions"][0] and "types" not in detail["options"]
    sheet = openpyxl.load_workbook(BytesIO(exported(qa, detail))).active
    assert sheet["D1"].value == "Question Type"
    assert sheet["D2"].value == "Check inventory"
