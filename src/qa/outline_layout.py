"""Translate ordered content into source-row relocations and styled insertions."""

from copy import deepcopy

from . import store


def arrange(layout, items, questions):
    baseline = layout.get("outline", [])
    originals = {i["id"]: i for i in baseline if i.get("source_rows")}
    # Identity corrections retain the source row binding, even on old import plans.
    source_ids = {rows[0]: qid for qid, rows in layout["rows"].items() if rows}
    for iid, item in list(originals.items()):
        if item["kind"] == "question" and source_ids.get(item["source_rows"][0], iid) != iid:
            del originals[iid]
            replacement = source_ids[item["source_rows"][0]]
            originals[replacement] = {**item, "id": replacement}
    header = layout["region"]["header_end_row"]
    end = max((max(i["source_rows"]) for i in originals.values()), default=header)
    blocks, previous = {}, header
    for item in sorted(originals.values(), key=lambda i: i["source_rows"][0]):
        start, last = min(item["source_rows"]), max(item["source_rows"])
        if start <= previous:
            store.fail("unsupported_layout_change", 422)
        blocks[item["id"]] = list(range(previous + 1, last + 1))
        previous = last
    mapping = {r: None for r in range(header + 1, end + 1)}
    insertions, positions, next_row = [], {}, header + 1
    for item in items:
        iid = item["id"]
        if item["kind"] == "question" and (iid not in questions or not questions[iid]["included"]):
            # Preserve whitespace attached to removed content.
            own = set(originals.get(iid, {}).get("source_rows", []))
            for r in blocks.get(iid, []):
                if r not in own:
                    mapping[r] = next_row
                    next_row += 1
            continue
        if iid in originals:
            for r in blocks[iid]:
                mapping[r] = next_row
                if r == originals[iid]["source_rows"][0]:
                    positions[iid] = next_row
                next_row += 1
        else:
            if item["kind"] == "layout" or (
                item["kind"] == "group" and layout["mapping"].get("group") is not None
            ):
                continue
            candidates = [i for i in originals.values() if i["kind"] == item["kind"]]
            preferred = [i for i in candidates if i.get("group_id") == item.get("group_id")]
            prototype = next(iter(preferred or candidates), None)
            positions[iid] = next_row
            insertions.append(
                {
                    "item": item,
                    "row": next_row,
                    "prototype": prototype["source_rows"][0] if prototype else None,
                }
            )
            next_row += 1
    return mapping, insertions, positions, end, next_row - end - 1


def style_cell(parts, cell, *, indent=None, note=False, group=False):
    from .workbook_layout import parse, serialize, tag

    root = parse(parts["xl/styles.xml"])
    styles, fonts = root.find(tag("cellXfs")), root.find(tag("fonts"))
    xf = deepcopy(styles[int(cell.get("s", "0"))])
    if indent is not None:
        alignment = xf.find(tag("alignment"))
        if alignment is None:
            from lxml import etree as ET

            alignment = ET.SubElement(xf, tag("alignment"))
        alignment.set("indent", str(min(250, indent)))
        alignment.set("wrapText", "1")
        xf.set("applyAlignment", "1")
    if note or group:
        from lxml import etree as ET

        font = deepcopy(fonts[int(xf.get("fontId", "0"))])
        for name, enabled in [("i", note), ("b", group)]:
            for child in list(font):
                if child.tag == tag(name):
                    font.remove(child)
            if enabled:
                ET.SubElement(font, tag(name))
        encoded = serialize(font)
        fid = next((i for i, f in enumerate(fonts) if serialize(f) == encoded), None)
        if fid is None:
            fid = len(fonts)
            fonts.append(font)
            fonts.set("count", str(len(fonts)))
        xf.set("fontId", str(fid))
        xf.set("applyFont", "1")
    encoded = serialize(xf)
    sid = next((i for i, s in enumerate(styles) if serialize(s) == encoded), None)
    if sid is None:
        sid = len(styles)
        styles.append(xf)
        styles.set("count", str(len(styles)))
    cell.set("s", str(sid))
    parts["xl/styles.xml"] = serialize(root)
