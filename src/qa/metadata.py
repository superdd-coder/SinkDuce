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


def valid_status(data, value):
    return value == "" or any(o["id"] == value for o in data["options"]["statuses"])


def default_status(data):
    options = data["options"]["statuses"]
    return (
        "waiting"
        if any(o["id"] == "waiting" for o in options)
        else options[0]["id"]
        if options
        else ""
    )


def _merge_status(data, label):
    label = label.strip()
    if not label:
        return ""
    statuses = data["options"]["statuses"]
    existing = next((o for o in statuses if o["label"].strip() == label), None)
    if existing:
        return existing["id"]
    oid = store.new_id()
    statuses.append({"id": oid, "label": label, "color": "#6486ad"})
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
    status_col = mapping.get("status")
    if status_col is not None:
        for label in plan.get("validations", {}).get(str(status_col), []):
            _merge_status(data, label)
        for row in plan["rows"]:
            _merge_status(data, row.get("effective_values", row["values"])[status_col])
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
    status_col = mapping.get("status")
    if status_col is not None:
        data["layout"]["option_baseline"][qid][str(status_col)] = row.get(
            "source_effective_values", row["values"]
        )[status_col]
        value = _merge_status(data, row.get("effective_values", row["values"])[status_col])
        conn.execute(
            "UPDATE qa_version_questions SET status=? WHERE version_id=? AND question_id=?",
            (value, vid, qid),
        )


def update_options(collection, lid, request):
    from . import service

    with store.writing(collection, lid, request, "options.update") as (conn, _, v):
        data = read(conn, v["id"])
        statuses = request.options.model_dump()["statuses"]
        ids = {o["id"] for o in statuses}
        labels = {o["label"].strip() for o in statuses}
        if len(ids) != len(statuses) or len(labels) != len(statuses) or "" in labels:
            store.fail("invalid_options", 422)
        removed = {o["id"] for o in data["options"]["statuses"]} - ids
        for oid in removed:
            used = conn.execute(
                "SELECT 1 FROM qa_version_questions WHERE version_id=? AND status=?",
                (v["id"], oid),
            ).fetchone()
            if used:
                target = request.replacements.get("statuses", {}).get(oid)
                if target not in ids and target != "":
                    store.fail("option_in_use")
                conn.execute(
                    "UPDATE qa_version_questions SET status=? WHERE version_id=? AND status=?",
                    (target, v["id"], oid),
                )
        data["options"]["statuses"] = statuses
        save(conn, v["id"], data)
    return service.detail(collection, lid)
