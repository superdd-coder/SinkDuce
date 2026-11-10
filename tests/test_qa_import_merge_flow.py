"""End-to-end safe imports use independent museum records in temporary storage."""

from tests.test_qa import create, mutate, qa_fixture  # noqa: F401
from tests.test_qa_excel import commit, upload


def initial(qa):
    detail = create(qa)
    return commit(
        qa,
        detail,
        upload(
            qa,
            detail,
            [
                ["Question", "First response", "Inspection follow-up"],
                [
                    "Explain the cabinet access procedure.",
                    "Use the supervised entrance.",
                    "The spare key is registered.",
                ],
                [
                    "Confirm the museum opening hours.",
                    "The gallery opens at nine.",
                    "The desk confirms the schedule.",
                ],
            ],
        ),
    )


def patch(qa, detail, plan, **changes):
    client, base = qa
    response = client.patch(
        f"{base}/lists/{detail['id']}/imports/{plan['id']}",
        json={"plan_revision": plan["plan_revision"], **changes},
    )
    assert response.status_code == 200, response.text
    return response.json()


def first_question(detail):
    return next(
        question
        for question in detail["questions"]
        if question["text"] == "Explain the cabinet access procedure."
    )


def reply(question, column):
    return next(item["body"] for item in question["replies"] if item["column_id"] == column)


def test_first_import_counts_nonblank_replies_but_preserves_empty_columns(qa):
    client, base = qa
    response = client.post(base + "/lists", json={"title": "Museum checklist", "mode": "import"})
    assert response.status_code == 201, response.text
    detail = response.json()
    rows = [
        ["Question", "First response", "Inspection follow-up"],
        ["Explain the cabinet access procedure.", None, "  "],
        ["Confirm the museum opening hours.", "The gallery opens at nine.", None],
    ]
    plan = upload(qa, detail, rows)
    counts = plan["review"]["counts"]
    assert (counts["kept_replies"], counts["updated_replies"], counts["new_replies"]) == (0, 0, 1)
    saved = commit(qa, detail, plan)
    assert [column["label"] for column in saved["columns"]] == [
        "First response",
        "Inspection follow-up",
    ]


def test_blank_cells_and_absent_reply_columns_archive_saved_replies(qa):
    detail = initial(qa)
    question = first_question(detail)
    column = next(item["id"] for item in detail["columns"] if item["label"] == "First response")
    other = next(
        item["id"] for item in detail["columns"] if item["label"] == "Inspection follow-up"
    )
    plan = upload(qa, detail, [["Question", "First response"], [question["text"], ""]])
    assert plan["review"]["unresolved"] == 0
    saved = commit(qa, detail, plan)
    current = first_question(saved)
    assert reply(current, column) == ""
    assert not next(item for item in saved["columns"] if item["id"] == other)["included"]
    client, base = qa
    history = client.get(
        f"{base}/lists/{detail['id']}", params={"version_id": detail["version"]["id"]}
    ).json()
    assert reply(first_question(history), column) == "Use the supervised entrance."
    assert reply(first_question(history), other) == "The spare key is registered."


def test_carrying_selected_old_replies_does_not_carry_other_cells_in_the_column(qa):
    detail = initial(qa)
    first, second = detail["questions"]
    columns = {item["label"]: item["id"] for item in detail["columns"]}
    mapped, absent = columns["First response"], columns["Inspection follow-up"]
    plan = upload(
        qa, detail, [["Question", "First response"], [first["text"], ""], [second["text"], ""]]
    )
    plan = patch(qa, detail, plan, retained_replies={mapped: [first["id"]], absent: [second["id"]]})
    saved = commit(qa, detail, plan)
    first, second = saved["questions"]
    assert [reply(first, mapped), reply(first, absent)] == ["Use the supervised entrance.", ""]
    assert [reply(second, mapped), reply(second, absent)] == ["", "The desk confirms the schedule."]


def test_unchanged_external_text_does_not_erase_local_edits_on_repeated_imports(qa):
    detail = initial(qa)
    question = first_question(detail)
    column = next(item["id"] for item in detail["columns"] if item["label"] == "First response")
    detail = mutate(
        qa,
        detail,
        f"/questions/{question['id']}/replies/{column}",
        {"body": "Use the supervised entrance after booking."},
        "put",
    )
    rows = [["Question", "First response"], [question["text"], "Use the supervised entrance."]]
    for _ in range(2):
        plan = upload(qa, detail, rows)
        assert plan["review"]["unresolved"] == 0
        detail = commit(qa, detail, plan)
        assert reply(first_question(detail), column) == "Use the supervised entrance after booking."


def test_divergent_edits_need_one_choice_then_identical_external_input_stays_resolved(qa):
    detail = initial(qa)
    question = first_question(detail)
    column = next(item["id"] for item in detail["columns"] if item["label"] == "First response")
    detail = mutate(
        qa,
        detail,
        f"/questions/{question['id']}/replies/{column}",
        {"body": "Use the supervised entrance after booking."},
        "put",
    )
    rows = [
        ["Question", "First response"],
        [question["text"], "Access is through the reception desk."],
    ]
    plan = upload(qa, detail, rows)
    conflicts = plan["review"]["conflicts"]
    assert len(conflicts) == 1 and plan["review"]["unresolved"] == 1
    client, base = qa
    response = client.post(
        f"{base}/lists/{detail['id']}/imports/{plan['id']}/commit",
        json={"plan_revision": plan["plan_revision"]},
    )
    assert response.status_code == 409
    assert client.get(f"{base}/lists/{detail['id']}").json()["revision"] == detail["revision"]
    plan = patch(qa, detail, plan, content_choices={conflicts[0]["id"]: "keep"})
    assert plan["review"]["unresolved"] == 0
    detail = commit(qa, detail, plan)
    assert reply(first_question(detail), column) == "Use the supervised entrance after booking."
    again = upload(qa, detail, rows)
    assert again["review"]["unresolved"] == 0
    assert (
        reply(first_question(commit(qa, detail, again)), column)
        == "Use the supervised entrance after booking."
    )


def test_explicit_blank_overwrite_is_scoped_to_one_column_and_validated(qa):
    detail = initial(qa)
    question = first_question(detail)
    column = next(item["id"] for item in detail["columns"] if item["label"] == "First response")
    plan = upload(qa, detail, [["Question", "First response"], [question["text"], ""]])
    plan = patch(qa, detail, plan, blank_columns=["1"])
    if plan["review"]["unresolved"]:
        plan = patch(
            qa,
            detail,
            plan,
            content_choices={
                conflict["id"]: "incoming" for conflict in plan["review"]["conflicts"]
            },
        )
    saved = commit(qa, detail, plan)
    assert reply(first_question(saved), column) == ""
    assert not next(item for item in saved["columns"] if item["label"] == "Inspection follow-up")[
        "included"
    ]


def test_missing_questions_leave_the_version_including_local_additions(qa):
    detail = initial(qa)
    detail = mutate(qa, detail, "/questions", {"text": "Provide the display cabinet dimensions."})
    added = next(
        question
        for question in detail["questions"]
        if question["text"] == "Provide the display cabinet dimensions."
    )
    plan = upload(
        qa,
        detail,
        [
            ["Question", "First response"],
            [first_question(detail)["text"], "Use the supervised entrance."],
        ],
    )
    missing = {item["id"]: item for item in plan["review"]["missing_questions"]}
    assert missing[added["id"]]["kept"] is False
    old = next(
        question
        for question in detail["questions"]
        if question["text"] == "Confirm the museum opening hours."
    )
    assert missing[old["id"]]["kept"] is False
    saved = commit(qa, detail, plan)
    assert (
        next(question for question in saved["questions"] if question["id"] == added["id"])[
            "included"
        ]
        is False
    )
    assert (
        next(question for question in saved["questions"] if question["id"] == old["id"])["included"]
        is False
    )


def test_current_draft_text_carries_forward_without_applying_it_as_a_reply(qa):
    detail = initial(qa)
    question = first_question(detail)
    column = next(item["id"] for item in detail["columns"] if item["label"] == "First response")
    cell = next(item for item in question["replies"] if item["column_id"] == column)
    client, base = qa
    draft_url = f"{base}/lists/{detail['id']}/questions/{question['id']}/drafts/{column}"
    response = client.put(
        draft_url,
        json={
            "version_id": detail["version"]["id"],
            "draft_revision": 0,
            "base_cell_revision_id": cell["text_revision_id"],
            "body": "Polish the description of supervised access.",
        },
    )
    assert response.status_code == 200, response.text
    plan = upload(
        qa,
        detail,
        [["Question", "First response"], [question["text"], "Use the supervised entrance."]],
    )
    saved = commit(qa, detail, plan)
    draft = client.get(draft_url + f"?version_id={saved['version']['id']}").json()
    assert draft["body"] == "Polish the description of supervised access."
    assert reply(first_question(saved), column) == "Use the supervised entrance."


def test_stale_preview_can_recheck_the_saved_upload_without_erasing_later_edits(qa):
    detail = initial(qa)
    question = first_question(detail)
    column = next(item["id"] for item in detail["columns"] if item["label"] == "First response")
    plan = upload(
        qa,
        detail,
        [["Question", "First response"], [question["text"], "Use the supervised entrance."]],
    )
    detail = mutate(
        qa,
        detail,
        f"/questions/{question['id']}/replies/{column}",
        {"body": "Reserve access with the curator."},
        "put",
    )
    client, base = qa
    url = f"{base}/lists/{detail['id']}/imports/{plan['id']}"
    assert client.get(url).json()["stale"] is True
    result = client.post(url + "/commit", json={"plan_revision": plan["plan_revision"]})
    assert result.status_code == 409
    plan = patch(qa, detail, plan, rebase=True)
    assert plan["base_revision"] == detail["revision"] and plan["stale"] is False
    assert plan["review"]["unresolved"] == 0
    saved = commit(qa, detail, plan)
    assert reply(first_question(saved), column) == "Reserve access with the curator."


def test_reimporting_an_unchanged_source_note_keeps_its_local_edit(qa):
    from tests.test_qa_outline import imported, upload_blob, workbook

    detail = imported(qa)
    note = next(item for item in detail["outline"] if item["kind"] == "note")
    detail = mutate(
        qa,
        detail,
        f"/outline/{note['id']}",
        {"text": "Check the updated cabinet guide before responding."},
        "patch",
    )
    plan = upload_blob(qa, detail, workbook())
    assert not plan["review"]["unresolved"]
    saved = commit(qa, detail, plan)
    notes = [item for item in saved["outline"] if item["kind"] == "note"]
    assert len(notes) == 1
    assert notes[0]["id"] == note["id"]
    assert notes[0]["text"] == "Check the updated cabinet guide before responding."


def test_stale_content_choice_is_rejected_without_modifying_working_data(qa):
    detail = initial(qa)
    plan = upload(
        qa,
        detail,
        [
            ["Question", "First response"],
            [first_question(detail)["text"], "Use the supervised entrance."],
        ],
    )
    client, base = qa
    response = client.patch(
        f"{base}/lists/{detail['id']}/imports/{plan['id']}",
        json={
            "plan_revision": plan["plan_revision"],
            "content_choices": {"unrelated-choice": "incoming"},
        },
    )
    assert response.status_code == 422
    assert client.get(f"{base}/lists/{detail['id']}").json()["revision"] == detail["revision"]


def test_unchanged_source_does_not_resurrect_a_note_deleted_in_the_system(qa):
    from tests.test_qa_outline import imported, upload_blob, workbook

    detail = imported(qa)
    note = next(item for item in detail["outline"] if item["kind"] == "note")
    detail = mutate(qa, detail, f"/outline/{note['id']}", {"remove": True}, "patch")
    plan = upload_blob(qa, detail, workbook())
    assert plan["review"]["unresolved"] == 0
    saved = commit(qa, detail, plan)
    assert not any(item["kind"] == "note" for item in saved["outline"])


def test_locally_removed_question_stays_removed_until_explicit_restore(qa):
    detail = initial(qa)
    question = first_question(detail)
    detail = mutate(qa, detail, f"/questions/{question['id']}/membership", {"included": False})
    rows = [["Question", "First response"], [question["text"], "Use the supervised entrance."]]
    plan = upload(qa, detail, rows)
    assert plan["review"]["unresolved"] == 0
    saved = commit(qa, detail, plan)
    assert first_question(saved)["included"] is False
    plan = upload(qa, saved, rows)
    item = next(item for item in plan["review"]["conflicts"] if item["kind"] == "membership")
    plan = patch(qa, saved, plan, content_choices={item["id"]: "incoming"})
    restored = commit(qa, saved, plan)
    assert first_question(restored)["included"] is True
