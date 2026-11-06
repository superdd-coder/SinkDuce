"""Versioned, ordered groups, notes and question trees; source bindings stay internal."""

import json
from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5

from . import store


def descendants(items, item_id):
    found = {item_id}
    while True:
        expanded = found | {i["id"] for i in items if i.get("parent_id") in found}
        if expanded == found:
            return found
        found = expanded


def from_plan(meta, plan, identities):
    previous = meta.get("outline", [])
    used, items, group = set(), [], ""
    question_rows = {r["key"]: r for r in plan["rows"]}
    old_members, new_members = {}, {}
    for old in previous:
        if old["kind"] == "question" and old.get("group_id"):
            old_members.setdefault(old["group_id"], set()).add(old["id"])
    for row in plan["rows"]:
        if row.get("group_key") and row["key"] in identities:
            new_members.setdefault(row["group_key"], set()).add(identities[row["key"]])

    def source_id(category, label, source_key):
        candidates = [
            i
            for i in previous
            if i["kind"] == category and i.get("text") == label and i["id"] not in used
        ]
        if category == "group" and len(candidates) != 1:
            members = new_members.get(source_key, set())
            overlaps = [
                i
                for i in previous
                if i["kind"] == "group"
                and i["id"] not in used
                and members.intersection(old_members.get(i["id"], set()))
            ]
            if len(overlaps) == 1:
                candidates = overlaps
        iid = (
            candidates[0]["id"]
            if len(candidates) == 1
            else uuid5(NAMESPACE_URL, f"qa:{plan['id']}:{source_key}").hex
        )
        used.add(iid)
        return iid

    column_groups = {}
    for row in plan.get("table_rows", plan["rows"]):
        kind = row.get("kind", "question")
        if kind == "continuation":
            continue
        source_key = row["key"]
        if kind in ("question", "child") and source_key in identities:
            logical = question_rows[source_key]
            group_key = logical.get("group_key", "")
            question_group = group
            if plan["mapping"].get("group") is not None and group_key.startswith("column:"):
                if group_key not in column_groups:
                    label = logical["group"]
                    gid = column_groups[group_key] = source_id("group", label, group_key)
                    items.append(
                        {
                            "id": gid,
                            "kind": "group",
                            "text": label,
                            "group_id": "",
                            "parent_id": "",
                            "source_rows": [],
                        }
                    )
                question_group = column_groups[group_key]
            item = {
                "id": identities[source_key],
                "kind": "question",
                "group_id": question_group,
                "parent_id": identities.get(logical.get("parent_key"), ""),
                "source_rows": logical.get("source_rows", [int(source_key)]),
            }
        else:
            category = "group" if kind == "section" else "note" if kind == "note" else "layout"
            vals = row["values"]
            semantic = [
                i
                for i, v in enumerate(vals)
                if v
                and i != plan["mapping"].get("number")
                and (
                    category != "group"
                    or row.get("typed", [{}] * len(vals))[i].get("kind") not in ("n", "date")
                )
            ]
            nonempty = semantic or [i for i, v in enumerate(vals) if v]
            label = "\n".join(vals[i] for i in nonempty)
            iid = source_id(category, label, source_key)
            item = {
                "id": iid,
                "kind": category,
                "text": label,
                "group_id": group,
                "parent_id": "",
                "source_rows": [int(source_key)],
                "text_column": nonempty[0] if nonempty else plan["region"]["start_column"] - 1,
                "source_values": vals,
                "text_columns": nonempty,
            }
            if category == "group":
                group = iid
                item["group_id"] = ""
        items.append(item)
    # Omitted identities remain available for restoration without entering the export.
    present = {i["id"] for i in items}
    omitted = []
    for old in previous:
        if old["kind"] == "question" and old["id"] not in present:
            retained = deepcopy(old)
            retained["source_rows"] = []
            if retained.get("group_id") not in present:
                retained["group_id"] = ""
            if retained.get("parent_id") not in present | {
                i["id"] for i in previous if i["kind"] == "question"
            }:
                retained["parent_id"] = ""
            items.append(retained)
            omitted.append(retained)
    # A missing child follows its surviving parent, including a changed group.
    # Reinsert omitted rows near their old siblings so the list remains grouped.
    by_id = {i["id"]: i for i in items}
    for item in omitted:
        parent = by_id.get(item.get("parent_id"))
        if parent:
            item["group_id"] = parent.get("group_id", "")
    for item in omitted:
        old_position = next(j for j, i in enumerate(previous) if i["id"] == item["id"])
        following = next(
            (
                by_id[i["id"]]
                for i in previous[old_position + 1 :]
                if i["id"] in present
                and i["kind"] != "group"
                and by_id[i["id"]].get("group_id", "") == item.get("group_id", "")
                and by_id[i["id"]].get("parent_id", "") == item.get("parent_id", "")
            ),
            None,
        )
        move(
            items,
            item["id"],
            parent_id=item.get("parent_id", ""),
            before_id=following["id"] if following else None,
        )
    meta["outline"] = items
    if meta.get("layout") is not None:
        meta["layout"]["outline"] = deepcopy(items)
        if plan["mapping"].get("group") is not None:
            meta["layout"]["group_inheritance"] = {
                row["key"]: row["group_inherited"]
                for row in plan["rows"]
                if row.get("group_inherited")
            }
    sync_groups(meta)
    return items


def ensure(conn, vid, meta):
    if "outline" not in meta:
        layout = meta.get("layout") or {}
        raw = conn.execute(
            "SELECT plan_json FROM qa_imports WHERE id=?", (layout.get("import_id", ""),)
        ).fetchone()
        if raw:
            plan = json.loads(raw[0])
            identities = {str(rows[0]): qid for qid, rows in layout.get("rows", {}).items() if rows}
            from_plan(meta, plan, identities)
        else:
            meta["outline"] = []
    known = {i["id"] for i in meta["outline"]}
    for row in conn.execute(
        "SELECT question_id FROM qa_version_questions WHERE version_id=? ORDER BY position", (vid,)
    ):
        if row[0] not in known:
            meta["outline"].append(
                {
                    "id": row[0],
                    "kind": "question",
                    "group_id": "",
                    "parent_id": "",
                    "source_rows": [],
                }
            )
    return meta["outline"]


def sync_groups(meta):
    names = {i["id"]: i["text"] for i in meta["outline"] if i["kind"] == "group"}
    meta["groups"].update(
        {
            i["id"]: names.get(i.get("group_id"), "")
            for i in meta["outline"]
            if i["kind"] == "question"
        }
    )


def validate(items):
    by_id = {i["id"]: i for i in items}
    if len(by_id) != len(items):
        store.fail("invalid_outline", 422)
    for i in items:
        gid, pid = i.get("group_id", ""), i.get("parent_id", "")
        if gid and (gid not in by_id or by_id[gid]["kind"] != "group"):
            store.fail("invalid_group", 422)
        if pid:
            if (
                i["kind"] != "question"
                or pid not in by_id
                or by_id[pid]["kind"] != "question"
                or by_id[pid].get("group_id", "") != gid
            ):
                store.fail("invalid_parent", 422)
            seen, parent = {i["id"]}, pid
            while parent:
                if parent in seen or parent not in by_id:
                    store.fail("invalid_parent", 422)
                seen.add(parent)
                parent = by_id[parent].get("parent_id", "")


def move(items, iid, *, group_id=None, parent_id=None, before_id=None):
    item = next((i for i in items if i["id"] == iid), None)
    if item is None or item["kind"] == "layout":
        store.fail("not_found", 404)
    members = descendants(items, iid)
    if item["kind"] == "group":
        members |= {i["id"] for i in items if i.get("group_id") == iid}
    block = [i for i in items if i["id"] in members]
    remaining = [i for i in items if i["id"] not in members]
    if before_id in members or parent_id in members:
        store.fail("invalid_parent", 422)
    if item["kind"] != "group":
        gid = item.get("group_id", "") if group_id is None else group_id
        pid = item.get("parent_id", "") if parent_id is None else parent_id
        if group_id is not None and gid != item.get("group_id") and parent_id is None:
            pid = ""
        if pid:
            parent = next(
                (i for i in remaining if i["id"] == pid and i["kind"] == "question"), None
            )
            if parent is None or group_id is not None and gid != parent.get("group_id", ""):
                store.fail("invalid_parent", 422)
            gid = parent.get("group_id", "")
        if gid and not any(i["kind"] == "group" and i["id"] == gid for i in remaining):
            store.fail("invalid_group", 422)
        item["parent_id"] = pid
        for member in block:
            member["group_id"] = gid
    if before_id:
        target = next((i for i in remaining if i["id"] == before_id), None)
        if target is None:
            store.fail("invalid_outline", 422)
        if item["kind"] == "group":
            if target["kind"] != "group":
                store.fail("invalid_outline", 422)
        elif target.get("group_id", "") != item.get("group_id", "") or target.get(
            "parent_id", ""
        ) != item.get("parent_id", ""):
            store.fail("invalid_outline", 422)
        index = remaining.index(target)
    elif item["kind"] == "group":
        index = len(remaining)
    elif item.get("parent_id"):
        family = descendants(remaining, item["parent_id"])
        index = max(j for j, i in enumerate(remaining) if i["id"] in family) + 1
    else:
        gid = item.get("group_id", "")
        indices = [
            j
            for j, i in enumerate(remaining)
            if i["id"] == gid or i.get("group_id", "") == gid and i["kind"] != "group"
        ]
        index = max(indices) + 1 if indices else 0
    items[:] = remaining[:index] + block + remaining[index:]
    validate(items)


def persist(conn, vid, meta):
    from . import metadata

    validate(meta["outline"])
    sync_groups(meta)
    for position, item in enumerate(meta["outline"]):
        if item["kind"] == "question":
            conn.execute(
                "UPDATE qa_version_questions SET position=? WHERE version_id=? AND question_id=?",
                (position, vid, item["id"]),
            )
    metadata.save(conn, vid, meta)


def append_question(items, iid, group_id=None, parent_id=None):
    move(items, iid, group_id=group_id, parent_id=parent_id)
    item = next(i for i in items if i["id"] == iid)
    if item.get("parent_id"):
        return
    siblings = [
        i
        for i in items
        if i.get("group_id") == item.get("group_id") and i["kind"] != "group" and i["id"] != iid
    ]
    last_question = max((j for j, i in enumerate(siblings) if i["kind"] == "question"), default=-1)
    trailing = next(
        (
            i
            for i in siblings[last_question + 1 :]
            if i["kind"] == "note" and not i.get("parent_id")
        ),
        None,
    )
    if trailing and last_question >= 0:
        move(items, iid, before_id=trailing["id"])


def create_item(collection, lid, request):
    from . import metadata, service

    with store.writing(collection, lid, request, "outline.create") as (conn, listing, version):
        meta = metadata.read(conn, version["id"])
        remember(meta, listing["revision"] + 1)
        item = {
            "id": store.new_id(),
            "kind": request.kind,
            "text": request.text.strip(),
            "group_id": "",
            "parent_id": "",
            "source_rows": [],
        }
        if not item["text"]:
            store.fail("text_required", 422)
        meta["outline"].append(item)
        move(
            meta["outline"],
            item["id"],
            group_id=request.group_id or "",
            before_id=request.before_id,
        )
        persist(conn, version["id"], meta)
    return service.detail(collection, lid)


def move_item(collection, lid, iid, request):
    from . import metadata, service

    with store.writing(collection, lid, request, "outline.move") as (conn, listing, version):
        meta = metadata.read(conn, version["id"])
        remember(meta, listing["revision"] + 1)
        move(
            meta["outline"],
            iid,
            group_id=request.group_id,
            parent_id=request.parent_id,
            before_id=request.before_id,
        )
        persist(conn, version["id"], meta)
    return service.detail(collection, lid)


def edit_item(collection, lid, iid, request):
    from . import metadata, service

    with store.writing(collection, lid, request, "outline.edit") as (conn, listing, version):
        meta = metadata.read(conn, version["id"])
        remember(meta, listing["revision"] + 1)
        item = next(
            (i for i in meta["outline"] if i["id"] == iid and i["kind"] in ("group", "note")), None
        )
        if item is None:
            store.fail("not_found", 404)
        if request.remove:
            if item["kind"] == "group":
                if request.replacement_id is None and any(
                    i.get("group_id") == iid for i in meta["outline"]
                ):
                    store.fail("group_replacement_required")
                if request.replacement_id == iid:
                    store.fail("invalid_group", 422)
                roots = [
                    i["id"]
                    for i in meta["outline"]
                    if i.get("group_id") == iid and not i.get("parent_id") and i["kind"] != "layout"
                ]
                for root in roots:
                    move(meta["outline"], root, group_id=request.replacement_id or "")
                for row in meta["outline"]:
                    if row.get("group_id") == iid:
                        row["group_id"] = request.replacement_id or ""
            meta["outline"].remove(item)
        elif request.text is not None:
            if not request.text.strip():
                store.fail("text_required", 422)
            item["text"] = request.text.strip()
        persist(conn, version["id"], meta)
    return service.detail(collection, lid)


def remember(meta, revision):
    meta["outline_undo"] = deepcopy(meta["outline"])
    meta["outline_undo_revision"] = revision


def undo(collection, lid, request):
    from . import metadata, service

    with store.writing(collection, lid, request, "outline.undo") as (conn, listing, version):
        meta = metadata.read(conn, version["id"])
        if "outline_undo" not in meta or meta.get("outline_undo_revision") != listing["revision"]:
            store.fail("stale_revision")
        meta["outline"] = meta.pop("outline_undo")
        meta.pop("outline_undo_revision", None)
        persist(conn, version["id"], meta)
    return service.detail(collection, lid)
