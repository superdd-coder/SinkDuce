"""Real workbook round trips, identity continuity, and import atomicity."""

from io import BytesIO

import openpyxl

from tests.test_qa import create, mutate, qa_fixture  # noqa: F401


def workbook(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Example"
    for row in rows:
        ws.append(row)
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def upload(qa, d, rows):
    client, base = qa
    r = client.post(
        f"{base}/lists/{d['id']}/imports",
        files={"file": ("example.xlsx", workbook(rows))},
        data={"expected_revision": d["revision"]},
    )
    assert r.status_code == 200, r.text
    return r.json()


def commit(qa, d, plan):
    client, base = qa
    r = client.post(
        f"{base}/lists/{d['id']}/imports/{plan['id']}/commit",
        json={"plan_revision": plan["plan_revision"]},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_real_excel_import_export_keeps_empty_columns_and_plain_text(qa):
    d = create(qa)
    plan = upload(
        qa,
        d,
        [
            ["No", "Question", "A response", "B response"],
            ["1", "Explain the greenhouse timer.", "Timer verified", None],
        ],
    )
    d = commit(qa, d, plan)
    assert d["questions"][0]["text"] == "Explain the greenhouse timer."
    assert len([c for c in d["columns"] if c["included"]]) == 2
    # Excel formulas without cached values are rejected separately; use a saved literal here.
    qid, cid = d["questions"][0]["id"], next(c["id"] for c in d["columns"] if c["included"])
    d = mutate(
        qa, d, f"/questions/{qid}/replies/{cid}", {"body": "=literal text\nnext line"}, "put"
    )
    client, base = qa
    r = client.post(
        f"{base}/lists/{d['id']}/exports",
        json={"expected_revision": d["revision"], "include_status": True, "include_type": True},
    )
    assert r.status_code == 200, r.text
    export_id = r.json()["id"]
    raw = client.get(f"{base}/lists/{d['id']}/exports/{export_id}/download")
    assert raw.status_code == 200
    wb = openpyxl.load_workbook(BytesIO(raw.content))
    ws = wb.active
    assert all(ws.column_dimensions[c].hidden is not True for c in ws.column_dimensions)
    values = [cell.value for row in ws for cell in row if cell.value is not None]
    assert qid not in values and cid not in values
    literal = next(cell for row in ws for cell in row if cell.value == "=literal text\nnext line")
    assert literal.data_type == "s"
    d = mutate(qa, d, f"/questions/{qid}", {"text": "Changed wording after export"}, "patch")
    assert client.get(f"{base}/lists/{d['id']}/exports/{export_id}/download").content == raw.content


def test_missing_questions_persist_and_return_without_external_ids(qa):
    d = create(qa)
    d = commit(
        qa,
        d,
        upload(
            qa,
            d,
            [
                ["Question", "Reply"],
                ["Provide the botanical map.", "Available"],
                ["Confirm the aquarium hours.", "Pending"],
            ],
        ),
    )
    original = {q["text"]: q["id"] for q in d["questions"]}
    d = commit(
        qa, d, upload(qa, d, [["Question", "Reply"], ["Confirm the aquarium hours.", "Updated"]])
    )
    d = commit(
        qa, d, upload(qa, d, [["Question", "Reply"], ["Confirm the aquarium hours.", "Updated"]])
    )
    hidden = next(q for q in d["questions"] if q["id"] == original["Provide the botanical map."])
    assert hidden["included"] is False
    previous_version = d["version"]["id"]
    d = commit(
        qa,
        d,
        upload(
            qa,
            d,
            [
                ["Question", "Reply"],
                ["Provide the botanical map.", "Restored"],
                ["Confirm the aquarium hours.", None],
            ],
        ),
    )
    assert (
        next(q for q in d["questions"] if q["text"] == "Provide the botanical map.")["id"]
        == hidden["id"]
    )
    assert all(q["included"] for q in d["questions"])
    assert (
        next(q for q in d["questions"] if q["text"] == "Confirm the aquarium hours.")[
            "latest_reply"
        ]
        == ""
    )
    assert next(q for q in d["questions"] if q["id"] == hidden["id"])["latest_reply"] == "Restored"
    client, base = qa
    previous = client.get(f"{base}/lists/{d['id']}?version_id={previous_version}").json()
    assert next(q for q in previous["questions"] if q["id"] == original["Confirm the aquarium hours."])["latest_reply"] == "Updated"


def test_import_is_stale_safe_idempotent_and_duplicate_assignment_blocks(qa):
    d = create(qa)
    d = mutate(qa, d, "/questions", {"text": "Confirm the arboretum hours."})
    plan = upload(qa, d, [["Question", "A response"], ["Confirm the arboretum hours.", "Open"]])
    completed = commit(qa, d, plan)
    assert commit(qa, d, plan)["version"]["id"] == completed["version"]["id"]
    client, base = qa
    duplicate = upload(
        qa,
        completed,
        [
            ["Question", "A response"],
            ["Confirm the arboretum hours.", "One"],
            ["Confirm the arboretum hours.", "Two"],
        ],
    )
    r = client.post(
        f"{base}/lists/{d['id']}/imports/{duplicate['id']}/commit",
        json={"plan_revision": duplicate["plan_revision"]},
    )
    assert r.status_code == 409
    fresh = upload(
        qa, completed, [["Question", "A response"], ["Confirm the arboretum hours.", "New"]]
    )
    changed = mutate(
        qa,
        completed,
        f"/questions/{completed['questions'][0]['id']}",
        {"status": "closed"},
        "patch",
    )
    r = client.post(
        f"{base}/lists/{d['id']}/imports/{fresh['id']}/commit",
        json={"plan_revision": fresh["plan_revision"]},
    )
    assert r.status_code == 409
    assert client.get(f"{base}/lists/{d['id']}").json()["revision"] == changed["revision"]


def test_column_rename_uses_retained_content_and_archives_the_absent_round(qa):
    d = create(qa)
    rows = [
        ["Question", "First reply", "Middle reply", "Last reply"],
        [
            "Explain the nursery watering cycle.",
            "Watering is scheduled for the early morning.",
            "The controller was inspected by the operator.",
            "The next inspection is already scheduled.",
        ],
    ]
    d = commit(qa, d, upload(qa, d, rows))
    ids = {c["label"]: c["id"] for c in d["columns"] if c["included"]}
    plan = upload(
        qa,
        d,
        [["Question", "Updated first label", "Last reply"], [rows[1][0], rows[1][1], rows[1][3]]],
    )
    # Unknown headings are preserved as fields until the user marks the column as a reply.
    client, base = qa
    result = client.patch(
        f"{base}/lists/{d['id']}/imports/{plan['id']}",
        json={
            "plan_revision": plan["plan_revision"],
            "mapping": {"question": 0, "replies": [1, 2]},
        },
    )
    assert result.status_code == 200, result.text
    plan = result.json()
    assert plan["column_matches"]["1"] == ids["First reply"]
    d = commit(qa, d, plan)
    old_three = [c for c in d["columns"] if c["id"] in ids.values()]
    assert [c["id"] for c in old_three] == list(ids.values())
    assert old_three[1]["included"] is False
    assert next(reply for reply in d["questions"][0]["replies"] if reply["column_id"] == ids["Middle reply"])["body"] == rows[1][2]


def test_formula_without_cache_is_not_silently_imported(qa):
    d = create(qa)
    client, base = qa
    r = client.post(
        f"{base}/lists/{d['id']}/imports",
        files={
            "file": (
                "formula.xlsx",
                workbook(
                    [["Question", "Reply"], ["Explain the meadow monitoring schedule.", "=1+1"]]
                ),
            )
        },
        data={"expected_revision": d["revision"]},
    )
    assert r.status_code == 422
    assert "formula_without_cached_value" in r.text
    assert client.get(f"{base}/lists/{d['id']}").json()["revision"] == d["revision"]


def test_first_file_creates_v1_and_preparation_is_not_an_active_list(qa):
    client, base = qa
    response = client.post(base + "/lists", json={"title": "Example import", "mode": "import"})
    assert response.status_code == 201, response.text
    d = response.json()
    assert d["version"] is None
    assert client.get(base + "/lists").json() == []
    plan = upload(qa, d, [["Question", "Next response"], ["Provide the garden map.", None]])
    d = commit(qa, d, plan)
    assert d["version"]["number"] == 1
    assert len(client.get(f"{base}/lists/{d['id']}/versions").json()) == 1


def test_header_preview_can_be_corrected_before_question_validation(qa):
    client, base = qa
    d = create(qa)
    plan = upload(
        qa,
        d,
        [
            ["Cover title"],
            [None],
            ["Item code", "Topic", "Answer"],
            [None, "Explain the library opening schedule.", None],
        ],
    )
    assert plan["mapping_valid"] is False
    r = client.patch(
        f"{base}/lists/{d['id']}/imports/{plan['id']}",
        json={
            "plan_revision": plan["plan_revision"],
            "header_row": 3,
            "mapping": {"question": 1, "number": 0, "replies": [2]},
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["mapping_valid"] is True
    d = commit(qa, d, r.json())
    assert d["questions"][0]["text"] == "Explain the library opening schedule."


def test_omitted_question_keeps_last_included_reply_names_and_restores_again(qa):
    client, base = qa
    d = client.post(
        base + "/lists", json={"title": "Example pond checklist", "mode": "import"}
    ).json()
    d = commit(
        qa,
        d,
        upload(
            qa,
            d,
            [
                ["Question", "Response"],
                [
                    "Confirm the conservatory schedule.",
                    "The schedule is available at the entrance.",
                ],
                ["Provide the pond map.", "The map is available in the visitors guide."],
            ],
        ),
    )
    first = d["version"]["number"]
    qid = d["questions"][1]["id"]
    d = commit(
        qa,
        d,
        upload(
            qa,
            d,
            [
                ["Question", "Response", "New follow up"],
                [
                    "Confirm the conservatory schedule.",
                    "The schedule is available at the entrance.",
                    None,
                ],
            ],
        ),
    )
    hidden = next(q for q in d["questions"] if q["id"] == qid)
    assert [r["label"] for r in hidden["replies"]] == ["Response"]
    assert hidden["last_included_version"] == first
    d = mutate(qa, d, "/versions", {})
    d = mutate(qa, d, f"/questions/{qid}/membership", {"included": True, "restore_replies": True})
    assert (
        next(q for q in d["questions"] if q["id"] == qid)["latest_reply"]
        == "The map is available in the visitors guide."
    )
    d = mutate(qa, d, "/versions", {})
    d = mutate(qa, d, f"/questions/{qid}/membership", {"included": False})
    assert (
        next(q for q in d["questions"] if q["id"] == qid)["absent_since"] == d["version"]["number"]
    )


def test_pending_import_can_resume_and_cancel_without_activating_a_list(qa):
    client, base = qa
    d = client.post(
        base + "/lists", json={"title": "Example resumed import", "mode": "import"}
    ).json()
    p = upload(qa, d, [["Question"], ["Describe the orchard route."]])
    r = client.get(base + "/imports/pending")
    assert r.status_code == 200, r.text
    assert r.json()[0]["plan"]["id"] == p["id"]
    assert (
        client.delete(
            f"{base}/lists/{d['id']}/imports/{p['id']}",
            params={"plan_revision": p["plan_revision"]},
        ).status_code
        == 200
    )
    assert client.get(base + "/imports/pending").json() == []
    assert (
        client.post(
            f"{base}/lists/{d['id']}/imports/{p['id']}/commit",
            json={"plan_revision": p["plan_revision"]},
        ).status_code
        == 409
    )
    assert client.get(base + "/lists").json() == []


def test_empty_cover_sheet_does_not_block_selecting_the_actual_list(qa):
    client, base = qa
    d = create(qa)
    wb = openpyxl.Workbook()
    wb.active.title = "Cover"
    ws = wb.create_sheet("Checklist")
    ws.append(["Question", "Reply"])
    ws.append(["Provide the observatory visitor map.", None])
    out = BytesIO()
    wb.save(out)
    r = client.post(
        f"{base}/lists/{d['id']}/imports",
        files={"file": ("cover.xlsx", out.getvalue())},
        data={"expected_revision": d["revision"]},
    )
    assert r.status_code == 200, r.text
    plan = r.json()
    r = client.patch(
        f"{base}/lists/{d['id']}/imports/{plan['id']}",
        json={"plan_revision": plan["plan_revision"], "sheet": "Checklist", "header_row": 1},
    )
    assert r.status_code == 200, r.text
    assert r.json()["rows"][0]["text"] == "Provide the observatory visitor map."


def test_two_renamed_columns_cannot_compete_for_one_old_column(qa):
    client, base = qa
    d = client.post(
        base + "/lists", json={"title": "Example archive review", "mode": "import"}
    ).json()
    content = "The fictional archive is available to registered visitors."
    d = commit(
        qa, d, upload(qa, d, [["Question", "Response"], ["Explain archive access.", content]])
    )
    p = upload(
        qa,
        d,
        [["Question", "Team note", "Operator note"], ["Explain archive access.", content, content]],
    )
    assert p["column_matches"] == {"1": None, "2": None}
