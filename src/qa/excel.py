"""Structured .xlsx input/output; no internal identities in exported workbooks."""

from io import BytesIO

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .numbering import display_numbers
from .reply_format import rich_reply
from .workbook_reader import MAX_EXPANDED, MAX_UPLOAD, default_mapping, read_workbook

__all__ = ["MAX_EXPANDED", "MAX_UPLOAD", "default_mapping", "read_workbook", "write_workbook"]


def write_workbook(snapshot, options, logo_path=None):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Q&A"
    columns = [c for c in snapshot["columns"] if c["included"]]
    headers = (["No"] if options.get("include_number", True) else []) + ["Question"]
    if options.get("include_status"):
        headers.append("Status")
    headers += [c["label"] for c in columns]
    ws.append([options.get("title") or snapshot["title"]])
    position = max(1, len(headers) - 1) if options.get("metadata_position") == "right" else 1
    ws.cell(2, position, f"V{snapshot['version']['number']}")
    ws.cell(2, position + 1, options["date"])
    ws.append([])
    ws.append(headers)
    questions = {q["id"]: q for q in snapshot["questions"] if q["included"]}
    items = snapshot.get("outline") or [{"id": qid, "kind": "question"} for qid in questions]
    automatic = display_numbers(snapshot, snapshot["questions"], automatic=True)
    depths, styled_rows = {}, []
    for item in items:
        if item["kind"] in ("group", "note"):
            numbered = item["kind"] == "group" and options.get("include_number", True)
            number = (
                automatic.get(item["id"], "")
                if options.get("resequence")
                else item.get("display_number", automatic.get(item["id"], ""))
            )
            ws.append(([number] if numbered else []) + [item["text"]])
            text_column = 2 if numbered else 1
            if len(headers) > text_column:
                ws.merge_cells(
                    start_row=ws.max_row,
                    start_column=text_column,
                    end_row=ws.max_row,
                    end_column=len(headers),
                )
            ws.cell(ws.max_row, text_column).font = Font(
                bold=item["kind"] == "group", italic=item["kind"] == "note"
            )
            continue
        if item["id"] not in questions:
            continue
        q = questions[item["id"]]
        row = (
            [
                automatic[q["id"]]
                if options.get("resequence")
                else q.get("display_number", automatic[q["id"]])
            ]
            if options.get("include_number", True)
            else []
        ) + [q["text"]]
        if options.get("include_status"):
            row.append(
                next(
                    (
                        o["label"]
                        for o in snapshot.get("options", {}).get("statuses", [])
                        if o["id"] == q["status"]
                    ),
                    q["status"],
                )
            )
        replies = {r["column_id"]: r["body"] for r in q["replies"]}
        row.extend(rich_reply(replies.get(c["id"], "")) for c in columns)
        ws.append(row)
        depth = depths.get(item.get("parent_id"), -1) + 1
        depths[item["id"]] = depth
        styled_rows.append((ws.max_row, depth))
    for row in ws:
        for cell in row:
            if cell.value is not None:
                cell.data_type = "s"
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for r, depth in styled_rows:
        ws.cell(r, 2 if options.get("include_number", True) else 1).alignment = Alignment(
            vertical="top", wrap_text=True, indent=min(250, depth)
        )
    for cell in ws[4]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="275A43")
    ws["A1"].font = Font(size=17, bold=True, color="275A43")
    if logo_path:
        from openpyxl.drawing.image import Image

        image = Image(str(logo_path))
        scale = min(110 / image.width, 45 / image.height)
        image.width, image.height = image.width * scale, image.height * scale
        anchor = f"{get_column_letter(max(2, len(headers)))}1"
        if options.get("logo_position") == "left":
            ws["B1"] = ws["A1"].value
            ws["B1"].font = Font(size=17, bold=True, color="275A43")
            ws["A1"] = None
            anchor = "A1"
        ws.add_image(image, anchor)
        ws.row_dimensions[1].height = 42
    for i in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(i)].width = (
            12 if headers[i - 1] in {"No", "Status", "Type"} else 48
        )
    if logo_path and options.get("logo_position") == "left":
        ws.column_dimensions["A"].width = max(17, ws.column_dimensions["A"].width)
    ws.freeze_panes = "B5"
    ws.auto_filter.ref = f"A4:{get_column_letter(len(headers))}{max(4, ws.max_row)}"
    out = BytesIO()
    wb.save(out)
    return out.getvalue()
