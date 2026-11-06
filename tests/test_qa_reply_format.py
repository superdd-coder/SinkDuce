"""Reply emphasis survives both export paths and a subsequent import."""

from io import BytesIO
from zipfile import ZipFile

import openpyxl
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.styles import Font

from tests.test_qa import create, mutate, qa_fixture  # noqa: F401
from tests.test_qa_excel import commit
from tests.test_qa_flexible import exported, sample, upload_blob

BODY = "Please confirm **the east entrance** and *opening hours*.\n\nKeep ***both styles***."
PLAIN = "Please confirm the east entrance and opening hours.\n\nKeep both styles."


def assert_emphasis(value):
    assert isinstance(value, CellRichText)
    assert str(value) == PLAIN
    runs = {r.text: (bool(r.font.b), bool(r.font.i)) for r in value if isinstance(r, TextBlock)}
    assert runs["the east entrance"] == (True, False)
    assert runs["opening hours"] == (False, True)
    assert runs["both styles"] == (True, True)


def test_template_export_has_real_emphasis_and_safe_plain_text(qa):
    d = mutate(qa, create(qa), "/questions", {"text": "Confirm visitor access."})
    qid, cid = d["questions"][0]["id"], d["columns"][0]["id"]
    d = mutate(qa, d, f"/questions/{qid}/replies/{cid}", {"body": BODY}, "put")
    wb = openpyxl.load_workbook(BytesIO(exported(qa, d)), rich_text=True)
    reply = next(c for row in wb.active for c in row if str(c.value) == PLAIN)
    assert_emphasis(reply.value)
    assert reply.data_type == "s"


def test_original_layout_emphasis_new_column_and_reimport(qa):
    source = openpyxl.load_workbook(BytesIO(sample()))
    source["Register"]["D6"].font = Font(name="Arial", size=13, color="244466")
    data = BytesIO()
    source.save(data)
    original = data.getvalue()
    d = create(qa)
    d = commit(qa, d, upload_blob(qa, d, original))
    qid = d["questions"][0]["id"]
    cid = next(c["id"] for c in d["columns"] if c["included"])
    d = mutate(qa, d, f"/questions/{qid}/replies/{cid}", {"body": BODY}, "put")
    d = mutate(qa, d, "/columns", {"labels": ["Additional reply"]})
    d = mutate(qa, d, f"/questions/{qid}/replies/{d['columns'][-1]['id']}", {"body": BODY}, "put")
    output = exported(qa, d)
    ws = openpyxl.load_workbook(BytesIO(output), rich_text=True)["Register"]
    assert_emphasis(ws["D6"].value)
    for run in ws["D6"].value:
        assert run.font.rFont == "Arial"
        assert run.font.sz == 13
        assert run.font.color.rgb == "00244466"
    assert_emphasis(ws["J6"].value)
    assert ws.freeze_panes == "D6"
    assert ws["C6"].fill.fgColor.rgb == "00DDEEFF"
    assert ws.column_dimensions["C"].width == 52
    with ZipFile(BytesIO(original)) as before, ZipFile(BytesIO(output)) as after:
        assert before.read("xl/worksheets/sheet1.xml") == after.read("xl/worksheets/sheet1.xml")
    # Importing an exchanged workbook must retain editable emphasis, not just plain text.
    other = create(qa)
    other = commit(qa, other, upload_blob(qa, other, output))
    from src.qa.reply_format import reply_runs

    body = next(r["body"] for r in other["questions"][0]["replies"] if r["included"])
    assert reply_runs(body) == reply_runs(BODY)


def test_reply_format_preserves_literals_newlines_and_nests_marks():
    from src.qa.reply_format import reply_runs, rich_reply

    value = rich_reply("=literal **bold**\n\n# No heading\n<script>x</script> a_b_c \\*literal\\*")
    assert str(value) == "=literal bold\n\n# No heading\n<script>x</script> a_b_c *literal*"
    assert_emphasis(rich_reply(BODY))
    assert reply_runs("**line one\nline two**")[0].text == "line one\nline two"
    assert str(rich_reply("# **label** &amp; \\*literal\\*")) == "# label & *literal*"


def test_unchanged_imported_markdown_is_formatted_only_in_reply_cells(qa):
    wb = openpyxl.load_workbook(BytesIO(sample()))
    wb["Register"]["D6"] = BODY
    wb["Register"]["G6"] = "**literal field value**"
    raw = BytesIO()
    wb.save(raw)
    d = create(qa)
    d = commit(qa, d, upload_blob(qa, d, raw.getvalue()))
    output = exported(qa, d)
    ws = openpyxl.load_workbook(BytesIO(output), rich_text=True)["Register"]
    assert_emphasis(ws["D6"].value)
    assert ws["G6"].value == "**literal field value**"
    # A native rich-text cell that is not edited retains its original runs.
    other = create(qa)
    other = commit(qa, other, upload_blob(qa, other, output))
    ws = openpyxl.load_workbook(BytesIO(exported(qa, other)), rich_text=True)["Register"]
    assert_emphasis(ws["D6"].value)
