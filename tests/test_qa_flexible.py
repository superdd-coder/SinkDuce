"""General workbook layouts, using independently invented museum records."""

from io import BytesIO
from zipfile import ZipFile

import openpyxl
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from tests.test_qa import create, mutate, qa_fixture  # noqa: F401
from tests.test_qa_excel import commit


def sample():
    wb = openpyxl.Workbook()
    wb.active.title = "Welcome"
    wb.active["A1"] = "Museum correspondence"
    ws = wb.create_sheet("Register")
    ws["B2"] = "Visitor services"
    ws.merge_cells("B2:I2")
    headers = [
        "Ref",
        "Description",
        "Response",
        "Status",
        "Category",
        "Coordinator",
        "Capacity",
        "Next response",
    ]
    for c, label in enumerate(headers, 2):
        ws.cell(4, c, label).font = Font(bold=True)
    ws["B5"] = "Gallery access"
    ws.merge_cells("B5:I5")
    ws["B5"].font = Font(bold=True)
    for r, row in enumerate(
        [
            [
                "M-7",
                "Explain the sculpture hall access procedure.",
                "Use the north entrance.",
                "Awaiting review",
                "Visitor access",
                "Desk team",
                37,
                None,
            ],
            [
                "M-9",
                "Confirm the cloakroom opening hours.",
                None,
                "Verified",
                "Operations",
                "Reception",
                24,
                None,
            ],
        ],
        6,
    ):
        for c, value in enumerate(row, 2):
            ws.cell(r, c, value)
    ws["C6"].fill = PatternFill("solid", fgColor="DDEEFF")
    ws.column_dimensions["C"].width = 52
    ws.row_dimensions[6].height = 48
    ws.freeze_panes = "D6"
    ws["B9"] = "End of register"
    ws.merge_cells("B9:I9")
    dv = DataValidation(type="list", formula1='"Awaiting review,Verified,Deferred"')
    ws.add_data_validation(dv)
    dv.add("E6:E8")
    other = wb.create_sheet("Calculations")
    other["A1"] = "=Register!H6+Register!H7"
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def upload_blob(qa, d, blob):
    client, base = qa
    r = client.post(
        f"{base}/lists/{d['id']}/imports",
        files={"file": ("example.xlsx", blob)},
        data={"expected_revision": d["revision"]},
    )
    assert r.status_code == 200, r.text
    return r.json()


def exported(qa, d):
    client, base = qa
    r = client.post(f"{base}/lists/{d['id']}/exports", json={"expected_revision": d["revision"]})
    assert r.status_code == 200, r.text
    return client.get(f"{base}/lists/{d['id']}/exports/{r.json()['id']}/download").content


def test_detect_main_region_and_keep_unrecognized_columns(qa):
    p = upload_blob(qa, create(qa), sample())
    assert p["sheet"] == "Register"
    assert p["header_row"] == 4
    assert p["region"]["start_column"] == 2
    assert p["mapping"]["extras"] == [6, 7]
    assert p["mapping"]["status"] == 4
    assert p["mapping"]["type"] == 5
    assert [r["key"] for r in p["rows"]] == ["6", "7"]
    assert p["mapping_valid"] is True
    assert {r["kind"] for r in p["table_rows"]} >= {"question", "section"}


def test_custom_options_fields_and_original_layout_round_trip(qa):
    d = create(qa)
    raw = sample()
    d = commit(qa, d, upload_blob(qa, d, raw))
    assert {o["label"] for o in d["options"]["statuses"]} >= {"Verified", "Deferred"}
    assert {o["label"] for o in d["options"]["types"]} >= {"Visitor access", "Operations"}
    q = d["questions"][0]
    assert {f["label"]: f["value"] for f in q["fields"]} == {
        "Coordinator": "Desk team",
        "Capacity": 37,
    }
    reply = next(c for c in d["columns"] if c["included"])
    d = mutate(
        qa,
        d,
        f"/questions/{q['id']}/replies/{reply['id']}",
        {"body": "=Keep this as literal text"},
        "put",
    )
    out = exported(qa, d)
    wb = openpyxl.load_workbook(BytesIO(out))
    assert wb.sheetnames == ["Welcome", "Register", "Calculations"]
    ws = wb["Register"]
    assert ws["D6"].value == "=Keep this as literal text" and ws["D6"].data_type == "s"
    assert ws["H6"].value == 37 and ws["H6"].data_type == "n"
    assert ws["C6"].fill.fgColor.rgb == "00DDEEFF"
    assert ws.column_dimensions["C"].width == 52
    assert "B5:I5" in ws.merged_cells and ws.freeze_panes == "D6"
    with ZipFile(BytesIO(raw)) as original, ZipFile(BytesIO(out)) as result:
        assert original.read("xl/worksheets/sheet1.xml") == result.read("xl/worksheets/sheet1.xml")


def test_option_rename_and_delete_keep_history_and_require_replacement(qa):
    d = create(qa)
    d = mutate(qa, d, "/questions", {"text": "Describe the museum ticket policy."})
    old = d["version"]["id"]
    d = mutate(qa, d, "/versions", {})
    options = d["options"]
    options["statuses"][0]["label"] = "In progress"
    d = mutate(qa, d, "/options", {"options": options}, "patch")
    client, base = qa
    previous = client.get(f"{base}/lists/{d['id']}?version_id={old}").json()
    assert previous["options"]["statuses"][0]["label"] != "In progress"
    options["statuses"] = [o for o in options["statuses"] if o["id"] != "waiting"]
    r = client.patch(
        f"{base}/lists/{d['id']}/options",
        json={"expected_revision": d["revision"], "options": options},
    )
    assert r.status_code == 409
    d = mutate(
        qa,
        d,
        "/options",
        {"options": options, "replacements": {"statuses": {"waiting": "review"}}},
        "patch",
    )
    assert d["questions"][0]["status"] == "review"


def test_unknown_question_row_can_be_reviewed_without_aborting_preview(qa):
    wb = openpyxl.load_workbook(BytesIO(sample()))
    wb["Register"]["D8"] = "A reply without a question"
    out = BytesIO()
    wb.save(out)
    d = create(qa)
    p = upload_blob(qa, d, out.getvalue())
    assert p["mapping_valid"] is False
    assert next(r for r in p["table_rows"] if r["key"] == "8")["kind"] == "review"
    client, base = qa
    r = client.patch(
        f"{base}/lists/{d['id']}/imports/{p['id']}",
        json={"plan_revision": p["plan_revision"], "row_actions": {"8": "continuation"}},
    )
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["mapping_valid"] is True
    d = commit(qa, d, p)
    assert d["questions"][1]["latest_reply"] == "A reply without a question"
    assert next(r for r in p["table_rows"] if r["key"] == "7")["values"][3] == ""


def test_layout_updates_deleted_rows_added_replies_and_cross_sheet_references(qa):
    d = create(qa)
    d = commit(qa, d, upload_blob(qa, d, sample()))
    first, second = d["questions"]
    d = mutate(qa, d, f"/questions/{first['id']}/membership", {"included": False})
    d = mutate(qa, d, "/columns", {"labels": ["Final response"]})
    d = mutate(
        qa,
        d,
        f"/questions/{second['id']}/replies/{d['columns'][-1]['id']}",
        {"body": "Hours confirmed."},
        "put",
    )
    wb = openpyxl.load_workbook(BytesIO(exported(qa, d)))
    ws = wb["Register"]
    assert ws["C6"].value == second["text"]
    assert ws["J4"].value == "Final response" and ws["J6"].value == "Hours confirmed."
    assert wb["Calculations"]["A1"].value == "=#REF!+Register!H6"
    assert "B5:J5" in ws.merged_cells
    d = mutate(
        qa, d, f"/questions/{first['id']}/membership", {"included": True, "restore_replies": True}
    )
    ws = openpyxl.load_workbook(BytesIO(exported(qa, d)))["Register"]
    assert ws["C6"].value == first["text"] and ws["C7"].value == second["text"]


def test_multirow_headers_and_repeated_headers_keep_their_context(qa):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Identity", None, "Correspondence", None])
    ws.merge_cells("A1:B1")
    ws.merge_cells("C1:D1")
    ws.append(["Index", "Description", "Reply", "Owner"])
    ws.append([1, "Explain the planetarium seating plan.", "Seating is assigned.", "Front desk"])
    ws.append(["Index", "Description", "Reply", "Owner"])
    ws.append([2, "Provide the exhibit map.", None, "Visitor desk"])
    for cell in ws[2]:
        cell.font = Font(bold=True)
    out = BytesIO()
    wb.save(out)
    p = upload_blob(qa, create(qa), out.getvalue())
    assert p["header_row"] == 1 and p["region"]["header_end_row"] == 2
    assert len(p["rows"]) == 2
    assert next(r for r in p["table_rows"] if r["key"] == "4")["kind"] == "header"


def test_added_question_exports_at_end_of_table_and_blank_fields_remain_blank(qa):
    d = create(qa)
    d = commit(qa, d, upload_blob(qa, d, sample()))
    d = mutate(qa, d, "/questions", {"text": "Provide the museum accessibility guide."})
    ws = openpyxl.load_workbook(BytesIO(exported(qa, d)))["Register"]
    assert ws["C8"].value == "Provide the museum accessibility guide."
    assert ws["G8"].value is None and ws["H8"].value is None
    assert "B10:I10" in ws.merged_cells


def test_option_changes_remain_isolated_between_lists(qa):
    first, second = create(qa), create(qa)
    options = first["options"]
    options["statuses"][0]["label"] = "Custom queue"
    first = mutate(qa, first, "/options", {"options": options}, "patch")
    client, base = qa
    assert (
        client.get(f"{base}/lists/{second['id']}").json()["options"]["statuses"][0]["label"]
        != "Custom queue"
    )


def test_corrected_identity_moves_original_layout_and_extra_fields(qa):
    d = create(qa)
    d = commit(qa, d, upload_blob(qa, d, sample()))
    old_id = d["questions"][0]["id"]
    client, base = qa
    plans = client.get(f"{base}/lists/{d['id']}/imports").json()
    iid = plans[0]["id"]
    preview = client.post(
        f"{base}/lists/{d['id']}/imports/{iid}/corrections",
        json={"expected_revision": d["revision"], "row_key": "6", "target": "new"},
    )
    assert preview.status_code == 200, preview.text
    changed = client.post(
        f"{base}/lists/{d['id']}/imports/{iid}/corrections/apply",
        json={"preview_id": preview.json()["id"]},
    )
    assert changed.status_code == 200, changed.text
    d = changed.json()
    q = next(
        q
        for q in d["questions"]
        if q["included"] and q["id"] != old_id and q["text"].startswith("Explain")
    )
    assert {f["label"]: f["value"] for f in q["fields"]}["Coordinator"] == "Desk team"
    ws = openpyxl.load_workbook(BytesIO(exported(qa, d)))["Register"]
    assert ws["C6"].value == q["text"] and ws["G6"].value == "Desk team"


def test_section_type_is_inherited_in_system_without_filling_original_blanks(qa):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Index", "Date", "Type", "Question", "Response"])
    from datetime import date

    ws.append([1, date(2031, 2, 4), "Exhibition", None, None])
    ws["A2"].font = Font(bold=True)
    ws["C2"].font = Font(bold=True)
    ws.append(["1.1", None, None, "Describe the gallery audio guide.", None])
    out = BytesIO()
    wb.save(out)
    d = create(qa)
    p = upload_blob(qa, d, out.getvalue())
    assert p["mapping_valid"]
    d = commit(qa, d, p)
    q = d["questions"][0]
    assert next(o["label"] for o in d["options"]["types"] if o["id"] == q["type"]) == "Exhibition"
    ws = openpyxl.load_workbook(BytesIO(exported(qa, d))).active
    assert ws["C2"].value == "Exhibition" and ws["C3"].value is None


def test_legacy_database_migration_keeps_questions_replies_and_foreign_keys(qa):
    import sqlite3

    from src.qa import store

    d = create(qa)
    d = mutate(qa, d, "/questions", {"text": "Explain the ticket exchange process."})
    qid, cid = d["questions"][0]["id"], d["columns"][0]["id"]
    d = mutate(
        qa, d, f"/questions/{qid}/replies/{cid}", {"body": "Contact the visitor desk."}, "put"
    )
    with store.connection("col_qa") as conn:
        location = conn.execute("PRAGMA database_list").fetchone()[2]
    conn = sqlite3.connect(location)
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='qa_version_questions'"
    ).fetchone()[0]
    constrained = sql.replace('"qa_version_questions"', "qa_old_questions").replace(
        "qa_version_questions (", "qa_old_questions ("
    )
    constrained = constrained.replace(
        "status TEXT NOT NULL",
        "status TEXT NOT NULL CHECK(status IN ('waiting','review','closed'))",
    )
    constrained = constrained.replace(
        "type TEXT NOT NULL,",
        "type TEXT NOT NULL CHECK(type IN ('Clarify','Provide','Confirm','Revise','Other')),",
    )
    with conn:
        conn.execute(constrained)
        conn.execute("INSERT INTO qa_old_questions SELECT * FROM qa_version_questions")
        conn.execute("DROP TABLE qa_version_questions")
        conn.execute("ALTER TABLE qa_old_questions RENAME TO qa_version_questions")
        conn.execute("DELETE FROM qa_schema_migrations WHERE version=2")
    conn.close()
    client, base = qa
    reread = client.get(f"{base}/lists/{d['id']}")
    assert reread.status_code == 200, reread.text
    assert reread.json()["questions"] == d["questions"]
    with store.connection("col_qa") as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_structural_export_does_not_delete_unrelated_cells_beside_table(qa):
    wb = openpyxl.load_workbook(BytesIO(sample()))
    wb["Register"]["L6"] = "Separate worksheet annotation"
    out = BytesIO()
    wb.save(out)
    d = create(qa)
    d = commit(qa, d, upload_blob(qa, d, out.getvalue()))
    d = mutate(qa, d, f"/questions/{d['questions'][0]['id']}/membership", {"included": False})
    client, base = qa
    r = client.post(f"{base}/lists/{d['id']}/exports", json={"expected_revision": d["revision"]})
    assert r.status_code == 422 and "unsupported_layout_change" in r.text
    assert client.get(f"{base}/lists/{d['id']}/exports").json() == []


def test_ai_orphan_reply_note_requires_confirmation_without_removing_source(qa, monkeypatch):
    import json

    from src.services import services

    wb = openpyxl.load_workbook(BytesIO(sample()))
    wb["Register"]["D8"] = "Reply instructions for the entire register."
    out = BytesIO()
    wb.save(out)

    class LLM:
        def generate(self, prompt, **kwargs):
            return json.dumps(
                {
                    "sheet": "Register",
                    "header_row": 4,
                    "question": 2,
                    "number": 1,
                    "status": 4,
                    "type": 5,
                    "replies": [3, 8],
                    "row_kinds": {"8": "note"},
                }
            )

    monkeypatch.setattr(services, "llm", LLM())
    d = create(qa)
    p = upload_blob(qa, d, out.getvalue())
    assert p["ai_status"] == "ready" and not p["mapping_valid"]
    orphan = next(row for row in p["table_rows"] if row["key"] == "8")
    assert orphan["kind"] == "review"
    assert orphan["values"][3] == "Reply instructions for the entire register."
    client, base = qa
    response = client.patch(
        f"{base}/lists/{d['id']}/imports/{p['id']}",
        json={"plan_revision": p["plan_revision"], "row_actions": {"8": "note"}},
    )
    assert response.status_code == 200, response.text
    p = response.json()
    assert p["mapping_valid"]
    assert next(r for r in p["table_rows"] if r["key"] == "8")["kind"] == "note"
    assert p["row_actions"]["8"] == "note"


def test_restored_historical_question_keeps_custom_fields_on_new_source_layout(qa):
    d = create(qa)
    d = commit(qa, d, upload_blob(qa, d, sample()))
    old_id = d["questions"][0]["id"]
    wb = openpyxl.load_workbook(BytesIO(sample()))
    wb["Register"].delete_rows(6)
    out = BytesIO()
    wb.save(out)
    d = commit(qa, d, upload_blob(qa, d, out.getvalue()))
    assert not next(q for q in d["questions"] if q["id"] == old_id)["included"]
    d = mutate(
        qa, d, f"/questions/{old_id}/membership", {"included": True, "restore_replies": True}
    )
    ws = openpyxl.load_workbook(BytesIO(exported(qa, d)))["Register"]
    assert ws["G7"].value == "Desk team" and ws["H7"].value == 37


def test_structural_export_updates_whole_column_and_row_references():
    from src.qa.workbook_layout import GridMap

    grid = GridMap([6], [4], 7, 2, 8, 1)
    assert grid.formula("SUM($E:$J)", "Register", "Register") == "SUM($D:$J)"
    assert grid.formula("SUM('Register'!$7:$9)", "Cover", "Register") == "SUM('Register'!$6:$10)"
    assert grid.formula("SUM(D:D)", "Register", "Register") == "SUM(#REF!)"


def test_exported_status_validation_tracks_list_option_edits(qa):
    d = create(qa)
    d = commit(qa, d, upload_blob(qa, d, sample()))
    options = d["options"]
    next(o for o in options["statuses"] if o["label"] == "Verified")["label"] = "Checked"
    d = mutate(qa, d, "/options", {"options": options}, "patch")
    ws = openpyxl.load_workbook(BytesIO(exported(qa, d)))["Register"]
    assert ws["E7"].value == "Checked"
    validation = next(v for v in ws.data_validations.dataValidation if "E7" in v)
    assert "Checked" in validation.formula1 and "Verified" not in validation.formula1


def test_long_option_labels_use_a_preserved_hidden_range_in_export(qa):
    d = create(qa)
    d = commit(qa, d, upload_blob(qa, d, sample()))
    options = d["options"]
    label = "Queue, awaiting museum review"
    next(o for o in options["statuses"] if o["label"] == "Verified")["label"] = label
    d = mutate(qa, d, "/options", {"options": options}, "patch")
    raw = exported(qa, d)
    wb = openpyxl.load_workbook(BytesIO(raw))
    ws = wb["Register"]
    validation = next(v for v in ws.data_validations.dataValidation if "E7" in v)
    destination = next(wb.defined_names[validation.formula1].destinations)
    assert wb[destination[0]].sheet_state == "hidden"
    values = [c.value for row in wb[destination[0]][destination[1]] for c in row]
    assert label in values
    assert wb["Calculations"]["A1"].value == "=Register!H6+Register!H7"
    from src.qa.workbook_reader import read_workbook

    parsed = read_workbook(raw)
    assert label in parsed["validations"]["4"]
