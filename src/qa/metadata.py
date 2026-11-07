"""Per-version list vocabulary, additional fields and original-workbook bindings."""

import json
from copy import deepcopy

from . import store


def defaults():
    return {
        "options": {
            "statuses": [
                {"id": key, "label": label, "color": color}
                for key, label, color in [
                    ("waiting", "Waiting for reply", "#b58c42"),
                    ("review", "To review", "#6486ad"),
                    ("closed", "Closed", "#397b60"),
                ]
            ],
            "types": [
                {"id": key, "label": key, "color": "#397b60"}
                for key in ["Clarify", "Provide", "Confirm", "Revise", "Other"]
            ],
        },
        "fields": [],
        "values": {},
        "groups": {},
        "layout": None,
    }


def read(conn, vid):
    row = conn.execute(
        "SELECT data_json FROM qa_version_metadata WHERE version_id=?", (vid,)
    ).fetchone()
    result = json.loads(row[0]) if row else defaults()
    from .outline import ensure

    ensure(conn, vid, result)
    return result


def save(conn, vid, data):
    conn.execute(
        "INSERT OR REPLACE INTO qa_version_metadata VALUES(?,?)",
        (vid, json.dumps(data, ensure_ascii=False)),
    )


def public(data, qid):
    return [
        {
            "id": f["id"],
            "label": f["label"],
            **data["values"].get(qid, {}).get(f["id"], {"value": None, "kind": "s"}),
        }
        for f in data["fields"]
        if f.get("included", True)
    ]


def valid_option(data, field, value):
    return value == "" or any(o["id"] == value for o in data["options"][field])


def default_value(data, field):
    preferred = "waiting" if field == "statuses" else "Other"
    options = data["options"][field]
    return (
        preferred
        if any(o["id"] == preferred for o in options)
        else options[0]["id"]
        if options
        else ""
    )


def merge_option(data, field, label):
    label = label.strip()
    if not label:
        return ""
    existing = next((o for o in data["options"][field] if o["label"].strip() == label), None)
    if existing:
        return existing["id"]
    oid = store.new_id()
    data["options"][field].append(
        {"id": oid, "label": label, "color": "#6486ad" if field == "statuses" else "#397b60"}
    )
    return oid


def field_bindings(data, plan):
    """Resolve existing extra-field targets once, without changing metadata or a plan."""
    previous = data["fields"]
    fields, used = {}, set()
    for index in plan["mapping"].get("extras", []):
        label = plan["headers"][index] or f"Column {index + 1}"
        candidates = [f for f in previous if f["label"] == label and f["id"] not in used]
        aligned = plan.get("field_matches", {}).get(str(index))
        if aligned:
            candidates = [f for f in previous if f["id"] == aligned and f["id"] not in used]
        if not candidates:
            # A rename is accepted only with exact nonempty values on matched questions.
            for f in previous:
                comparisons = [
                    (
                        row["typed"][index],
                        data["values"].get(plan["matches"][row["key"]], {}).get(f["id"]),
                    )
                    for row in plan["rows"]
                    if plan["matches"].get(row["key"]) not in (None, "new")
                ]
                if (
                    f["id"] not in used
                    and comparisons
                    and any(a.get("value") not in (None, "") for a, _ in comparisons)
                    and all(a == b for a, b in comparisons)
                ):
                    candidates.append(f)
        fid = candidates[0]["id"] if len(candidates) == 1 else None
        fields[index] = fid
        if fid:
            used.add(fid)
    return fields


def import_setup(conn, vid, plan):
    data = read(conn, vid)
    mapping = plan["mapping"]
    for key, field in [("status", "statuses"), ("type", "types")]:
        index = mapping.get(key)
        if index is not None:
            for label in plan.get("validations", {}).get(str(index), []):
                merge_option(data, field, label)
            for row in plan["rows"]:
                merge_option(data, field, row.get("effective_values", row["values"])[index])
    fields = field_bindings(data, plan)
    for field in data["fields"]:
        field["included"] = False
    for index, existing in fields.items():
        fid = existing or store.new_id()
        label = plan["headers"][index] or f"Column {index + 1}"
        data["fields"] = [f for f in data["fields"] if f["id"] != fid]
        data["fields"].append({"id": fid, "label": label, "included": True})
        fields[index] = fid
    data["layout"] = {
        "import_id": plan["id"],
        "sheet": plan["sheet"],
        "header_row": plan["header_row"],
        "region": plan.get(
            "region",
            {
                "start_column": 1,
                "end_column": len(plan["headers"]),
                "header_end_row": plan["header_row"],
                "end_row": max((int(r["key"]) for r in plan["rows"]), default=plan["header_row"]),
            },
        ),
        "mapping": mapping,
        "options": deepcopy(data["options"]),
        "rows": {},
        "columns": {},
        "fields": {fid: index for index, fid in fields.items()},
        "baseline": {},
        "typed_baseline": {},
        "effective_typed_baseline": {},
        "option_baseline": {},
    }
    return data, fields


def import_row(conn, vid, data, row, qid, fields, mapping):
    data["values"].setdefault(qid, {})
    for index, fid in fields.items():
        data["values"][qid][fid] = row.get("effective_typed", row["typed"])[index]
    data["groups"][qid] = row.get("group", "")
    data["layout"]["rows"][qid] = row.get("source_rows", [int(row["key"])])
    data["layout"]["baseline"][qid] = row["values"]
    data["layout"].setdefault("typed_baseline", {})[qid] = deepcopy(row["typed"])
    data["layout"].setdefault("effective_typed_baseline", {})[qid] = deepcopy(
        row.get("source_effective_typed", row["typed"])
    )
    data["layout"].setdefault("reply_baseline", {})[qid] = row.get("formatted_values", {})
    data["layout"].setdefault("option_baseline", {})[qid] = {}
    for key, field in [("status", "statuses"), ("type", "types")]:
        index = mapping.get(key)
        if index is not None:
            data["layout"]["option_baseline"][qid][str(index)] = row.get(
                "source_effective_values", row["values"]
            )[index]
            value = merge_option(data, field, row.get("effective_values", row["values"])[index])
            if key == "type" and not value and row.get("reset_imported_type"):
                continue
            conn.execute(
                f"UPDATE qa_version_questions SET {key}=? WHERE version_id=? AND question_id=?",
                (value, vid, qid),
            )
            if key == "type":
                conn.execute(
                    "UPDATE qa_version_questions SET type_origin='manual' WHERE version_id=? AND question_id=?",
                    (vid, qid),
                )


def update_options(collection, lid, request):
    from . import service

    with store.writing(collection, lid, request, "options.update") as (conn, _, v):
        data = read(conn, v["id"])
        new = request.options.model_dump()
        for field, column in [("statuses", "status"), ("types", "type")]:
            ids = [o["id"] for o in new[field]]
            labels = [o["label"].strip() for o in new[field]]
            if (
                len(ids) != len(set(ids))
                or len(labels) != len(set(labels))
                or any(not label for label in labels)
            ):
                store.fail("invalid_options", 422)
            removed = {o["id"] for o in data["options"][field]} - set(ids)
            for oid in removed:
                used = conn.execute(
                    f"SELECT 1 FROM qa_version_questions WHERE version_id=? AND {column}=?",
                    (v["id"], oid),
                ).fetchone()
                if used:
                    target = request.replacements.get(field, {}).get(oid)
                    if target not in ids and target != "":
                        store.fail("option_in_use")
                    conn.execute(
                        f"UPDATE qa_version_questions SET {column}=? WHERE version_id=? AND {column}=?",
                        (target, v["id"], oid),
                    )
        data["options"] = new
        save(conn, v["id"], data)
    return service.detail(collection, lid)
