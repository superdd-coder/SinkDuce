"""Deterministic three-way merges use independent fictional cabinet records."""

import copy
import json

import pytest
from fastapi import HTTPException

from tests.test_qa import create, mutate, qa_fixture  # noqa: F401

QUESTION = "Describe the cabinet inspection schedule."


def question(qa, body="Inspect each morning."):
    data = mutate(qa, create(qa), "/questions", {"text": QUESTION})
    qid, cid = data["questions"][0]["id"], data["columns"][0]["id"]
    data = mutate(qa, data, f"/questions/{qid}/replies/{cid}", {"body": body}, "put")
    return data, qid, cid


def incoming(data, qid, cid, body="Inspect each morning.", text=QUESTION):
    return {
        "id": "incoming_import",
        "mapping": {"question": 0, "replies": [1], "extras": []},
        "headers": ["Question", "A response"],
        "matches": {"2": qid},
        "column_matches": {"1": cid},
        "rows": [
            {
                "key": "2",
                "text": text,
                "values": [text, body],
                "typed": [{"value": text, "kind": "s"}, {"value": body, "kind": "s"}],
            }
        ],
    }


def record(
    data, qid, cid, body="Inspect each morning.", text=QUESTION, *, external=None, extra=None
):
    from src.qa import service, store

    with store.connection("col_qa", "update") as conn, conn:
        row = {
            "key": "2",
            "text": text,
            "values": [text, body],
            "typed": [{"value": text, "kind": "s"}, {"value": body, "kind": "s"}],
            "committed_question_id": qid,
            "committed_state": dict(service._question(conn, data["version"]["id"], qid)),
            "owned_columns": [cid],
            "committed_cells": dict(
                conn.execute(
                    "SELECT column_id,text_revision_id FROM qa_version_cells WHERE version_id=? AND question_id=?",
                    (data["version"]["id"], qid),
                )
            ),
        }
        if external is not None:
            row["external_baseline"] = external
        plan = {
            "id": store.new_id(),
            "mapping": {"question": 0, "replies": [1], "extras": []},
            "rows": [row],
            "headers": ["Question", "A response"],
        }
        if extra:
            extra(plan, row, conn)
        conn.execute(
            "INSERT INTO qa_imports VALUES(?,?,?,?,?,?)",
            (
                plan["id"],
                data["id"],
                data["revision"],
                json.dumps(plan),
                data["version"]["id"],
                store.now(),
            ),
        )
    return plan


def evaluate(data, plan):
    from src.qa import import_merge, store

    with store.connection("col_qa") as conn:
        return import_merge.evaluate(conn, data["id"], plan)


@pytest.mark.parametrize("source_present", [True, False])
def test_matched_local_removal_stays_removed_but_file_omission_can_return(qa, source_present):
    from src.qa import metadata, store

    data, qid, cid = question(qa)
    record(data, qid, cid)
    with store.connection("col_qa", "update") as conn, conn:
        meta = metadata.read(conn, data["version"]["id"])
        meta["layout"] = {"rows": {qid: [2]} if source_present else {}}
        metadata.save(conn, data["version"]["id"], meta)
    data = mutate(qa, data, f"/questions/{qid}/membership", {"included": False})
    plan = incoming(data, qid, cid)
    result = evaluate(data, plan)
    assert result["rows"]["2"]["included"] is not source_present
    assert (qid in result["kept_question_ids"]) is not source_present
    assert result["review"]["counts"]["kept_replies"] == int(not source_present)
    assert not result["review"]["unresolved"]
    memberships = [item for item in result["review"]["conflicts"] if item["kind"] == "membership"]
    assert bool(memberships) is source_present
    if source_present:
        assert memberships[0]["choice"] == "keep"
        plan["content_choices"] = {memberships[0]["id"]: "incoming"}
        restored = evaluate(data, plan)
        assert restored["rows"]["2"]["included"] is True
        assert qid in restored["kept_question_ids"]
        assert restored["review"]["counts"]["kept_replies"] == 1


@pytest.mark.parametrize(
    "local,remote,expected",
    [
        ("", "  \t", (0, 0, 0)),
        ("", "Inspect each morning.", (0, 0, 1)),
        ("Inspect each morning.", "", (0, 0, 0)),
        ("Inspect each morning.", "Inspect at closing.", (0, 1, 0)),
    ],
)
def test_reply_review_counts_content_instead_of_cell_slots(qa, local, remote, expected):
    data, qid, cid = question(qa, local)
    record(data, qid, cid, local)
    counts = evaluate(data, incoming(data, qid, cid, remote))["review"]["counts"]
    assert (counts["kept_replies"], counts["updated_replies"], counts["new_replies"]) == expected


@pytest.mark.parametrize(
    "local,remote,expected,conflict",
    [
        ("Inspect each morning.", "Inspect each evening.", "Inspect each evening.", False),
        ("Inspect each afternoon.", "Inspect each morning.", "Inspect each afternoon.", False),
        ("Inspect each afternoon.", "Inspect each afternoon.", "Inspect each afternoon.", False),
        ("Inspect each afternoon.", "Inspect each evening.", "Inspect each afternoon.", True),
        ("", "Inspect each morning.", "", False),
    ],
)
def test_reply_three_way_matrix(qa, local, remote, expected, conflict):
    data, qid, cid = question(qa)
    record(data, qid, cid)
    data = mutate(qa, data, f"/questions/{qid}/replies/{cid}", {"body": local}, "put")
    result = evaluate(data, incoming(data, qid, cid, remote))
    assert result["rows"]["2"]["replies"]["1"] == expected
    assert result["review"]["unresolved"] == int(conflict)
    if conflict:
        item = result["review"]["conflicts"][0]
        assert (item["kind"], item["current"], item["incoming"], item["baseline"]) == (
            "reply",
            local,
            remote,
            "Inspect each morning.",
        )


def test_explicitly_carried_reply_preserves_the_earlier_external_baseline(qa):
    data, qid, cid = question(qa)
    record(data, qid, cid)
    data = mutate(
        qa, data, f"/questions/{qid}/replies/{cid}", {"body": "Inspect each afternoon."}, "put"
    )
    plan = incoming(data, qid, cid, "")
    plan["retained_replies"] = {cid: [qid]}
    untouched = copy.deepcopy(plan)
    result = evaluate(data, plan)
    assert result["rows"]["2"]["replies"]["1"] == "Inspect each afternoon."
    assert result["rows"]["2"]["external_baseline"]["replies"]["1"] == "Inspect each morning."
    assert result["review"]["counts"]["blank_kept"] == 1
    assert result["review"]["unresolved"] == 0
    assert plan == untouched, "the raw workbook remains authoritative and unmodified"
    plan["blank_columns"] = ["1"]
    cleared = evaluate(data, plan)
    assert cleared["rows"]["2"]["replies"]["1"] == ""
    assert cleared["review"]["counts"]["cleared_replies"] == 1
    assert cleared["review"]["counts"]["updated_replies"] == 0


def test_keep_conflict_advances_remote_baseline_without_overwriting_local_content(qa):
    data, qid, cid = question(qa)
    record(data, qid, cid)
    data = mutate(
        qa, data, f"/questions/{qid}/replies/{cid}", {"body": "Inspect each afternoon."}, "put"
    )
    plan = incoming(data, qid, cid, "Inspect each evening.")
    first = evaluate(data, plan)
    conflict_id = first["review"]["conflicts"][0]["id"]
    plan["content_choices"] = {conflict_id: "keep"}
    chosen = evaluate(data, plan)
    assert chosen["review"]["unresolved"] == 0
    baseline = chosen["rows"]["2"]["external_baseline"]
    assert baseline["question_id"] == qid
    assert baseline["replies"]["1"] == "Inspect each evening."
    data = mutate(qa, data, "/versions", {})
    record(data, qid, cid, "Inspect each evening.", external=baseline)
    again = evaluate(data, incoming(data, qid, cid, "Inspect each evening."))
    assert again["rows"]["2"]["replies"]["1"] == "Inspect each afternoon."
    assert again["review"]["unresolved"] == 0


def test_conflict_choices_are_bound_to_content_and_explicit_incoming_is_respected(qa):
    data, qid, cid = question(qa)
    plan = incoming(data, qid, cid, "Inspect each evening.")
    first = evaluate(data, plan)
    conflict_id = first["review"]["conflicts"][0]["id"]
    plan["content_choices"] = {conflict_id: "incoming"}
    assert evaluate(data, plan)["rows"]["2"]["replies"]["1"] == "Inspect each evening."
    plan["rows"][0]["values"][1] = "Inspect at closing."
    with pytest.raises(HTTPException) as error:
        evaluate(data, plan)
    assert error.value.detail["code"] == "invalid_mapping"


def test_question_text_and_rich_reply_format_use_three_way_comparison(qa):
    from src.qa.reply_format import reply_runs

    data, qid, cid = question(qa, "Use ***both*** locks.")
    record(data, qid, cid, "Use **_both_** locks.")
    data = mutate(
        qa, data, f"/questions/{qid}", {"text": "Describe the cabinet lock checks."}, "patch"
    )
    result = evaluate(data, incoming(data, qid, cid, "Use ***both*** locks."))
    assert result["rows"]["2"]["text"] == "Describe the cabinet lock checks."
    assert reply_runs(result["rows"]["2"]["replies"]["1"]) == reply_runs("Use ***both*** locks.")
    assert result["review"]["unresolved"] == 0


def test_missing_questions_report_drafts_but_require_explicit_retention(qa):
    from src.qa import store

    data, qid, cid = question(qa)
    record(data, qid, cid)
    plan = incoming(data, "new", "new", "A different reply.", "Describe the gallery entrance.")
    ordinary = evaluate(data, plan)
    assert ordinary["review"]["missing_questions"] == [
        {"id": qid, "text": QUESTION, "kept": False, "reason": "not_in_file"}
    ]
    with store.connection("col_qa", "update") as conn, conn:
        rid = conn.execute(
            "SELECT text_revision_id FROM qa_version_cells WHERE version_id=? AND question_id=? AND column_id=?",
            (data["version"]["id"], qid, cid),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO qa_reply_drafts VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                data["version"]["id"],
                qid,
                cid,
                "other_actor",
                "Check the latch.",
                1,
                rid,
                "[]",
                "",
                store.now(),
            ),
        )
    omitted = evaluate(data, plan)
    assert omitted["review"]["missing_questions"][0]["reason"] == "draft"
    assert qid not in omitted["kept_question_ids"]
    plan["retained_questions"] = [qid]
    assert qid in evaluate(data, plan)["kept_question_ids"]
    plan.pop("retained_questions")
    plan["removed_questions"] = [qid]
    assert qid not in evaluate(data, plan)["kept_question_ids"]
    plan["retained_questions"] = [qid]
    with pytest.raises(HTTPException):
        evaluate(data, plan)


def test_missing_locally_created_and_edited_questions_are_omitted_by_default(qa):
    data, qid, cid = question(qa)
    plan = incoming(data, "new", "new", "A different reply.", "Describe the gallery entrance.")
    assert evaluate(data, plan)["review"]["missing_questions"][0]["reason"] == "local_changes"
    assert qid not in evaluate(data, plan)["kept_question_ids"]
    record(data, qid, cid)
    data = mutate(
        qa, data, f"/questions/{qid}/replies/{cid}", {"body": "An updated local note."}, "put"
    )
    assert qid not in evaluate(data, plan)["kept_question_ids"]
    plan["retained_questions"] = [qid]
    assert qid in evaluate(data, plan)["kept_question_ids"]


def test_missing_columns_are_only_included_when_a_reply_is_carried(qa):
    data = create(qa)
    plan = incoming(data, "new", "new", "A reply.")
    assert evaluate(data, plan)["kept_column_ids"] == []
    data, qid, cid = question(qa)
    extra = data["columns"][1]["id"]
    plan = incoming(data, qid, cid)
    assert evaluate(data, plan)["kept_column_ids"] == [cid]
    data = mutate(qa, data, f"/questions/{qid}/replies/{extra}", {"body": "A separate room note."}, "put")
    plan["retained_replies"] = {extra: [qid]}
    assert set(evaluate(data, plan)["kept_column_ids"]) == {cid, extra}
    data = mutate(qa, data, f"/columns/{extra}", {"included": False}, "patch")
    assert evaluate(data, plan)["kept_column_ids"] == [cid]


def test_revoked_or_reassigned_import_rows_do_not_supply_a_trusted_baseline(qa):
    from src.qa import store

    data, qid, cid = question(qa)
    prior = record(data, qid, cid)
    with store.connection("col_qa", "update") as conn, conn:
        conn.execute(
            "INSERT INTO qa_correction_previews VALUES(?,?,?,?)",
            ("fictional_correction", data["id"], "{}", store.now()),
        )
        conn.execute(
            "INSERT INTO qa_revoked_associations VALUES(?,?)",
            (prior["rows"][0]["committed_state"]["text_revision_id"], "fictional_correction"),
        )
    result = evaluate(data, incoming(data, qid, cid, "Inspect each evening."))
    assert result["review"]["conflicts"][0]["baseline"] is None


def test_inherited_cells_are_not_a_baseline_for_a_column_missing_from_the_import(qa):
    data, qid, cid = question(qa)
    other = data["columns"][1]["id"]
    data = mutate(
        qa, data, f"/questions/{qid}/replies/{other}", {"body": "A local follow-up."}, "put"
    )
    record(data, qid, cid)
    result = evaluate(data, incoming(data, qid, other, "A remote follow-up."))
    assert result["review"]["conflicts"][0]["baseline"] is None


def test_pending_identities_skip_content_merging_and_bad_override_keys_are_rejected(qa):
    data, qid, cid = question(qa)
    plan = incoming(data, qid, cid)
    plan["matches"]["2"] = None
    assert evaluate(data, plan)["rows"] == {}
    plan["blank_columns"] = ["foreign_column"]
    with pytest.raises(HTTPException):
        evaluate(data, plan)


def test_status_and_typed_extra_values_preserve_local_changes(qa):
    from src.qa import metadata

    data, qid, cid = question(qa)

    def extra(plan, row, conn):
        plan["mapping"].update(status=2, extras=[3, 4])
        plan["headers"] += ["Status", "Type", "Drawer"]
        row["values"] += ["Waiting for reply", "Other", "Drawer A"]
        row["typed"] += [{"value": value, "kind": "s"} for value in row["values"][2:]]
        row["committed_fields"] = {"4": "drawer_field"}
        meta = metadata.read(conn, data["version"]["id"])
        meta["fields"] = [{"id": "drawer_field", "label": "Drawer", "included": True}]
        meta["values"][qid] = {"drawer_field": {"value": "Drawer B", "kind": "s"}}
        metadata.save(conn, data["version"]["id"], meta)

    record(data, qid, cid, extra=extra)
    data = mutate(qa, data, f"/questions/{qid}", {"status": "closed"}, "patch")
    plan = incoming(data, qid, cid)
    plan["headers"] += ["Status", "Type", "Drawer"]
    plan["mapping"].update(status=2, extras=[3, 4])
    plan["rows"][0]["values"] += ["Waiting for reply", "Other", "Drawer A"]
    plan["rows"][0]["typed"] += [
        {"value": value, "kind": "s"} for value in plan["rows"][0]["values"][2:]
    ]
    merged = evaluate(data, plan)["rows"]["2"]
    assert merged["effective_values"][2:] == ["Closed", "Other", "Drawer B"]
    assert merged["effective_typed"][4] == {"value": "Drawer B", "kind": "s"}


def test_empty_metadata_preserves_local_and_false_is_an_actual_typed_value(qa):
    from src.qa import metadata, store

    data, qid, cid = question(qa)
    with store.connection("col_qa", "update") as conn, conn:
        meta = metadata.read(conn, data["version"]["id"])
        meta["fields"] = [{"id": "switch_field", "label": "Alarm enabled", "included": True}]
        meta["values"][qid] = {"switch_field": {"value": True, "kind": "b"}}
        metadata.save(conn, data["version"]["id"], meta)
    plan = incoming(data, qid, cid)
    plan["mapping"]["extras"] = [2]
    plan["headers"].append("Alarm enabled")
    plan["rows"][0]["values"].append("")
    plan["rows"][0]["typed"].append({"value": None, "kind": "n"})
    kept = evaluate(data, plan)
    assert kept["review"]["unresolved"] == 0
    assert kept["rows"]["2"]["effective_typed"][2]["value"] is True
    plan["rows"][0]["values"][2] = "False"
    plan["rows"][0]["typed"][2] = {"value": False, "kind": "b"}
    changed = evaluate(data, plan)
    assert changed["review"]["unresolved"] == 1
    assert changed["review"]["conflicts"][0]["incoming"] == "False"


def test_read_decoration_ignores_stale_choices_but_authoritative_merge_rejects_them(qa):
    from src.qa import import_merge, store

    data, qid, cid = question(qa)
    plan = incoming(data, qid, cid, "A changed external answer.")
    plan["content_choices"] = {"stale_conflict": "keep"}
    with store.connection("col_qa") as conn:
        assert (
            import_merge.evaluate(conn, data["id"], plan, strict=False)["review"]["unresolved"] == 1
        )
        with pytest.raises(HTTPException):
            import_merge.evaluate(conn, data["id"], plan)


@pytest.mark.parametrize("local_change,remote_change", [(True, False), (False, True), (True, True)])
def test_structure_changes_do_not_block_import(qa, local_change, remote_change):
    from src.qa import metadata, store

    data, qid, cid = question(qa)
    data = mutate(qa, data, "/questions", {"text": "Describe the cabinet lock."})
    second = data["questions"][1]["id"]
    record(data, qid, cid)
    with store.connection("col_qa", "update") as conn, conn:
        meta = metadata.read(conn, data["version"]["id"])
        meta["layout"] = {"outline": copy.deepcopy(meta["outline"])}
        if local_change:
            meta["outline"].insert(
                0,
                {
                    "id": "local_note",
                    "kind": "note",
                    "text": "Use the cabinet guide.",
                    "group_id": "",
                    "parent_id": "",
                },
            )
        metadata.save(conn, data["version"]["id"], meta)
    plan = incoming(data, qid, cid)
    plan["region"] = {"start_column": 1}
    plan["rows"].append(
        {
            "key": "3",
            "text": "Describe the cabinet lock.",
            "values": ["Describe the cabinet lock.", ""],
        }
    )
    plan["matches"]["3"] = second
    if remote_change:
        plan["rows"].reverse()
    result = evaluate(data, plan)
    assert result["review"]["conflicts"] == []


def test_missing_question_protection_distinguishes_imported_outline_positions_from_local_group_moves(
    qa,
):
    from src.qa import metadata, store

    data, qid, cid = question(qa)
    record(data, qid, cid)
    with store.connection("col_qa", "update") as conn, conn:
        meta = metadata.read(conn, data["version"]["id"])
        meta["outline"] = [
            {"id": "group_a", "kind": "group", "text": "Cabinet checks"},
            {"id": qid, "kind": "question", "group_id": "group_a", "parent_id": ""},
        ]
        meta["layout"] = {"outline": copy.deepcopy(meta["outline"])}
        metadata.save(conn, data["version"]["id"], meta)
        conn.execute(
            "UPDATE qa_version_questions SET position=1 WHERE version_id=? AND question_id=?",
            (data["version"]["id"], qid),
        )
    plan = incoming(data, "new", "new", "A different reply.", "Describe the gallery entrance.")
    assert evaluate(data, plan)["review"]["missing_questions"][0]["kept"] is False
    with store.connection("col_qa", "update") as conn, conn:
        meta = metadata.read(conn, data["version"]["id"])
        meta["outline"].insert(1, {"id": "group_b", "kind": "group", "text": "Opening checks"})
        meta["outline"][-1]["group_id"] = "group_b"
        metadata.save(conn, data["version"]["id"], meta)
    assert evaluate(data, plan)["review"]["missing_questions"][0]["reason"] == "local_changes"


def test_missing_question_with_a_local_custom_field_change_reports_omission(qa):
    from src.qa import metadata

    data, qid, cid = question(qa)

    def extra(plan, row, conn):
        plan["mapping"]["extras"] = [2]
        plan["headers"].append("Drawer")
        row["values"].append("Drawer A")
        row["typed"].append({"value": "Drawer A", "kind": "s"})
        row["committed_fields"] = {"2": "drawer_field"}
        meta = metadata.read(conn, data["version"]["id"])
        meta["fields"] = [{"id": "drawer_field", "label": "Drawer", "included": True}]
        meta["values"][qid] = {"drawer_field": {"value": "Drawer B", "kind": "s"}}
        metadata.save(conn, data["version"]["id"], meta)

    record(data, qid, cid, extra=extra)
    plan = incoming(data, "new", "new", "A different reply.", "Describe the gallery entrance.")
    assert evaluate(data, plan)["review"]["missing_questions"][0]["reason"] == "local_changes"
    assert qid not in evaluate(data, plan)["kept_question_ids"]


def test_content_choice_is_bound_to_the_selected_column_identity(qa):
    data, qid, cid = question(qa)
    other = data["columns"][1]["id"]
    data = mutate(
        qa, data, f"/questions/{qid}/replies/{other}", {"body": "Inspect each morning."}, "put"
    )
    plan = incoming(data, qid, cid, "Inspect each evening.")
    conflict = evaluate(data, plan)["review"]["conflicts"][0]
    plan["content_choices"] = {conflict["id"]: "incoming"}
    plan["column_matches"]["1"] = other
    with pytest.raises(HTTPException):
        evaluate(data, plan)


def test_missing_question_with_a_reply_preserved_by_an_earlier_merge_is_omitted(qa):
    data, qid, cid = question(qa, "A locally refined inspection note.")
    record(
        data,
        qid,
        cid,
        "An unchanged external inspection note.",
        external={
            "version": 1,
            "question_id": qid,
            "question": QUESTION,
            "replies": {"1": "An unchanged external inspection note."},
            "fields": {},
        },
    )
    plan = incoming(data, "new", "new", "An unrelated reply.", "Describe the gallery entrance.")
    assert evaluate(data, plan)["review"]["missing_questions"][0]["reason"] == "local_changes"
    assert qid not in evaluate(data, plan)["kept_question_ids"]
