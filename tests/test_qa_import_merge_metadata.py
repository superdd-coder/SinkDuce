"""Preserved import metadata exports against fictional museum source cells."""

from io import BytesIO

import openpyxl

from tests.test_qa import create, mutate, qa_fixture  # noqa: F401
from tests.test_qa_excel import commit, upload
from tests.test_qa_flexible import exported, sample, upload_blob


def test_preserved_status_exports_local_label_against_raw_source(qa):
    detail = create(qa)
    detail = commit(qa, detail, upload_blob(qa, detail, sample()))
    question = next(item for item in detail["questions"] if item["included"])
    detail = mutate(qa, detail, f"/questions/{question['id']}", {"status": "closed"}, "patch")
    plan = upload_blob(qa, detail, sample())
    assert not plan["review"]["unresolved"]
    detail = commit(qa, detail, plan)
    assert (
        next(item for item in detail["questions"] if item["id"] == question["id"])["status"]
        == "closed"
    )
    workbook = openpyxl.load_workbook(BytesIO(exported(qa, detail)))
    assert workbook["Register"]["E6"].value == "Closed"


def test_raw_typed_source_baseline_is_separate_from_preserved_values(qa):
    from src.qa import metadata, store

    detail = create(qa)
    detail = commit(qa, detail, upload_blob(qa, detail, sample()))
    question = next(item for item in detail["questions"] if item["included"])
    with store.connection("col_qa", "update") as conn, conn:
        meta = metadata.read(conn, detail["version"]["id"])
        fid = next(item["id"] for item in meta["fields"] if item["label"] == "Capacity")
        meta["values"][question["id"]][fid] = {"value": 0, "kind": "n"}
        metadata.save(conn, detail["version"]["id"], meta)
    plan = upload_blob(qa, detail, sample())
    detail = commit(qa, detail, plan)
    with store.connection("col_qa") as conn:
        meta = metadata.read(conn, detail["version"]["id"])
    assert meta["values"][question["id"]][fid] == {"value": 0, "kind": "n"}
    assert meta["layout"]["typed_baseline"][question["id"]][7] == {"value": 37, "kind": "n"}


def test_duplicate_incoming_field_labels_reserve_existing_binding_only_once(qa):
    question = "Describe the cabinet access process."
    response = "Use the entrance supervised by the museum desk."
    detail = create(qa)
    detail = commit(
        qa,
        detail,
        upload(qa, detail, [["Question", "Reply", "Desk"], [question, response, "Archive desk"]]),
    )
    fid = detail["questions"][0]["fields"][0]["id"]
    plan = upload(
        qa,
        detail,
        [["Question", "Reply", "Desk", "Desk"], [question, response, "Archive desk", None]],
    )
    assert not plan["review"]["unresolved"]
    detail = commit(qa, detail, plan)
    fields = detail["questions"][0]["fields"]
    assert len(fields) == 2
    assert next(item["value"] for item in fields if item["id"] == fid) == "Archive desk"
    assert next(item["value"] for item in fields if item["id"] != fid) is None


def test_preserved_status_is_reported_when_next_file_omits_question(qa):
    rows = [
        ["Question", "Reply", "Status", "Type"],
        [
            "Describe the cabinet inspection routine.",
            "Inspect both cabinet locks each morning.",
            "Waiting for reply",
            "Clarify",
        ],
        [
            "Confirm the gallery opening schedule.",
            "The reception team maintains the schedule.",
            "Waiting for reply",
            "Clarify",
        ],
    ]
    detail = create(qa)
    detail = commit(qa, detail, upload(qa, detail, rows))
    qid = detail["questions"][0]["id"]
    detail = mutate(qa, detail, f"/questions/{qid}", {"status": "closed"}, "patch")
    detail = commit(qa, detail, upload(qa, detail, rows))
    plan = upload(qa, detail, [rows[0], rows[2]])
    missing = next(item for item in plan["review"]["missing_questions"] if item["id"] == qid)
    assert missing["kept"] is False
    assert missing["reason"] == "local_changes"
