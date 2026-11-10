"""Bounded structural discovery. Source coordinates and typed cells stay intact."""

import re
from datetime import date, datetime
from io import BytesIO
from itertools import groupby, pairwise
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

import openpyxl
from openpyxl.cell.rich_text import CellRichText

from .reply_format import rich_to_markdown
from .store import fail

MAX_UPLOAD = 20 * 1024 * 1024
MAX_EXPANDED = 200 * 1024 * 1024


def text(value):
    return "" if value is None else str(value)


def _header_name(value):
    return re.sub(r"[\s_.-]+", " ", text(value).split(" / ")[-1].strip().casefold())


def header_role(value):
    value = _header_name(value)
    if re.search(r"status|readiness|状态|进度", value):
        return "status"
    if re.fullmatch(
        r"(?:(?:question|request|thematic|topic|theme) )?(?:group|grouping|topic|theme|section|workstream)(?: name)?"
        r"|(?:问题|请求|主题)?(?:分组|群組|群组|主题|主題|议题|議題|章节|章節)",
        value,
    ):
        return "group"
    if re.search(r"category|\btype\b|类别|類別|类型|類型", value):
        return "type"
    if re.search(r"repl|respons|answer|follow up|\bnote\b|comment|回复|答复|反馈", value):
        return "reply"
    if re.fullmatch(r"no\.?|number|index|ref|reference|序号|编号|#", value):
        return "number"
    if re.search(
        r"question|query|queries|description|detail|information required|request for information|问题|事项描述",
        value,
    ):
        return "question"
    return "extra"


def number_header_columns(headers):
    return {i for i, label in enumerate(headers) if header_role(label) == "number"}


def section_title_columns(values, columns, ordinal_columns):
    """Separate a heading's text from ordinals in their own numbered cells."""
    labels = [
        i
        for i in columns
        if not (i in ordinal_columns and re.fullmatch(r"\d+(?:\.\d+)*[.)]?", values[i].strip()))
    ]
    return labels or columns


def section_semantic_columns(row, nonempty, number_column, ordinal_columns):
    typed = row.get("typed")
    semantic = [
        i
        for i in nonempty
        if i != number_column and (not typed or typed[i].get("kind") not in ("date", "n"))
    ]
    return section_title_columns(row["values"], semantic, ordinal_columns)


def _load(blob):
    if len(blob) > MAX_UPLOAD:
        fail("parse_limit_exceeded", 413)
    try:
        with ZipFile(BytesIO(blob)) as archive:
            if sum(e.file_size for e in archive.infolist()) > MAX_EXPANDED:
                fail("parse_limit_exceeded", 413)
            # Bound actual XML size/cells, rather than Excel's often inflated used range.
            size = 0
            for entry in archive.infolist():
                with archive.open(entry) as stream:
                    while chunk := stream.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_EXPANDED:
                            fail("parse_limit_exceeded", 413)
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", entry.filename):
                    with archive.open(entry) as stream:
                        count = 0
                        for _, element in ET.iterparse(stream, events=("end",)):
                            if element.tag.endswith("}c"):
                                count += 1
                                if count > 250_000:
                                    fail("parse_limit_exceeded", 413)
                            element.clear()
        return (
            openpyxl.load_workbook(BytesIO(blob), data_only=False, rich_text=True),
            openpyxl.load_workbook(BytesIO(blob), data_only=True, rich_text=True),
        )
    except (BadZipFile, KeyError, ValueError, OSError, ET.ParseError):
        fail("unsupported_workbook", 422)


def _candidates(wb):
    candidates = []
    for ws in wb:
        populated = [c for c in ws._cells.values() if c.value is not None and text(c.value).strip()]
        by_row = {}
        for c in populated:
            by_row.setdefault(c.row, []).append(c)
        for r, cells in by_row.items():
            if r > 2020:
                continue
            short = [c for c in cells if len(text(c.value)) < 120]
            roles = [header_role(c.value) for c in short]
            structural = (
                len(short) >= 3
                and all(isinstance(c.value, str) for c in short)
                and sum(len(c.value) for c in short) / len(short) < 45
            )
            if "question" not in roles and not structural:
                continue
            score = (
                (8 if "question" in roles else 1)
                + min(6, sum(x != "extra" for x in roles))
                + (2 if "reply" in roles else 0)
            )
            score += sum(bool(c.font.bold) for c in short) / max(1, len(short))
            score -= 3 if ws.sheet_state != "visible" else 0
            # A later repeated heading is still offered, but does not outrank the first.
            parents = [
                m for m in ws.merged_cells.ranges if m.max_row == r - 1 and m.max_col > m.min_col
            ]
            header_start = r - 1 if len(parents) >= 2 else r
            candidates.append(
                {
                    "sheet": ws.title,
                    "header_row": header_start,
                    "header_end_row": r,
                    "score": round(score, 2),
                    "start_column": min(c.column for c in cells),
                    "end_column": max(c.column for c in cells),
                }
            )
    return sorted(
        candidates, key=lambda c: (-c["score"], wb.sheetnames.index(c["sheet"]), c["header_row"])
    )[:30]


def read_workbook(blob, sheet=None, header_row=None, region=None):
    wb, cached = _load(blob)
    try:
        candidates = _candidates(wb)
        for candidate in candidates:
            tab = cached[candidate["sheet"]]
            candidate["preview"] = [
                {
                    "row": r,
                    "values": [
                        text(tab.cell(r, c).value) for c in range(1, candidate["end_column"] + 1)
                    ],
                }
                for r in range(
                    candidate["header_row"], min(tab.max_row, candidate["header_row"] + 4) + 1
                )
            ]
        selected = next((c for c in candidates if not sheet or c["sheet"] == sheet), None)
        name = sheet or (selected["sheet"] if selected else wb.sheetnames[0])
        if name not in wb.sheetnames:
            fail("invalid_sheet", 422)
        ws, values = wb[name], cached[name]
        actual = [c for c in ws._cells.values() if c.value is not None and text(c.value).strip()]
        last_row = max((c.row for c in actual), default=1)
        last_col = max((c.column for c in actual), default=1)
        if last_row > 2020 or last_col > 100:
            fail("parse_limit_exceeded", 413)
        auto_header = header_row is None
        header_row = header_row or (selected["header_row"] if selected else 1)
        detected_end = selected["end_column"] if selected else last_col
        # Include unnamed populated columns aligned with the body, not isolated notes elsewhere.
        if selected:
            qcols = [
                c.column
                for c in ws[selected["header_end_row"]]
                if header_role(c.value) == "question"
            ]
            if qcols:
                body = [
                    r
                    for r in range(selected["header_end_row"] + 1, last_row + 1)
                    if ws.cell(r, qcols[0]).value is not None
                ]
                for c in range(detected_end + 1, last_col + 1):
                    if sum(ws.cell(r, c).value is not None for r in body) >= 2:
                        detected_end = c
        region = {
            "start_column": (selected["start_column"] if selected else 1),
            "end_column": detected_end,
            "end_row": last_row,
            "header_end_row": selected["header_end_row"]
            if selected and auto_header
            else header_row,
            **(region or {}),
        }
        if not (
            1 <= header_row <= region["header_end_row"] <= region["end_row"] <= 2020
            and 1 <= region["start_column"] <= region["end_column"] <= 100
        ):
            fail("invalid_header", 422)
        last_col = region["end_column"]
        merges = [[m.min_row, m.min_col, m.max_row, m.max_col] for m in ws.merged_cells.ranges]

        def header_value(r, c):
            cell = ws.cell(r, c)
            for a, b, d, e in merges:
                if a <= r <= d and b <= c <= e:
                    cell = ws.cell(a, b)
                    break
            return text(cell.value).strip()

        headers = []
        for c in range(1, last_col + 1):
            parts = list(
                dict.fromkeys(
                    header_value(r, c) for r in range(header_row, region["header_end_row"] + 1)
                )
            )
            headers.append(" / ".join(p for p in parts if p))
        rows, count = [], 0
        for r in range(region["header_end_row"] + 1, region["end_row"] + 1):
            row, typed, formatted = [], [], {}
            for c in range(1, last_col + 1):
                cell = ws.cell(r, c)
                value = values.cell(r, c).value if cell.data_type == "f" else cell.value
                if cell.data_type == "f" and value is None:
                    fail("formula_without_cached_value", 422)
                count += len(text(value))
                if count > 2_000_000 or len(text(value)) > 32767:
                    fail("parse_limit_exceeded", 413)
                row.append(text(value))
                if isinstance(value, CellRichText):
                    formatted[str(c - 1)] = rich_to_markdown(value)
                    value = str(value)
                typed.append(
                    {
                        "value": value.isoformat()
                        if isinstance(value, (date, datetime))
                        else value,
                        "kind": "date" if isinstance(value, (date, datetime)) else cell.data_type,
                    }
                )
            if any(row[region["start_column"] - 1 :]):
                rows.append(
                    {
                        "key": str(r),
                        "values": row,
                        "typed": typed,
                        "formatted_values": formatted,
                        "bold": [c for c in range(1, last_col + 1) if ws.cell(r, c).font.bold],
                        "italic": [c for c in range(1, last_col + 1) if ws.cell(r, c).font.italic],
                        "indents": {
                            str(c - 1): ws.cell(r, c).alignment.indent
                            for c in range(1, last_col + 1)
                            if ws.cell(r, c).alignment.indent
                        },
                        "merges": [m for m in merges if m[0] <= r <= m[2]],
                    }
                )
        preview = [
            {"row": r, "values": [text(values.cell(r, c).value) for c in range(1, last_col + 1)]}
            for r in sorted(
                set(range(max(1, header_row - 2), min(last_row, header_row + 9) + 1))
                | {int(row["key"]) for row in rows[:: max(1, len(rows) // 12)]}
            )
        ]
        validations = {}
        for dv in ws.data_validations.dataValidation:
            if dv.type != "list" or not dv.formula1:
                continue
            items = []
            formula = dv.formula1.lstrip("=")
            if formula.startswith('"') and formula.endswith('"'):
                items = formula[1:-1].split(",")
            else:
                try:
                    destinations = (
                        [(name, formula)]
                        if "!" not in formula
                        else [(formula.rsplit("!", 1)[0].strip("'"), formula.rsplit("!", 1)[1])]
                    )
                    if formula in wb.defined_names:
                        destinations = list(wb.defined_names[formula].destinations)
                    for target, ref in destinations:
                        if target in cached.sheetnames and re.fullmatch(
                            r"\$?[A-Z]+\$?\d+(?::\$?[A-Z]+\$?\d+)?", ref
                        ):
                            area = cached[target][ref]
                            if not isinstance(area, tuple):
                                area = ((area,),)
                            if sum(len(line) for line in area) <= 500:
                                items.extend(
                                    text(c.value)
                                    for line in area
                                    for c in line
                                    if c.value is not None
                                )
                except (ValueError, KeyError, AttributeError):
                    pass
            for c in range(region["start_column"], last_col + 1):
                if any(
                    rng.min_col <= c <= rng.max_col and rng.max_row > region["header_end_row"]
                    for rng in dv.ranges.ranges
                ):
                    validations[str(c - 1)] = list(dict.fromkeys(items))[:200]
        return {
            "sheets": wb.sheetnames,
            "sheet": name,
            "header_row": header_row,
            "region": region,
            "headers": headers,
            "header_values": [
                [text(values.cell(r, c).value) for c in range(1, last_col + 1)]
                for r in range(header_row, region["header_end_row"] + 1)
            ],
            "rows": rows,
            "preview": preview,
            "candidates": candidates,
            "validations": validations,
        }
    finally:
        wb.close()
        cached.close()


_AMBIGUOUS_TYPE_HEADERS = {"type", "category", "类型", "類型", "类别", "類別"}


def _action_type_label(label):
    return bool(
        re.match(r"(?:clarify|provide|confirm|revise|request|review)(?:\b|$)", label)
        or label
        in {
            "clarification",
            "confirmation",
            "verification",
            "revision",
            "inquiry",
            "other",
            "information request",
            "document request",
            "data request",
            "澄清",
            "提供",
            "确认",
            "確認",
            "核实",
            "核實",
            "修订",
            "修訂",
            "修改",
            "其他",
        }
    )


def _topic_label(label, *, phrase=False):
    if not label or len(label) > 100 or "?" in label or "？" in label:
        return False
    if re.match(
        r"(?:category|type|class|grade|level|tier|priority|option)\b", label
    ) or label.startswith(("类别", "類別", "类型", "類型", "选项", "選項", "级别", "級別")):
        return False
    words = re.findall(r"[^\W\d_]+", label)
    if not phrase:
        return any(len(word) >= 2 for word in words)
    return (
        sum(len(word) >= 2 for word in words) >= 2
        or len(re.findall(r"[\u3400-\u9fff]", label)) >= 2
    )


def _supports_group_column(parsed, index, table):
    header = _header_name(parsed["headers"][index])
    role = header_role(header)
    if role not in ("extra", "type") or role == "type" and header not in _AMBIGUOUS_TYPE_HEADERS:
        return False
    if str(index) in parsed.get("validations", {}):
        # A list choice on an ambiguous type column is evidence of an intentional vocabulary.
        return False
    body = [row for row in table if row["kind"] in ("question", "child")]
    raw = {int(row["key"]): " ".join(row["values"][index].casefold().split()) for row in body}
    if any(_action_type_label(label) for label in raw.values()):
        return False
    column = index + 1
    for row, following in pairwise(body):
        if _topic_label(raw[int(row["key"])]) and any(
            first == int(row["key"]) and left == right == column and int(following["key"]) <= last
            for first, left, last, right in row.get("merges", [])
        ):
            return True
    labels, bold_anchor = [], ""
    for row in table:
        if row["kind"] in ("section", "header", "review"):
            bold_anchor = ""
        number = int(row["key"])
        if number not in raw:
            continue
        label = raw[number]
        merged_anchor = next(
            (
                first
                for first, left, last, right in row.get("merges", [])
                if first < last and first <= number <= last and left == right == column
            ),
            None,
        )
        if merged_anchor is not None:
            # A merged label applies only inside its source range.
            bold_anchor = ""
            label = label or raw.get(merged_anchor, "")
        elif label:
            bold_anchor = label if column in row.get("bold", []) and _topic_label(label) else ""
        else:
            label = bold_anchor
        labels.append(label)
    # Contiguous blocks of distinct topic names carry more information than
    # a low number of distinct values or repeated action/class labels alone.
    runs = [(label, sum(1 for _ in run)) for label, run in groupby(labels)]
    return (
        len(runs) >= 2
        and len({label for label, _ in runs}) == len(runs)
        and all(_topic_label(label, phrase=True) for label, _ in runs)
        and (
            all(size >= 2 for _, size in runs)
            or len(runs) >= 3
            and any(size >= 2 for _, size in runs)
        )
    )


def resolve_group_mapping(parsed, mapping):
    """Reconcile automatic suggestions with clear header and workbook evidence."""
    headers = parsed["headers"]
    start = parsed.get("region", {}).get("start_column", 1) - 1
    active = range(start, len(headers))
    question = mapping["question"]
    candidates = [index for index in active if index != question]
    suggested = mapping.get("group")
    group = next((index for index in candidates if header_role(headers[index]) == "group"), None)
    if group is None:
        possible = [
            index for index in candidates if _header_name(headers[index]) in _AMBIGUOUS_TYPE_HEADERS
        ]
        if suggested in candidates and suggested not in possible:
            possible.append(suggested)
        table, _ = classify_rows(parsed, {**mapping, "group": None}) if possible else ([], [])
        group = next(
            (index for index in possible if _supports_group_column(parsed, index, table)),
            None,
        )
    result = {**mapping, "group": group}
    for role in ("number", "status", "type"):
        if group is not None and result.get(role) == group:
            result[role] = None
    for role in ("replies", "ignored"):
        result[role] = [index for index in result.get(role, []) if index != group]
    used = {
        question,
        group,
        result.get("number"),
        result.get("status"),
        result.get("type"),
        *result["replies"],
        *result["ignored"],
    }
    displaced_type = (group is not None and group == mapping.get("type")) or (
        suggested in candidates and suggested != group and header_role(headers[suggested]) == "type"
    )
    if result.get("type") is None and (group is not None or displaced_type):
        types = [
            index
            for index in candidates
            if index not in used and header_role(headers[index]) == "type"
        ]
        result["type"] = next(
            (
                index
                for index in types
                if _header_name(headers[index]) not in _AMBIGUOUS_TYPE_HEADERS
            ),
            types[0] if displaced_type and types else None,
        )
        used.add(result["type"])
    result["extras"] = [index for index in active if index not in used]
    return result


def default_mapping(parsed):
    headers = parsed["headers"]
    active = range(parsed.get("region", {}).get("start_column", 1) - 1, len(headers))
    classified = {i: header_role(headers[i]) for i in active}
    question = next((i for i, role in classified.items() if role == "question"), 0)
    result = {
        "question": question,
        "number": None,
        "status": None,
        "type": None,
        "group": None,
        "replies": [],
        "extras": [],
        "ignored": [],
    }
    for i, role in classified.items():
        if i == question:
            continue
        if role in ("number", "status", "type", "group") and result[role] is None:
            result[role] = i
        elif role == "reply":
            result["replies"].append(i)
        else:
            result["extras"].append(i)
    return resolve_group_mapping(parsed, result)


def classify_rows(parsed, mapping, actions=None, parents=None):
    actions = actions or {}
    parents = parents or {}
    table, questions, current_group = [], [], ""
    group_key = ""
    group_fields = {}
    start = parsed.get("region", {}).get("start_column", 1) - 1
    ordinal_columns = number_header_columns(parsed["headers"])
    group_col = mapping.get("group")
    column_label = column_key = bold_group = ""
    group_values = {}
    if group_col is not None:
        if not start <= group_col < len(parsed["headers"]):
            fail("invalid_mapping", 422)
        group_values = {int(row["key"]): row["values"][group_col].strip() for row in parsed["rows"]}
    for source in parsed["rows"]:
        row = {**source}
        vals, r = row["values"], int(row["key"])
        content = vals[mapping["question"]].strip()
        nonempty = [i for i in range(start, len(vals)) if vals[i].strip()]
        reply_content = any(vals[i].strip() for i in mapping["replies"])
        semantic = section_semantic_columns(row, nonempty, mapping.get("number"), ordinal_columns)
        wide = any(a == d == r and e - b >= 2 for a, b, d, e in row.get("merges", []))
        repeated = sum(
            bool(h.strip()) and h.split(" / ")[-1].strip() == v.strip()
            for h, v in zip(parsed["headers"], vals)
        ) >= max(2, sum(bool(h) for h in parsed["headers"]) * 0.7)
        merged_previous = any(
            a < r <= d and b <= mapping["question"] + 1 <= e for a, b, d, e in row.get("merges", [])
        )
        if repeated:
            kind = "header"
        elif wide and len(nonempty) <= 2:
            kind = (
                "note"
                if not any(i + 1 in row.get("bold", []) for i in nonempty)
                or any(i + 1 in row.get("italic", []) for i in nonempty)
                or max((len(vals[i]) for i in nonempty), default=0) > 160
                else "section"
            )
        elif content:
            kind = "question"
        elif merged_previous and reply_content and questions:
            kind = "continuation"
        elif not reply_content and (
            (semantic and all(i + 1 in row.get("bold", []) for i in semantic)) or len(nonempty) == 1
        ):
            kind = "section"
        else:
            kind = "review"
        # A styled reply cell is not a group heading when its question is missing.
        if not content and reply_content and kind in ("section", "note", "header"):
            kind = "review"
        kind = actions.get(row["key"], kind)
        if kind in ("section", "header", "review", "exclude"):
            column_label = column_key = bold_group = ""
        row_group, row_group_key = current_group, group_key
        group_inherited = ""
        group_value = ""
        if group_col is not None and kind in ("question", "child"):
            label = group_values[r]
            if label:
                vertical = any(
                    a == r < d and b == e == group_col + 1 for a, b, d, e in row.get("merges", [])
                )
                bold_group = label if group_col + 1 in row.get("bold", []) and not vertical else ""
            else:
                for a, b, d, e in row.get("merges", []):
                    if a < r <= d and b == e == group_col + 1 and group_values.get(a):
                        label, group_inherited = group_values[a], "merge"
                        bold_group = ""
                        break
                if not label and bold_group:
                    label, group_inherited = bold_group, "bold"
            group_value = label
            if label and label != current_group:
                if label != column_label:
                    column_label, column_key = label, f"column:{row['key']}"
                row_group, row_group_key = column_label, column_key
            else:
                column_label = column_key = ""
        parent_key = parents.get(row["key"], "")
        indent = row.get("indents", {}).get(str(mapping["question"]), 0)
        if kind in ("question", "child") and row["key"] not in parents:
            for prior in reversed(questions):
                if prior.get("group_key") != row_group_key:
                    break
                prior_indent = prior.get("indents", {}).get(str(mapping["question"]), 0)
                if indent > prior_indent:
                    parent_key = prior["key"]
                    break
        if kind == "question" and parent_key:
            kind = "child"
        if kind == "child" and not parent_key:
            kind = "review"
        row.update(
            kind=kind, text=content, group=row_group, group_key=row_group_key, parent_key=parent_key
        )
        if group_inherited:
            row["group_inherited"] = group_inherited
        if group_value:
            row["group_value"] = group_value
        if kind == "section":
            group_key = row["key"]
            current_group = " · ".join(vals[i] for i in (semantic or nonempty))
            group_fields = {
                i: row["typed"][i]
                for i in semantic
                if i == mapping.get("type") or i in mapping.get("extras", [])
            }
        elif kind in ("question", "child"):
            effective = list(row.get("typed", [{"value": value, "kind": "s"} for value in vals]))
            for i, value in group_fields.items():
                if not vals[i]:
                    effective[i] = value
            row["effective_typed"] = effective
            row["effective_values"] = [text(c["value"]) for c in effective]
            type_col = mapping.get("type")
            if type_col is not None and vals[type_col] and type_col + 1 in row.get("bold", []):
                group_fields[type_col] = row["typed"][type_col]
            row["source_rows"] = [r]
            questions.append(row)
        elif kind == "continuation":
            if questions:
                target = questions[-1]
                target["source_rows"].append(r)
                # Only mapped question/reply text is joined; metadata keeps its own cells.
                for i in [mapping["question"], *mapping["replies"]]:
                    if vals[i]:
                        markup = target.get("formatted_values", {})
                        added = row.get("formatted_values", {})
                        if str(i) in markup or str(i) in added:
                            target["formatted_values"] = {
                                **markup,
                                str(i): "\n".join(
                                    v
                                    for v in [
                                        markup.get(str(i), target["values"][i]),
                                        added.get(str(i), vals[i]),
                                    ]
                                    if v
                                ),
                            }
                        target["values"] = list(target["values"])
                        target["values"][i] = "\n".join(
                            v for v in [target["values"][i], vals[i]] if v
                        )
                target["text"] = target["values"][mapping["question"]]
            else:
                row["kind"] = "review"
        table.append({**row, "values": source["values"]})
    validate_question_parents(questions)
    return table, questions


def validate_question_parents(questions):
    """Only question rows can own child questions, within one acyclic group tree."""
    by_key = {r["key"]: r for r in questions if r.get("kind", "question") in ("question", "child")}
    for row in questions:
        parent = row.get("parent_key")
        seen = {row["key"]}
        while parent:
            if (
                parent in seen
                or parent not in by_key
                or by_key[parent].get("group_key") != row.get("group_key")
            ):
                fail("invalid_parent", 422)
            seen.add(parent)
            parent = by_key[parent].get("parent_key")
