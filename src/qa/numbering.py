"""Version-local presentation numbers. Never used to resolve question identities."""

from collections import defaultdict


def _structure(items, question_ids):
    return [
        (i["id"], i.get("group_id", ""), i.get("parent_id", ""))
        for i in items
        if i["kind"] == "group" or i["kind"] == "question" and i["id"] in question_ids
    ]


def display_numbers(meta, questions, *, automatic=False):
    """Retain supplied numbering until membership/order/hierarchy changes.

    Source numbers remain untouched for provenance. Comparing the outline to its
    import baseline also covers existing lists, undo, and immutable old versions.
    Notes, labels, replies and filters never affect numbering.
    """
    active = {q["id"]: q for q in questions if q["included"]}
    items = meta.get("outline") or [{"id": qid, "kind": "question"} for qid in active]
    result, counters = {}, defaultdict(int)
    for item in items:
        iid = item["id"]
        if item["kind"] == "group":
            counters[""] += 1
            result[iid] = str(counters[""])
        elif item["kind"] == "question" and iid in active:
            ancestor = item.get("parent_id") or item.get("group_id") or ""
            ancestor = ancestor if ancestor in result else ""
            counters[ancestor] += 1
            prefix = result[ancestor] + "." if ancestor else ""
            result[iid] = prefix + str(counters[ancestor])

    layout = meta.get("layout") or {}
    column = layout.get("mapping", {}).get("number")
    if (
        not automatic
        and column is not None
        and layout.get("outline") is not None
        and _structure(items, active) == _structure(layout["outline"], layout.get("rows", {}))
        and all(str(q.get("external_number") or "").strip() for q in active.values())
    ):
        result.update({qid: str(q["external_number"]).strip() for qid, q in active.items()})
        for item in layout["outline"]:
            # Merged headings may occupy the number cell; keep their label intact.
            values = item.get("source_values", [])
            text_columns = item.get("text_columns", [item.get("text_column")])
            if item["kind"] == "group" and column not in text_columns and column < len(values):
                value = str(values[column] or "").strip()
                if value:
                    result[item["id"]] = value
    return result
