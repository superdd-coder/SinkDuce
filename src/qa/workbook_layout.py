"""Patch the imported XLSX package, preserving unrelated parts byte-for-byte.

Cell edits retain styles and rich text outside edited cells. Structural edits relocate
ordinary references, merged areas, validations and drawing anchors. Unsupported
structural dependencies stop the export explicitly instead of discarding content.
"""

import posixpath
import re
from copy import deepcopy
from io import BytesIO
from itertools import groupby, pairwise
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree as ET
from openpyxl.cell.rich_text import CellRichText
from openpyxl.formula.tokenizer import Tokenizer, TokenizerError
from openpyxl.formula.translate import Translator, TranslatorError
from openpyxl.utils.cell import (
    column_index_from_string,
    coordinate_from_string,
    get_column_letter,
    range_boundaries,
)
from openpyxl.utils.exceptions import CellCoordinatesException

from . import store
from .reply_format import rich_reply

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML = "http://www.w3.org/XML/1998/namespace"


def tag(name):
    return f"{{{NS}}}{name}"


def parse(raw):
    return ET.fromstring(raw, ET.XMLParser(resolve_entities=False, no_network=True))


def serialize(root):
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True)


def target_path(parent, target):
    return (
        target.lstrip("/")
        if target.startswith("/")
        else posixpath.normpath(posixpath.join(posixpath.dirname(parent), target))
    )


class GridMap:
    def __init__(self, rows, columns, row_after, new_rows, column_after, new_columns):
        self.rows, self.columns = set(rows), set(columns)
        self.row_after, self.new_rows = row_after, new_rows
        self.column_after, self.new_columns = column_after, new_columns

    def axis(self, value, axis):
        if axis == "row" and hasattr(self, "row_mapping"):
            if value in self.row_mapping:
                return self.row_mapping[value]
            return value + (self.row_delta if value > self.row_end else 0)
        removed, after, added = (
            (self.rows, self.row_after, self.new_rows)
            if axis == "row"
            else (self.columns, self.column_after, self.new_columns)
        )
        if value in removed:
            return None
        return value - sum(r < value for r in removed) + (added if value > after else 0)

    def point(self, address):
        col, row = coordinate_from_string(address)
        r, c = self.axis(row, "row"), self.axis(column_index_from_string(col), "column")
        return f"{get_column_letter(c)}{r}" if r and c else None

    def row_range(self, first, last):
        low, high = self.axis(first, "row"), self.axis(last, "row")
        if hasattr(self, "row_mapping") and first < last:
            mapped = [self.axis(v, "row") for v in range(first, min(last, self.row_end) + 1)]
            mapped = [v for v in mapped if v is not None]
            if last > self.row_end:
                mapped.append(self.axis(last, "row"))
            if mapped:
                low, high = min(mapped), max(mapped)
                if any(
                    new is not None and not first <= old <= last and low <= new <= high
                    for old, new in self.row_mapping.items()
                ):
                    store.fail("unsupported_layout_change", 422)
        return low, high

    def reference(self, ref, expand=False):
        # Excel also uses entire-axis references, including print titles and totals.
        whole = re.fullmatch(r"(\$?)([A-Z]{1,3}|\d+):(\$?)([A-Z]{1,3}|\d+)", ref)
        if whole and whole[2].isdigit() == whole[4].isdigit():
            first, last = whole[2], whole[4]
            axis = "row" if first.isdigit() else "column"
            convert = int if axis == "row" else column_index_from_string
            render = str if axis == "row" else get_column_letter
            lo, hi = convert(first), convert(last)
            removed = self.rows if axis == "row" else self.columns
            while lo in removed and lo <= hi:
                lo += 1
            while hi in removed and hi >= lo:
                hi -= 1
            if lo > hi:
                return "#REF!"
            mapped_lo, mapped_hi = (
                self.row_range(lo, hi)
                if axis == "row"
                else (self.axis(lo, axis), self.axis(hi, axis))
            )
            return f"{whole[1]}{render(mapped_lo)}:{whole[3]}{render(mapped_hi)}"
        match = re.fullmatch(r"(\$?)([A-Z]{1,3})(\$?)(\d+)(?::(\$?)([A-Z]{1,3})(\$?)(\d+))?", ref)
        if not match:
            return ref
        ac, c, ar, r, bc, d, br, s = match.groups()
        c, r = column_index_from_string(c), int(r)
        if d is None:
            nc, nr = self.axis(c, "column"), self.axis(r, "row")
            return f"{ac}{get_column_letter(nc)}{ar}{nr}" if nc and nr else "#REF!"
        d, s = column_index_from_string(d), int(s)
        while c in self.columns and c <= d:
            c += 1
        while d in self.columns and d >= c:
            d -= 1
        while r in self.rows and r <= s:
            r += 1
        while s in self.rows and s >= r:
            s -= 1
        if c > d or r > s:
            return "#REF!"
        nc, nd, nr, ns = (
            self.axis(c, "column"),
            self.axis(d, "column"),
            self.axis(r, "row"),
            self.axis(s, "row"),
        )
        nr, ns = self.row_range(r, s)
        if expand and c < d == self.column_after:
            nd += self.new_columns
        return f"{ac}{get_column_letter(nc)}{ar}{nr}:{bc}{get_column_letter(nd)}{br}{ns}"

    def formula(self, formula, current_sheet, changed_sheet):
        if not formula:
            return formula
        try:
            tokens = Tokenizer("=" + formula).items
        except (TokenizerError, ValueError, IndexError):
            store.fail("unsupported_layout_change", 422)
        changed = False
        for token in tokens:
            if token.type != "OPERAND" or token.subtype != "RANGE":
                continue
            value = token.value
            if "!" in value:
                qualifier, ref = value.rsplit("!", 1)
                sheet = qualifier.strip("'").replace("''", "'")
                if sheet != changed_sheet:
                    if ":" in sheet and "[" not in sheet:
                        store.fail("unsupported_layout_change", 422)
                    continue
                prefix = qualifier + "!"
            else:
                if current_sheet != changed_sheet:
                    continue
                ref, prefix = value, ""
            mapped = self.reference(ref)
            token.value = mapped if mapped == "#REF!" else prefix + mapped
            changed |= token.value != value
        return "".join(t.value for t in tokens) if changed else formula


def expand_shared_formulas(root, affected=None):
    """Resolve a shared formula at each original cell, before relocating any references."""
    groups = {}
    for node in root.iter(tag("f")):
        if node.get("t") != "shared":
            continue
        group = node.get("si", "")
        if not re.fullmatch(r"[0-9]+", group):
            store.fail("unsupported_layout_change", 422)
        groups.setdefault(group, []).append(node)

    for members in groups.values():
        masters = [node for node in members if node.text and node.text.strip()]
        if len(masters) != 1 or not masters[0].get("ref"):
            store.fail("unsupported_layout_change", 422)
        master = masters[0]
        try:
            left, top, right, bottom = range_boundaries(master.get("ref"))
            if not all((left, top, right, bottom)) or left > right or top > bottom:
                raise ValueError
            origin = master.getparent().get("r")
            translator = Translator("=" + master.text, origin=origin)
            formulas, seen = [], set()
            for node in members:
                address = node.getparent().get("r")
                column, row = coordinate_from_string(address)
                column = column_index_from_string(column)
                if (
                    (column, row) in seen
                    or not (left <= column <= right and top <= row <= bottom)
                    or node is not master
                    and node.get("ref") is not None
                ):
                    raise ValueError
                seen.add((column, row))
                formula = (
                    master.text if node is master else translator.translate_formula(address)[1:]
                )
                if not formula:
                    raise ValueError
                formulas.append((node, formula))
        except (
            TypeError,
            ValueError,
            IndexError,
            TokenizerError,
            TranslatorError,
            CellCoordinatesException,
        ):
            store.fail("unsupported_layout_change", 422)
        if affected is not None and not any(affected(formula) for _, formula in formulas):
            continue
        for node, formula in formulas:
            for attr in ("t", "si", "ref"):
                node.attrib.pop(attr, None)
            node.text = formula


def set_cell(rows, address, value, prototype=None, fonts=None):
    _, r = coordinate_from_string(address)
    row = rows.get(r)
    if row is None:
        row = ET.Element(tag("row"), r=str(r))
        rows[r] = row
    cell = next((c for c in row.findall(tag("c")) if c.get("r") == address), None)
    if cell is None:
        cell = ET.SubElement(row, tag("c"), r=address)
        if prototype is not None and prototype.get("s"):
            cell.set("s", prototype.get("s"))
    for child in list(cell):
        if child.tag in (tag("v"), tag("f"), tag("is")):
            cell.remove(child)
    cell.attrib.pop("t", None)
    if value is None or value == "":
        return
    if isinstance(value, CellRichText):
        cell.set("t", "inlineStr")
        content = value.to_tree()
        for node in content.iter():
            node.tag = tag(node.tag)
        if fonts:
            base_font = fonts.get(cell.get("s", "0"))
            if base_font is not None:
                for properties in content.iter(tag("rPr")):
                    for attribute in base_font:
                        # The new reply owns emphasis; all other font styling comes
                        # from the source cell (typeface, size, color, scheme, etc.).
                        if attribute.tag in (tag("b"), tag("i")):
                            continue
                        copied = deepcopy(attribute)
                        if copied.tag == tag("name"):
                            copied.tag = tag("rFont")
                        properties.append(copied)
        cell.append(content)
    elif isinstance(value, bool):
        cell.set("t", "b")
        ET.SubElement(cell, tag("v")).text = "1" if value else "0"
    elif isinstance(value, (int, float)):
        ET.SubElement(cell, tag("v")).text = str(value)
    else:
        cell.set("t", "inlineStr")
        content = ET.SubElement(ET.SubElement(cell, tag("is")), tag("t"))
        content.set(f"{{{XML}}}space", "preserve")
        content.text = str(value)
    return cell


def update_option_validations(parts, path, root, workbook, rels, meta, grid, question_rows, added):
    """Update only question-cell choices; leave unrelated validations and source lists intact."""
    layout = meta["layout"]
    targets = {}
    for key, field in [("status", "statuses"), ("type", "types")]:
        index = layout["mapping"].get(key)
        labels = [o["label"] for o in meta["options"][field]]
        before = [o["label"] for o in layout.get("options", {}).get(field, [])]
        if index is not None and (labels != before or added) and question_rows:
            col = grid.axis(index + 1, "column")
            if col:
                targets[col] = labels
    if not targets:
        return
    validations = root.find(tag("dataValidations"))
    if validations is None:
        validations = ET.Element(tag("dataValidations"))
        following = {
            "hyperlinks",
            "printOptions",
            "pageMargins",
            "pageSetup",
            "headerFooter",
            "rowBreaks",
            "colBreaks",
            "customProperties",
            "cellWatches",
            "ignoredErrors",
            "smartTags",
            "drawing",
            "legacyDrawing",
            "legacyDrawingHF",
            "picture",
            "oleObjects",
            "controls",
            "webPublishItems",
            "tableParts",
            "extLst",
        }
        position = next(
            (i for i, node in enumerate(root) if ET.QName(node).localname in following), len(root)
        )
        root.insert(position, validations)
    affected_rows = set(question_rows.values())
    for validation in list(validations):
        remaining = []
        for ref in validation.get("sqref", "").split():
            a, b, c, d = range_boundaries(ref)
            regions = [(a, b, c, d)]
            for col in targets:
                next_regions = []
                for left, top, right, bottom in regions:
                    hits = sorted(r for r in affected_rows if top <= r <= bottom)
                    if not left <= col <= right or not hits:
                        next_regions.append((left, top, right, bottom))
                        continue
                    if left < col:
                        next_regions.append((left, top, col - 1, bottom))
                    if col < right:
                        next_regions.append((col + 1, top, right, bottom))
                    cursor = top
                    for row in hits:
                        if cursor < row:
                            next_regions.append((col, cursor, col, row - 1))
                        cursor = row + 1
                    if cursor <= bottom:
                        next_regions.append((col, cursor, col, bottom))
                regions = next_regions
            remaining.extend(
                f"{get_column_letter(a)}{b}:{get_column_letter(c)}{d}" for a, b, c, d in regions
            )
        if remaining:
            validation.set("sqref", " ".join(remaining))
        else:
            validations.remove(validation)

    helper = None
    for col, labels in targets.items():
        if not labels:
            continue
        joined = ",".join(labels)
        if len(joined) <= 253 and all(not any(c in label for c in ',"\n\r') for label in labels):
            formula = f'"{joined}"'
        else:
            # Longer choices and punctuation need a named range, not a truncated inline list.
            if helper is None:
                sheets = workbook.find(tag("sheets"))
                names = {s.get("name") for s in sheets}
                number = 1
                while (
                    f"_QA options {number}" in names
                    or f"xl/worksheets/qa-options-{number}.xml" in parts
                ):
                    number += 1
                sheet_name = f"_QA options {number}"
                resource = f"xl/worksheets/qa-options-{number}.xml"
                rel_id = f"qaOptions{number}"
                while any(r.get("Id") == rel_id for r in rels):
                    rel_id += "x"
                ET.SubElement(
                    rels,
                    "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship",
                    Id=rel_id,
                    Type=REL + "/worksheet",
                    Target=resource[3:],
                )
                sheet = ET.SubElement(
                    sheets,
                    tag("sheet"),
                    name=sheet_name,
                    state="hidden",
                    sheetId=str(max(int(s.get("sheetId")) for s in sheets) + 1),
                )
                sheet.set(f"{{{REL}}}id", rel_id)
                types = parse(parts["[Content_Types].xml"])
                ET.SubElement(
                    types,
                    "{http://schemas.openxmlformats.org/package/2006/content-types}Override",
                    PartName="/" + resource,
                    ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
                )
                parts["[Content_Types].xml"] = serialize(types)
                helper = (resource, sheet_name, {})
            resource, sheet_name, cells = helper
            for r, value in enumerate(labels, 1):
                set_cell(cells, f"{get_column_letter(col)}{r}", value)
            names = workbook.find(tag("definedNames"))
            if names is None:
                names = ET.Element(tag("definedNames"))
                calc = workbook.find(tag("calcPr"))
                workbook.insert(
                    list(workbook).index(calc) if calc is not None else len(workbook), names
                )
            formula = f"_qa_choices_{col}"
            while any(n.get("name") == formula for n in names):
                formula += "_"
            ET.SubElement(
                names, tag("definedName"), name=formula
            ).text = f"'{sheet_name}'!${get_column_letter(col)}$1:${get_column_letter(col)}${len(labels)}"
        node = ET.SubElement(
            validations,
            tag("dataValidation"),
            type="list",
            allowBlank="1",
            showErrorMessage="1",
            sqref=" ".join(f"{get_column_letter(col)}{r}" for r in sorted(affected_rows)),
        )
        ET.SubElement(node, tag("formula1")).text = formula
    if helper:
        resource, _, cells = helper
        sheet_root = ET.Element(tag("worksheet"), nsmap={None: NS})
        data = ET.SubElement(sheet_root, tag("sheetData"))
        for r in sorted(cells):
            cells[r][:] = sorted(
                cells[r],
                key=lambda cell: column_index_from_string(coordinate_from_string(cell.get("r"))[0]),
            )
            data.append(cells[r])
        parts[resource] = serialize(sheet_root)
    validations.set("count", str(len(validations)))


def tables_before_column(parts, sheet_path, table_parts, column_after):
    """Allow an append beside native tables without editing their package parts."""
    relationship_path = posixpath.join(
        posixpath.dirname(sheet_path), "_rels", posixpath.basename(sheet_path) + ".rels"
    )
    try:
        relationships = parse(parts[relationship_path])
        targets = {
            rel.get("Id"): target_path(sheet_path, rel.get("Target"))
            for rel in relationships
            if rel.get("Type") == REL + "/table" and rel.get("TargetMode") != "External"
        }
        if not len(table_parts):
            return False
        for entry in table_parts:
            table = parse(parts[targets[entry.get(f"{{{REL}}}id")]])
            bounds = range_boundaries(table.get("ref"))
            if any(value is None for value in bounds) or bounds[2] > column_after:
                return False
    except (KeyError, TypeError, ValueError, ET.XMLSyntaxError):
        return False
    return True


def remap_filter_columns(auto_filter, grid, *, expand):
    if auto_filter is None or not auto_filter.get("ref"):
        return
    filters = auto_filter.findall(tag("filterColumn"))
    if not filters:
        return
    before = auto_filter.get("ref")
    after = grid.reference(before, expand=expand)
    if after == "#REF!":
        return
    original_left = range_boundaries(before)[0]
    left, _, right, _ = range_boundaries(after)
    for entry in filters:
        col = grid.axis(original_left + int(entry.get("colId")), "column")
        if col is None or not left <= col <= right:
            auto_filter.remove(entry)
        else:
            entry.set("colId", str(col - left))


def write_group_column(rows, originals, root, meta, grid, question_rows, row_changes, prototype_r):
    """Write changed column-group labels without materializing inherited source blanks."""
    layout = meta["layout"]
    index = layout["mapping"].get("group")
    if index is None:
        return
    source_col = index + 1
    target_col = grid.axis(source_col, "column")
    if target_col is None:
        return
    before = {item["id"]: item for item in layout.get("outline", [])}
    after = {item["id"]: item for item in meta.get("outline", [])}
    source_questions = {
        item["source_rows"][0]: item
        for item in before.values()
        if item["kind"] == "question" and item.get("source_rows")
    }

    def column_label(items, gid, explicit_alias=False):
        # Source-bound headings already express the group; an empty cell falls back to them.
        if before.get(gid, {}).get("source_rows") and not explicit_alias:
            return ""
        return items.get(gid, {}).get("text", "")

    inheritance = layout.get("group_inheritance", {})
    sparse_bold = "bold" in inheritance.values()
    desired, edits, source_order = {}, {}, {}
    for qid, row in question_rows.items():
        source_r = (layout["rows"].get(qid) or [None])[0]
        source_order[row] = source_r
        old_group = source_questions.get(source_r, {}).get("group_id", "")
        new_group = after.get(qid, {}).get("group_id", "")
        if sparse_bold and old_group != new_group:
            store.fail("unsupported_layout_change", 422)
        inherited = inheritance.get(str(source_r), "")
        raw = layout["baseline"].get(qid, [])
        explicit_alias = bool(inherited or index < len(raw) and str(raw[index] or "").strip())
        desired[row] = value = column_label(
            after, new_group, explicit_alias and old_group == new_group
        )
        if value != column_label(before, old_group, explicit_alias) and inherited != "bold":
            edits[row] = value

    if sparse_bold and row_changes:
        # Move intact column runs only. Sparse blanks must stay behind their own anchor.
        order = [source_order[row] for row in sorted(source_order)]
        if (
            not all(desired.values())
            or set(order) != set(source_questions)
            or any(item["kind"] == "group" and item.get("source_rows") for item in before.values())
        ):
            store.fail("unsupported_layout_change", 422)

        def group_of(row):
            return source_questions[row].get("group_id", "")

        original_runs = {tuple(run) for _, run in groupby(sorted(source_questions), key=group_of)}
        projected_runs = {tuple(run) for _, run in groupby(order, key=group_of)}
        run_labels = [
            next(labels)[1]
            for _, labels in groupby(
                sorted(desired.items()), key=lambda item: group_of(source_order[item[0]])
            )
        ]
        if original_runs != projected_runs or any(a == b for a, b in pairwise(run_labels)):
            store.fail("unsupported_layout_change", 422)

    for merge in root.iter(tag("mergeCell")):
        ref = merge.get("ref")
        left, first, right, last = range_boundaries(ref)
        if not left <= source_col <= right or last <= layout["region"]["header_end_row"]:
            continue
        mapped = grid.reference(ref, expand=True)
        if mapped == "#REF!":
            continue
        _, start, _, end = range_boundaries(mapped)
        members = [row for row in desired if start <= row <= end]
        if not members:
            continue
        vertical = left == right == source_col and first < last
        labels = {desired[row] for row in members}
        if vertical and (len(labels) != 1 or grid.axis(first, "row") != start):
            store.fail("unsupported_layout_change", 422)
        if any(row in edits for row in members):
            if not vertical or first not in source_questions:
                store.fail("unsupported_layout_change", 422)
            for row in members:
                edits.pop(row, None)
            edits[start] = labels.pop()

    prototype = originals.get(f"{get_column_letter(source_col)}{prototype_r}")
    for row, value in edits.items():
        set_cell(rows, f"{get_column_letter(target_col)}{row}", value, prototype)


def write_original(blob, snapshot, meta):
    layout = meta["layout"]
    with ZipFile(BytesIO(blob)) as source:
        parts = {name: source.read(name) for name in source.namelist()}
    fonts = {}
    if "xl/styles.xml" in parts:
        styles = parse(parts["xl/styles.xml"])
        source_fonts, cell_styles = styles.find(tag("fonts")), styles.find(tag("cellXfs"))
        if source_fonts is not None and cell_styles is not None:
            fonts = {
                str(i): source_fonts[int(style.get("fontId", "0"))]
                for i, style in enumerate(cell_styles)
                if int(style.get("fontId", "0")) < len(source_fonts)
            }
    workbook = parse(parts["xl/workbook.xml"])
    rels = parse(parts["xl/_rels/workbook.xml.rels"])
    targets = {r.get("Id"): target_path("xl/workbook.xml", r.get("Target")) for r in rels}
    sheets = {s.get("name"): targets[s.get(f"{{{REL}}}id")] for s in workbook.find(tag("sheets"))}
    sheet_name = layout["sheet"]
    if sheet_name not in sheets:
        store.fail("source_unavailable", 404)
    path = sheets[sheet_name]
    root = parse(parts[path])
    questions = {q["id"]: q for q in snapshot["questions"]}
    columns = {c["id"]: c for c in snapshot["columns"]}
    source_rows, source_cols = layout["rows"], layout["columns"]
    deleted_rows = [
        r
        for qid, rows in source_rows.items()
        if qid not in questions or not questions[qid]["included"]
        for r in rows
    ]
    deleted_cols = [
        index + 1
        for cid, index in source_cols.items()
        if cid not in columns or not columns[cid]["included"]
    ]
    added_questions = [
        q for q in snapshot["questions"] if q["included"] and q["id"] not in source_rows
    ]
    added_columns = [c for c in snapshot["columns"] if c["included"] and c["id"] not in source_cols]
    row_after = max(
        (r for rows in source_rows.values() for r in rows),
        default=layout["region"]["header_end_row"],
    )
    prototype_col = max(
        (i + 1 for i in source_cols.values()), default=layout["mapping"]["question"] + 1
    )
    table_parts = root.find(tag("tableParts"))
    # A native table still uses the safe append path that leaves its XML intact.
    col_after = (
        max(layout["region"]["end_column"], prototype_col)
        if table_parts is not None
        else max(
            (
                index + 1
                for cid, index in source_cols.items()
                if cid in columns and columns[cid]["included"]
            ),
            default=layout["mapping"]["question"] + 1,
        )
    )
    grid = GridMap(
        deleted_rows, deleted_cols, row_after, len(added_questions), col_after, len(added_columns)
    )
    arrangement = None
    if layout.get("outline") is not None:
        from .outline_layout import arrange

        arrangement = arrange(layout, meta["outline"], questions)
        grid.row_mapping, insertions, item_positions, grid.row_end, grid.row_delta = arrangement
        deleted_rows = [r for r, mapped in grid.row_mapping.items() if mapped is None]
        grid.rows = set(deleted_rows)
    row_changes = bool(deleted_rows or added_questions)
    if arrangement:
        row_changes |= bool(insertions) or any(
            r != mapped for r, mapped in grid.row_mapping.items()
        )
    structural = bool(row_changes or deleted_cols or added_columns)
    table_append = (
        table_parts is not None
        and bool(added_columns)
        and not (row_changes or deleted_cols)
        and all(
            grid.reference(merge.get("ref"), expand=True) == merge.get("ref")
            for merge in root.iter(tag("mergeCell"))
        )
        and tables_before_column(parts, path, table_parts, col_after)
    )
    if structural and (
        table_parts is not None
        and not table_append
        or any(c.tag in (tag("pivotTableParts"), tag("extLst")) for c in root)
        or any(f.get("t") in ("array", "dataTable") for f in root.iter(tag("f")))
    ):
        store.fail("unsupported_layout_change", 422)
    if structural:
        expand_shared_formulas(root)
    sheet_data = root.find(tag("sheetData"))
    originals = {c.get("r"): deepcopy(c) for c in sheet_data.iter(tag("c"))}
    for address, cell in originals.items():
        col, row = coordinate_from_string(address)
        col = column_index_from_string(col)
        has_content = any(cell.find(tag(kind)) is not None for kind in ("v", "is", "f"))
        outside_columns = (
            not layout["region"]["start_column"] <= col <= layout["region"]["end_column"]
        )
        outside_rows = not layout["header_row"] <= row <= layout["region"]["end_row"]
        if has_content and (
            (row in deleted_rows or arrangement and grid.axis(row, "row") != row)
            and outside_columns
            or col in deleted_cols
            and outside_rows
        ):
            store.fail("unsupported_layout_change", 422)

    original_rows = {int(r.get("r")): deepcopy(r) for r in sheet_data}
    rows = {}
    for row in list(sheet_data):
        original_r = int(row.get("r"))
        r = grid.axis(original_r, "row")
        if r is None:
            continue
        row.set("r", str(r))
        row.attrib.pop("spans", None)
        for cell in list(row):
            mapped = grid.point(cell.get("r"))
            if mapped is None:
                row.remove(cell)
            else:
                cell.set("r", mapped)
        rows[r] = row
    new_row_start = row_after - sum(r <= row_after for r in deleted_rows) + 1
    question_rows = {
        qid: grid.axis(rr[0], "row")
        for qid, rr in source_rows.items()
        if qid in questions and questions[qid]["included"]
    }
    prototype_r = next(
        (r for rr in source_rows.values() for r in rr if r not in deleted_rows), row_after
    )
    added_items = (
        insertions
        if arrangement
        else [
            {
                "item": {"id": q["id"], "kind": "question"},
                "row": new_row_start + offset,
                "prototype": prototype_r,
            }
            for offset, q in enumerate(added_questions)
        ]
    )
    for entry in added_items:
        item, r = entry["item"], entry["row"]
        item_prototype = entry["prototype"] or prototype_r
        prototype = original_rows.get(item_prototype)
        attrs = (
            {k: v for k, v in prototype.attrib.items() if k not in ("r", "spans")}
            if prototype is not None
            else {}
        )
        rows[r] = ET.Element(tag("row"), r=str(r), **attrs)
        if item["kind"] == "question":
            question_rows[item["id"]] = r
        for index in range(layout["region"]["start_column"], layout["region"]["end_column"] + 1):
            col = grid.axis(index, "column")
            if col:
                set_cell(
                    rows,
                    f"{get_column_letter(col)}{r}",
                    None,
                    originals.get(f"{get_column_letter(index)}{item_prototype}"),
                )
    new_col_start = col_after - sum(c <= col_after for c in deleted_cols) + 1
    column_positions = {
        cid: grid.axis(i + 1, "column")
        for cid, i in source_cols.items()
        if cid in columns and columns[cid]["included"]
    }
    for offset, c in enumerate(added_columns):
        column_positions[c["id"]] = new_col_start + offset
    header_r = grid.axis(layout["region"]["header_end_row"], "row")
    for cid, col in column_positions.items():
        source_index = source_cols.get(cid)
        address = f"{get_column_letter(col)}{header_r}"
        if source_index is None or columns[cid]["label"]:
            # Existing multi-row header bands remain intact if the label is unchanged.
            old = (
                source_index is not None
                and layout.get("header_labels", {}).get(cid) == columns[cid]["label"]
            )
            if not old:
                set_cell(
                    rows,
                    address,
                    columns[cid]["label"],
                    originals.get(
                        f"{get_column_letter(prototype_col)}{layout['region']['header_end_row']}"
                    ),
                )

    def label(field, oid):
        return next((o["label"] for o in meta["options"][field] if o["id"] == oid), "")

    for qid, r in question_rows.items():
        q = questions[qid]
        baseline = layout["baseline"].get(qid)
        updates = {layout["mapping"]["question"]: q["text"]}
        typed_baseline = layout.get("typed_baseline", {}).get(qid, [])
        # Inherited source values may be unchanged while the actual source cell is blank.
        typed_baseline = layout.get("effective_typed_baseline", {}).get(qid, typed_baseline)
        for fid, index in layout.get("fields", {}).items():
            entry = meta["values"].get(qid, {}).get(fid)
            col = grid.axis(index + 1, "column")
            if entry is None or col is None:
                continue
            if baseline is not None:
                if index < len(typed_baseline):
                    unchanged = entry == typed_baseline[index]
                else:
                    # Legacy bindings lack original kinds; preserve equal displayed values.
                    before = baseline[index] if index < len(baseline) else ""
                    unchanged = str(entry["value"] if entry["value"] is not None else "") == str(
                        before if before is not None else ""
                    )
                if unchanged:
                    continue
            cell = set_cell(
                rows,
                f"{get_column_letter(col)}{r}",
                entry["value"],
                originals.get(f"{get_column_letter(index + 1)}{prototype_r}"),
            )
            if entry.get("kind") == "date" and entry["value"] not in (None, ""):
                cell[:] = []
                cell.set("t", "d")
                ET.SubElement(cell, tag("v")).text = entry["value"]
        for key, value in [
            ("number", q.get("display_number", q["external_number"])),
            ("status", label("statuses", q["status"])),
            ("type", label("types", q["type"])),
        ]:
            index = layout["mapping"].get(key)
            if index is not None:
                updates[index] = value
        reply_values = {reply["column_id"]: reply["body"] for reply in q["replies"]}
        for cid, col in column_positions.items():
            index = source_cols.get(cid)
            body = reply_values.get(cid, "")
            if index is None:
                set_cell(
                    rows,
                    f"{get_column_letter(col)}{r}",
                    rich_reply(body),
                    originals.get(f"{get_column_letter(prototype_col)}{prototype_r}"),
                    fonts=fonts,
                )
            else:
                updates[index] = body
        for index, value in updates.items():
            c = grid.axis(index + 1, "column")
            original_value = (
                layout.get("option_baseline", {})
                .get(qid, {})
                .get(str(index), baseline[index] if baseline else None)
            )
            is_reply = index in source_cols.values()
            formatted = rich_reply(str(value)) if is_reply else value
            imported_body = layout.get("reply_baseline", {}).get(qid, {}).get(str(index))
            unchanged = str(value) == (
                imported_body if imported_body is not None else original_value
            )
            # Preserve original native runs untouched; convert old literal Markdown
            # even when its saved body has not otherwise been edited.
            needs_format = is_reply and imported_body is None and formatted != value
            if c is None or baseline is not None and unchanged and not needs_format:
                continue
            prototype = originals.get(f"{get_column_letter(index + 1)}{prototype_r}")
            set_cell(rows, f"{get_column_letter(c)}{r}", formatted, prototype, fonts=fonts)
            # Joined continuation text is written once after editing, without duplication.
            for old_r in source_rows.get(qid, [])[1:]:
                nr = grid.axis(old_r, "row")
                if nr:
                    set_cell(rows, f"{get_column_letter(c)}{nr}", None)
    write_group_column(rows, originals, root, meta, grid, question_rows, row_changes, prototype_r)
    if arrangement:
        from .outline_layout import style_cell

        baseline_items = {i["id"]: i for i in layout["outline"]}
        outline_items = {i["id"]: i for i in meta["outline"]}
        numbers = {i["id"]: i.get("display_number", "") for i in snapshot.get("outline", [])}

        def label_index(item):
            prototype = next((i for i in layout["outline"] if i["kind"] == item["kind"]), {})
            return item.get(
                "text_column", prototype.get("text_column", layout["region"]["start_column"] - 1)
            )

        for iid, r in item_positions.items():
            item = outline_items[iid]
            if item["kind"] in ("group", "note"):
                base = baseline_items.get(iid)
                index = label_index(item)
                col = grid.axis(index + 1, "column")
                if col is None:
                    continue
                if base is None or base.get("text") != item.get("text"):
                    for c in (
                        [i + 1 for i in base.get("text_columns", [index])]
                        if base
                        else range(
                            layout["region"]["start_column"], layout["region"]["end_column"] + 1
                        )
                    ):
                        mapped = grid.axis(c, "column")
                        if mapped:
                            set_cell(rows, f"{get_column_letter(mapped)}{r}", None)
                    set_cell(rows, f"{get_column_letter(col)}{r}", item["text"])
                number_index = layout["mapping"].get("number")
                if (
                    item["kind"] == "group"
                    and number_index is not None
                    and number_index not in item.get("text_columns", [index])
                ):
                    number_col = grid.axis(number_index + 1, "column")
                    original_values = base.get("source_values", []) if base else []
                    original_number = (
                        str(original_values[number_index])
                        if number_index < len(original_values)
                        else ""
                    )
                    if number_col and numbers.get(iid) and numbers[iid] != original_number:
                        set_cell(rows, f"{get_column_letter(number_col)}{r}", numbers[iid])
                cell = next(
                    (c for c in rows[r] if c.get("r") == f"{get_column_letter(col)}{r}"), None
                )
                if cell is not None and base is None:
                    style_cell(
                        parts, cell, note=item["kind"] == "note", group=item["kind"] == "group"
                    )
            elif item["kind"] == "question":
                depth, parent = 0, item.get("parent_id")
                while parent:
                    depth += 1
                    parent = outline_items.get(parent, {}).get("parent_id")
                col = grid.axis(layout["mapping"]["question"] + 1, "column")
                cell = next(
                    (c for c in rows[r] if c.get("r") == f"{get_column_letter(col)}{r}"), None
                )
                if cell is not None and (depth or baseline_items.get(iid, {}).get("parent_id")):
                    style_cell(parts, cell, indent=depth)
    sheet_data[:] = []
    for r in sorted(rows):
        row = rows[r]
        row[:] = sorted(
            row, key=lambda c: column_index_from_string(coordinate_from_string(c.get("r"))[0])
        )
        sheet_data.append(row)
    if structural:
        if deleted_cols or added_columns:
            remap_filter_columns(root.find(tag("autoFilter")), grid, expand=not table_append)
        # Content references move with rows; viewport coordinates stay on the grid.
        for node in list(root.iter()):
            if node.tag == tag("sheetViews") or any(
                ancestor.tag == tag("sheetViews") for ancestor in node.iterancestors()
            ):
                continue
            for attr in ("ref", "sqref", "topLeftCell", "activeCell"):
                if attr not in node.attrib or node.tag == tag("c"):
                    continue
                expand = not (table_append and node.tag == tag("autoFilter"))
                refs = [grid.reference(ref, expand=expand) for ref in node.get(attr).split()]
                refs = [ref for ref in refs if ref != "#REF!"]
                if refs:
                    node.set(attr, " ".join(refs))
                elif node.getparent() is not None:
                    node.getparent().remove(node)
            if node.tag == tag("mergeCells"):
                node.set("count", str(len(node)))
        if arrangement:
            merged = root.find(tag("mergeCells"))
            for entry in insertions:
                if entry["item"]["kind"] not in ("group", "note"):
                    continue
                if merged is None:
                    merged = ET.SubElement(root, tag("mergeCells"))
                first = grid.axis(layout["region"]["start_column"], "column")
                last = grid.axis(layout["region"]["end_column"], "column")
                if entry["item"]["kind"] == "group" and layout["mapping"].get("number") is not None:
                    label_col = label_index(entry["item"]) + 1
                    number_col = layout["mapping"]["number"] + 1
                    if label_col != number_col:
                        first = grid.axis(label_col, "column")
                        if number_col > label_col:
                            last = grid.axis(number_col - 1, "column")
                if first and last and last > first:
                    ET.SubElement(
                        merged,
                        tag("mergeCell"),
                        ref=f"{get_column_letter(first)}{entry['row']}:{get_column_letter(last)}{entry['row']}",
                    )
                merged.set("count", str(len(merged)))
        dims = root.find(tag("cols"))
        if dims is None and added_columns:
            dims = ET.Element(tag("cols"))
            root.insert(list(root).index(sheet_data), dims)
        if dims is not None:
            new_dims = []
            template = None
            for col in dims:
                lo, hi = int(col.get("min")), int(col.get("max"))
                if lo <= prototype_col <= hi:
                    template = deepcopy(col)
                # Split broad column-dimension records around insertions/deletions.
                cuts = sorted(
                    {
                        lo,
                        hi + 1,
                        *[v for c in deleted_cols for v in (c, c + 1) if lo <= v <= hi + 1],
                        *([col_after + 1] if lo <= col_after < hi else []),
                    }
                )
                for a, b in pairwise(cuts):
                    if a in deleted_cols:
                        continue
                    item = deepcopy(col)
                    item.set("min", str(grid.axis(a, "column")))
                    item.set("max", str(grid.axis(b - 1, "column")))
                    new_dims.append(item)
            if added_columns:
                item = (
                    template
                    if template is not None
                    else ET.Element(tag("col"), width="48", customWidth="1")
                )
                item.attrib.pop("hidden", None)
                item.set("min", str(new_col_start))
                item.set("max", str(new_col_start + len(added_columns) - 1))
                new_dims.append(item)
            dims[:] = sorted(new_dims, key=lambda c: int(c.get("min")))
        # Shapes/images have zero-based anchors in related drawing parts.
        relation_path = posixpath.join(
            posixpath.dirname(path), "_rels", posixpath.basename(path) + ".rels"
        )
        if relation_path in parts:
            relations = parse(parts[relation_path])
            for rel in relations:
                if rel.get("TargetMode") == "External":
                    continue
                resource = target_path(path, rel.get("Target"))
                kind = rel.get("Type", "").rsplit("/", 1)[-1]
                if kind in ("vmlDrawing", "threadedComment", "pivotTable"):
                    store.fail("unsupported_layout_change", 422)
                if kind == "drawing" and resource in parts:
                    drawing = parse(parts[resource])
                    for anchor in drawing.iter():
                        local = ET.QName(anchor).localname
                        if local in ("row", "col") and anchor.text and anchor.text.isdigit():
                            axis = "row" if local == "row" else "column"
                            old = int(anchor.text) + 1
                            mapped = grid.axis(old, axis)
                            while mapped is None:
                                old += 1
                                mapped = grid.axis(old, axis)
                            anchor.text = str(mapped - 1)
                    parts[resource] = serialize(drawing)
                elif kind == "comments" and resource in parts:
                    comments = parse(parts[resource])
                    for comment in list(comments.iter(tag("comment"))):
                        ref = grid.reference(comment.get("ref"))
                        if ref == "#REF!":
                            comment.getparent().remove(comment)
                        else:
                            comment.set("ref", ref)
                    parts[resource] = serialize(comments)
    parts[path] = serialize(root)
    # Transform formulas only on structural changes. Other sheets remain byte-identical otherwise.
    if structural:
        for name, sheet_path in sheets.items():
            sheet_root = root if name == sheet_name else parse(parts[sheet_path])
            changed = name == sheet_name
            if not changed:
                expand_shared_formulas(
                    sheet_root,
                    lambda formula, name=name: grid.formula(formula, name, sheet_name) != formula,
                )
            # The cached <v> sibling can be the XML iterator's next node.
            # Snapshot traversal so removing it does not skip later formulas.
            for node in list(sheet_root.iter()):
                if (
                    ET.QName(node).localname not in ("f", "formula", "formula1", "formula2")
                    or not node.text
                ):
                    continue
                value = grid.formula(node.text, name, sheet_name)
                if value != node.text:
                    if node.tag == tag("f") and node.get("t") in ("array", "dataTable"):
                        store.fail("unsupported_layout_change", 422)
                    node.text = value
                    changed = True
                    if node.tag == tag("f"):
                        cache = node.getparent().find(tag("v"))
                        if cache is not None:
                            node.getparent().remove(cache)
            if changed:
                parts[sheet_path] = serialize(sheet_root)
        for defined in workbook.iter(tag("definedName")):
            local = (
                list(sheets)[int(defined.get("localSheetId"))]
                if defined.get("localSheetId")
                else None
            )
            defined.text = grid.formula(defined.text, local, sheet_name)
        for name, raw in list(parts.items()):
            if name.startswith("xl/charts/") and name.endswith(".xml"):
                chart = parse(raw)
                changed = False
                for node in chart.iter():
                    if ET.QName(node).localname == "f" and node.text:
                        value = grid.formula(node.text, None, sheet_name)
                        if value != node.text:
                            node.text = value
                            changed = True
                if changed:
                    parts[name] = serialize(chart)
    update_option_validations(
        parts, path, root, workbook, rels, meta, grid, question_rows, bool(added_questions)
    )
    # A single-column source range cannot expand through GridMap's range rules.
    # Keep its existing extent while including all cells and merged areas now written.
    dimension = root.find(tag("dimension"))
    references = [c.get("r") for c in root.iter(tag("c"))]
    references.extend(m.get("ref") for m in root.iter(tag("mergeCell")))
    if dimension is not None:
        references.append(dimension.get("ref"))
    bounds = [range_boundaries(ref) for ref in references if ref]
    if dimension is None:
        dimension = ET.Element(tag("dimension"))
        root.insert(1 if root.find(tag("sheetPr")) is not None else 0, dimension)
    if bounds:
        left, top = min(b[0] for b in bounds), min(b[1] for b in bounds)
        right, bottom = max(b[2] for b in bounds), max(b[3] for b in bounds)
        dimension.set("ref", f"{get_column_letter(left)}{top}:{get_column_letter(right)}{bottom}")
    else:
        dimension.set("ref", "A1")
    parts[path] = serialize(root)
    parts["xl/_rels/workbook.xml.rels"] = serialize(rels)
    # Recalculate dependent formulas on open after either text or structural edits.
    calc = workbook.find(tag("calcPr"))
    if calc is None:
        calc = ET.SubElement(workbook, tag("calcPr"))
    calc.set("fullCalcOnLoad", "1")
    calc.set("forceFullCalc", "1")
    parts["xl/workbook.xml"] = serialize(workbook)
    if "xl/calcChain.xml" in parts:
        del parts["xl/calcChain.xml"]
        for rel in list(rels):
            if rel.get("Type", "").endswith("/calcChain"):
                rels.remove(rel)
        parts["xl/_rels/workbook.xml.rels"] = serialize(rels)
        types = parse(parts["[Content_Types].xml"])
        for entry in list(types):
            if entry.get("PartName") == "/xl/calcChain.xml":
                types.remove(entry)
        parts["[Content_Types].xml"] = serialize(types)
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as output:
        for name, value in parts.items():
            output.writestr(name, value)
    return out.getvalue()
