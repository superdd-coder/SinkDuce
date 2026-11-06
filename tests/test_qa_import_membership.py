"""An imported revision follows its file's question membership, retaining history."""

from io import BytesIO

import openpyxl
import pytest

from tests.test_qa import mutate, qa_fixture  # noqa: F401
from tests.test_qa_excel import commit, upload
from tests.test_qa_import_merge_flow import first_question, initial, patch


def missing_question(detail):
    return next(item for item in detail["questions"] if item["id"] != first_question(detail)["id"])


def put_draft(qa, detail, question):
    client, base = qa
    cell = next(item for item in question["replies"] if item["label"] == "First response")
    url = f"{base}/lists/{detail['id']}/questions/{question['id']}/drafts/{cell['column_id']}"
    response = client.put(
        url,
        json={
            "version_id": detail["version"]["id"],
            "draft_revision": 0,
            "base_cell_revision_id": cell["text_revision_id"],
            "body": "Check the curator's revised opening schedule.",
        },
    )
    assert response.status_code == 200, response.text
    return url, response.json()


def partial_upload(qa, detail):
    return upload(
        qa,
        detail,
        [
            ["Question", "First response", "Inspection follow-up"],
            [
                first_question(detail)["text"],
                "Use the supervised entrance.",
                "The spare key is registered.",
            ],
        ],
    )


@pytest.mark.parametrize("change", ["untouched", "question", "reply", "status", "created", "draft"])
def test_file_omission_removes_question_from_new_revision_without_losing_prior_work(qa, change):
    detail = initial(qa)
    question = missing_question(detail)
    qid = question["id"]
    draft_url, original_draft = None, None
    if change == "question":
        detail = mutate(
            qa, detail, f"/questions/{qid}", {"text": "Confirm the revised gallery hours."}, "patch"
        )
    elif change == "reply":
        cell = next(item for item in question["replies"] if item["label"] == "First response")
        detail = mutate(
            qa,
            detail,
            f"/questions/{qid}/replies/{cell['column_id']}",
            {"body": "The gallery now opens at ten."},
            "put",
        )
    elif change == "status":
        detail = mutate(qa, detail, f"/questions/{qid}", {"status": "closed"}, "patch")
    elif change == "created":
        detail = mutate(qa, detail, "/questions", {"text": "Confirm the cabinet shelf dimensions."})
        qid = next(
            item["id"]
            for item in detail["questions"]
            if item["text"] == "Confirm the cabinet shelf dimensions."
        )
    elif change == "draft":
        draft_url, original_draft = put_draft(qa, detail, question)
    before = next(item for item in detail["questions"] if item["id"] == qid)
    old_vid = detail["version"]["id"]
    plan = partial_upload(qa, detail)
    decision = next(item for item in plan["review"]["missing_questions"] if item["id"] == qid)
    assert decision["kept"] is False
    saved = commit(qa, detail, plan)
    omitted = next(item for item in saved["questions"] if item["id"] == qid)
    assert omitted["included"] is False
    assert omitted["absent_since"] == saved["version"]["number"]
    assert [item["id"] for item in saved["questions"] if item["included"]] == [
        first_question(saved)["id"]
    ]
    client, base = qa
    history = client.get(f"{base}/lists/{detail['id']}?version_id={old_vid}").json()
    previous = next(item for item in history["questions"] if item["id"] == qid)
    assert previous["included"] is True
    assert (previous["text"], previous["status"], previous["replies"]) == (
        before["text"],
        before["status"],
        before["replies"],
    )
    if draft_url:
        old_draft = client.get(draft_url + f"?version_id={old_vid}")
        assert old_draft.status_code == 200
        assert old_draft.json() == original_draft


def test_explicitly_retaining_a_missing_question_carries_its_draft_into_the_new_revision(qa):
    detail = initial(qa)
    question = missing_question(detail)
    draft_url, original_draft = put_draft(qa, detail, question)
    plan = partial_upload(qa, detail)
    assert (
        next(item for item in plan["review"]["missing_questions"] if item["id"] == question["id"])[
            "kept"
        ]
        is False
    )
    plan = patch(qa, detail, plan, retained_questions=[question["id"]])
    assert (
        next(item for item in plan["review"]["missing_questions"] if item["id"] == question["id"])[
            "kept"
        ]
        is True
    )
    saved = commit(qa, detail, plan)
    assert (
        next(item for item in saved["questions"] if item["id"] == question["id"])["included"]
        is True
    )
    draft = qa[0].get(draft_url + f"?version_id={saved['version']['id']}").json()
    assert draft["body"] == original_draft["body"]
    assert draft["version_id"] == saved["version"]["id"]


def test_omitted_question_is_absent_from_export_and_can_return_with_its_original_identity(qa):
    detail = initial(qa)
    question = missing_question(detail)
    detail = mutate(qa, detail, f"/questions/{question['id']}", {"status": "closed"}, "patch")
    detail = commit(qa, detail, partial_upload(qa, detail))
    client, base = qa
    response = client.post(
        f"{base}/lists/{detail['id']}/exports", json={"expected_revision": detail["revision"]}
    )
    assert response.status_code == 200, response.text
    download = client.get(f"{base}/lists/{detail['id']}/exports/{response.json()['id']}/download")
    workbook = openpyxl.load_workbook(BytesIO(download.content))
    assert question["text"] not in [cell.value for row in workbook.active for cell in row]
    plan = upload(
        qa,
        detail,
        [
            ["Question", "First response", "Inspection follow-up"],
            [
                first_question(detail)["text"],
                "Use the supervised entrance.",
                "The spare key is registered.",
            ],
            [question["text"], "The gallery opens at nine.", "The desk confirms the schedule."],
        ],
    )
    assert question["id"] in plan["matches"].values()
    saved = commit(qa, detail, plan)
    restored = next(item for item in saved["questions"] if item["id"] == question["id"])
    assert restored["included"] is True
    assert restored["absent_since"] is None
    assert restored["status"] == "closed"
