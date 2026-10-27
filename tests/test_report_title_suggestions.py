"""Title and body decisions use independently fictional report content."""
import pytest

from src.reports.store import ReportStore


def create_report(tmp_path):
    store = ReportStore(tmp_path / "reports", session_exists=lambda sid: sid == "sample-session")
    report = store.save("sample-session", "Old title", "Old body.\n\nUnchanged passage.", [])
    return store, report


def body_edit(report):
    block = report["blocks"][0]
    return {"id": "body-edit", "block_id": block["id"], "block_hash": block["hash"],
            "before": "Old body.", "after": "New body."}


@pytest.mark.parametrize("order", [("title", "markdown"), ("markdown", "title")])
def test_explicit_title_and_body_edits_can_be_rejected_independently_after_reopen(tmp_path, order):
    store, original = create_report(tmp_path)
    updated = store.save_edits("sample-session", original["id"], [body_edit(original)], [],
                               expected_revision=1, title="New title", operation_id="sample-update")
    assert {e.get("field", "markdown") for e in updated["edit_log"]} == {"title", "markdown"}
    assert [b["id"] for b in updated["blocks"]] == [b["id"] for b in original["blocks"]]
    store = ReportStore(store.root, session_exists=lambda sid: True)
    current = store.read("sample-session", original["id"])
    for index, field in enumerate(order):
        suggestion = next(g for g in current["suggestions"] if g["decision"] == "pending"
                          and any(p["field"] == field for p in g["patches"]))
        current = store.decide_suggestion("sample-session", original["id"], suggestion["id"], "rejected",
                                          expected_revision=current["revision"], operation_id=f"reject-{field}")
        assert current[field] == original[field]
        if index == 0:
            other = order[1]
            assert current[other] == updated[other], "Rejecting one field must retain the other pending edit"
    assert current["title"] == original["title"]
    assert current["markdown"] == original["markdown"]
    assert store.read("sample-session", original["id"], 1) == original


@pytest.mark.parametrize("title", ["New title", "  虚构样例 📄  "])
def test_title_can_be_accepted_while_the_body_change_is_rejected(tmp_path, title):
    store, original = create_report(tmp_path)
    updated = store.save_edits("sample-session", original["id"], [body_edit(original)], [],
                               expected_revision=1, title=title)
    title_group = next(g for g in updated["suggestions"] if any(p["field"] == "title" for p in g["patches"]))
    accepted = store.decide_suggestion("sample-session", original["id"], title_group["id"], "accepted",
                                      expected_revision=2, operation_id="accept-title")
    body_group = next(g for g in accepted["suggestions"] if any(p["field"] == "markdown" for p in g["patches"]))
    rejected = store.decide_suggestion("sample-session", original["id"], body_group["id"], "rejected",
                                      expected_revision=3, operation_id="reject-body")
    assert rejected["title"] == title.strip()
    assert rejected["markdown"] == original["markdown"]


def test_existing_literal_title_edits_are_not_duplicated_by_an_explicit_title(tmp_path):
    store, original = create_report(tmp_path)
    updated = store.save_edits("sample-session", original["id"],
                               [{"id": "rename", "mode": "replace_all", "before": "Old", "after": "New"}], [],
                               expected_revision=1, title="New title")
    assert len([e for e in updated["edit_log"] if e.get("field") == "title"]) == 1
    groups = updated["suggestions"]
    assert len(groups) == 2, "Title and body are independently reviewable locations"
    assert len({g["instruction_id"] for g in groups}) == 1
    assert {p["field"] for g in groups for p in g["patches"]} == {"title", "markdown"}
    rejected = store.decide_suggestion("sample-session", original["id"], updated["suggestions"][0]["id"], "rejected",
                                      expected_revision=2, operation_id="reject-rename", scope="instruction")
    assert rejected["title"] == original["title"]
    assert rejected["markdown"] == original["markdown"]


def test_an_unchanged_explicit_title_does_not_create_an_extra_suggestion(tmp_path):
    store, original = create_report(tmp_path)
    updated = store.save_edits("sample-session", original["id"], [body_edit(original)], [],
                               expected_revision=1, title=original["title"])
    assert len(updated["suggestions"]) == 1
    assert all(p["field"] == "markdown" for p in updated["suggestions"][0]["patches"])


def test_title_only_update_is_reviewable_without_touching_body_block_ids(tmp_path):
    store, original = create_report(tmp_path)
    updated = store.save_edits("sample-session", original["id"], [], [], expected_revision=1, title="New title")
    assert updated["blocks"] == original["blocks"]
    assert len(updated["suggestions"]) == 1
    group = updated["suggestions"][0]
    assert {p["field"] for p in group["patches"]} == {"title"}
    rejected = store.decide_suggestion("sample-session", original["id"], group["id"], "rejected",
                                      expected_revision=2, operation_id="reject-title")
    assert rejected["title"] == original["title"]
    assert rejected["markdown"] == original["markdown"]
