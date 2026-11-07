"""Explicit import retention keeps only the selected fictional garden questions."""

from tests.test_qa import qa_fixture  # noqa: F401
from tests.test_qa_excel import commit, upload
from tests.test_qa_import_merge_flow import patch
from tests.test_qa_outline import imported


def omit_family(qa, detail, retained):
    other = detail["questions"][2]
    plan = upload(
        qa,
        detail,
        [["Question", "Reply"], [other["text"], "Water on alternate days."]],
    )
    plan = patch(qa, detail, plan, retained_questions=[retained])
    return commit(qa, detail, plan)


def test_retained_child_detaches_from_omitted_parent_without_changing_history(qa):
    detail = imported(qa)
    parent, child, _ = detail["questions"]
    original_version = detail["version"]["id"]
    original_parent = parent["id"]
    assert child["parent_id"] == original_parent

    saved = omit_family(qa, detail, child["id"])
    questions = {question["id"]: question for question in saved["questions"]}
    assert questions[original_parent]["included"] is False
    assert questions[child["id"]]["included"] is True
    assert questions[child["id"]]["parent_id"] == ""

    client, base = qa
    history = client.get(f"{base}/lists/{detail['id']}?version_id={original_version}")
    assert history.status_code == 200, history.text
    previous = {question["id"]: question for question in history.json()["questions"]}
    assert previous[original_parent]["included"] is True
    assert previous[child["id"]]["included"] is True
    assert previous[child["id"]]["parent_id"] == original_parent


def test_retaining_parent_does_not_implicitly_retain_missing_child(qa):
    detail = imported(qa)
    parent, child, _ = detail["questions"]

    saved = omit_family(qa, detail, parent["id"])
    questions = {question["id"]: question for question in saved["questions"]}
    assert questions[parent["id"]]["included"] is True
    assert questions[child["id"]]["included"] is False
    assert questions[child["id"]]["absent_since"] == saved["version"]["number"]
