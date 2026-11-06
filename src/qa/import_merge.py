"""Authoritative, deterministic content merges; raw workbook input stays immutable."""

import hashlib
import json
from copy import deepcopy

from . import metadata, store
from .reply_format import reply_runs


def _empty(value):
    if isinstance(value, dict):
        return _empty(value.get("value"))
    return value is None or isinstance(value, str) and not value.strip()


def _canonical(value, kind):
    if kind == "reply":
        return [(r.text.replace("\r\n", "\n"), r.bold, r.italic) for r in reply_runs(str(value))]
    if isinstance(value, str):
        return value.replace("\r\n", "\n")
    return value


def _display(value):
    if isinstance(value, dict):
        value = value.get("value")
    return "" if value is None else str(value)


class _Baselines:
    def __init__(self, conn, lid, vid):
        self.conn = conn
        self.revoked = {
            r[0] for r in conn.execute("SELECT text_revision_id FROM qa_revoked_associations")
        }
        ancestors = set()
        while vid and vid not in ancestors:
            ancestors.add(vid)
            row = conn.execute(
                "SELECT parent_id FROM qa_versions WHERE id=? AND list_id=?", (vid, lid)
            ).fetchone()
            vid = row[0] if row else None
        self.records = {}
        self.import_parents = {}
        self.parent_type_ids = {}
        for record in conn.execute(
            """SELECT i.*,v.number,v.parent_id FROM qa_imports i JOIN qa_versions v ON v.id=i.committed_version_id
            WHERE i.list_id=? ORDER BY v.number DESC,i.created_at DESC,i.id DESC""",
            (lid,),
        ):
            if record["committed_version_id"] not in ancestors:
                continue
            try:
                plan = json.loads(record["plan_json"])
            except (ValueError, TypeError):
                continue
            self.import_parents[record["id"]] = record["parent_id"]
            for row in plan.get("rows", []):
                self.records.setdefault(row.get("committed_question_id"), []).append((plan, row))

    def _valid(self, row, qid):
        state = row.get("committed_state", {})
        rid = state.get("text_revision_id")
        if not rid or rid in self.revoked or state.get("question_id", qid) != qid:
            return False
        revision = self.conn.execute(
            "SELECT question_id,column_id FROM qa_text_revisions WHERE id=?", (rid,)
        ).fetchone()
        external = row.get("external_baseline")
        return bool(
            revision
            and revision[0] == qid
            and revision[1] is None
            and (
                not external or external.get("version") == 1 and external.get("question_id") == qid
            )
        )

    def rows(self, qid):
        for plan, row in self.records.get(qid, []):
            yield plan, row, self._valid(row, qid)

    def question(self, qid):
        for _, row, valid in self.rows(qid):
            if not valid:
                return None
            external = row.get("external_baseline")
            return external.get("question") if external else row.get("text")
        return None

    def reply(self, qid, cid):
        for plan, row, valid in self.rows(qid):
            if not valid:
                return None
            indexes, owned = (
                plan.get("mapping", {}).get("replies", []),
                row.get("owned_columns", []),
            )
            if (
                len(indexes) != len(owned)
                or len(set(owned)) != len(owned)
                or len(set(indexes)) != len(indexes)
            ):
                return None
            if cid not in owned:
                continue
            index = indexes[owned.index(cid)]
            rid = row.get("committed_cells", {}).get(cid)
            revision = self.conn.execute(
                "SELECT question_id,column_id FROM qa_text_revisions WHERE id=?", (rid,)
            ).fetchone()
            if not revision or tuple(revision) != (qid, cid) or rid in self.revoked:
                return None
            external = row.get("external_baseline")
            if external:
                if str(index) in external.get("replies", {}):
                    return external["replies"][str(index)]
                continue
            values = row.get("values", [])
            if index < len(values):
                return row.get("formatted_values", {}).get(str(index), values[index])
        return None

    def field(self, qid, kind, fid=None):
        for plan, row, valid in self.rows(qid):
            if not valid:
                return None
            mapping = plan.get("mapping", {})
            index = (
                mapping.get(kind)
                if kind != "extra"
                else next(
                    (
                        int(i)
                        for i, old_fid in row.get("committed_fields", {}).items()
                        if old_fid == fid
                    ),
                    None,
                )
            )
            if index is None:
                continue
            external = row.get("external_baseline")
            if external:
                if str(index) in external.get("fields", {}):
                    return external["fields"][str(index)]
                continue
            typed = row.get("effective_typed", row.get("typed", []))
            values = row.get("effective_values", row.get("values", []))
            if index < len(typed):
                return typed[index]
            if index < len(values):
                return {"value": values[index], "kind": "s"}
        return None

    def edited(self, q, cells, meta, questions):
        for _, row, valid in self.rows(q["question_id"]):
            if not valid:
                return True
            state = row["committed_state"]
            before = self.conn.execute(
                "SELECT body FROM qa_text_revisions WHERE id=?", (state["text_revision_id"],)
            ).fetchone()
            if not before or _canonical(before[0], "question") != _canonical(q["body"], "question"):
                return True
            remote_question = self.question(q["question_id"])
            if remote_question is not None and _canonical(
                remote_question, "question"
            ) != _canonical(q["body"], "question"):
                return True
            for key in ("status", "external_number"):
                if key in state and q[key] != state[key]:
                    return True
            for key, option_key in (("status", "statuses"), ("type", "types")):
                if key == "type" and q["type_origin"] != "manual":
                    continue
                observed = self.field(q["question_id"], key)
                label = next(
                    (
                        option["label"]
                        for option in meta["options"][option_key]
                        if option["id"] == q[key]
                    ),
                    "",
                )
                if observed is not None and _display(observed) != label:
                    return True
            if q["type_origin"] == "manual" and (
                q["type"] != state.get("type") or state.get("type_origin") != "manual"
            ):
                return True
            for cid, cell in cells.items():
                remote_reply = self.reply(q["question_id"], cid)
                if remote_reply is not None:
                    if _canonical(remote_reply, "reply") != _canonical(cell["body"], "reply"):
                        return True
                elif not _empty(cell["body"]):
                    return True
                rid = row.get("committed_cells", {}).get(cid)
                before = self.conn.execute(
                    "SELECT body,question_id,column_id FROM qa_text_revisions WHERE id=?", (rid,)
                ).fetchone()
                if before and (
                    before[1] != q["question_id"] or before[2] != cid or rid in self.revoked
                ):
                    return True
                if _canonical(cell["body"], "reply") != _canonical(
                    before[0] if before else "", "reply"
                ):
                    return True
            for fid, value in meta["values"].get(q["question_id"], {}).items():
                old = self.field(q["question_id"], "extra", fid)
                if old is not None and old != value or old is None and not _empty(value):
                    return True
            source = (meta.get("layout") or {}).get("outline", [])
            current = meta.get("outline", [])
            source_item = next((i for i in source if i["id"] == q["question_id"]), None)
            current_item = next((i for i in current if i["id"] == q["question_id"]), None)
            if source_item and current_item:
                if any(
                    source_item.get(key, "") != current_item.get(key, "")
                    for key in ("group_id", "parent_id")
                ):
                    return True
                active = {qid for qid, item in questions.items() if item["included"]}
                shared = active & {i["id"] for i in source if i["kind"] == "question"}
                before = [i["id"] for i in source if i["id"] in shared]
                after = [i["id"] for i in current if i["id"] in shared]
                if before.index(q["question_id"]) != after.index(q["question_id"]):
                    return True
            return False
        return True

    def regrouped_type(self, question, header, label, options):
        """Only undo a type still carrying the old interpretation of this source column."""
        if not label or question["type_origin"] != "manual":
            return False, None
        oid = question["type"]
        builtins = {option["id"] for option in metadata.defaults()["options"]["types"]}
        option = next((item for item in options if item["id"] == oid), None)
        if oid in builtins or not option:
            return False, None

        def normalized(value):
            return " ".join(value.casefold().split())

        for prior, row, valid in self.rows(question["question_id"]):
            if not valid:
                break
            index = prior.get("mapping", {}).get("type")
            if index is None:
                continue
            headers = prior.get("headers", [])
            state = row["committed_state"]
            external = row.get("external_baseline")
            values = row.get("effective_values", row.get("values", []))
            observed = (
                _display(external.get("fields", {}).get(str(index)))
                if external
                else _display(values[index])
                if index < len(values)
                else ""
            )
            if (
                index >= len(headers)
                or normalized(headers[index]) != normalized(header)
                or state.get("type_origin") != "manual"
                or state.get("type") != oid
                or option["label"].strip() != observed.strip()
                or normalized(observed) != normalized(label)
            ):
                break
            prior_id = prior.get("id")
            if prior_id not in self.import_parents:
                break
            parent = self.import_parents[prior_id]
            if (
                parent
                and self.conn.execute(
                    """SELECT 1 FROM qa_version_questions WHERE version_id=? AND question_id=?
                AND type_origin='manual' AND type=?""",
                    (parent, question["question_id"], oid),
                ).fetchone()
            ):
                # A repeated import may have carried a prior choice. Look for the
                # source that introduced it instead of assuming it came from Excel.
                continue
            generated = False
            if option["color"] == "#397b60":
                if parent not in self.parent_type_ids:
                    parent_options = (
                        metadata.read(self.conn, parent)["options"]["types"] if parent else []
                    )
                    self.parent_type_ids[parent] = {item["id"] for item in parent_options}
                generated = oid not in self.parent_type_ids[parent]
            return True, oid if generated else None
        return False, None


def evaluate(conn, lid, plan, *, strict=True):
    """Return effective content and a public review without writing or changing plan."""
    listing = store.get_list(conn, lid)
    vid = listing["current_version_id"]
    questions = {
        r["question_id"]: dict(r)
        for r in conn.execute(
            """SELECT q.*,t.body FROM qa_version_questions q JOIN qa_text_revisions t ON t.id=q.text_revision_id
        WHERE q.version_id=?""",
            (vid,),
        )
    }
    columns = {
        r["column_id"]: dict(r)
        for r in conn.execute(
            "SELECT * FROM qa_version_columns WHERE version_id=? ORDER BY position", (vid,)
        )
    }
    cells = {}
    for row in conn.execute(
        """SELECT c.*,t.body FROM qa_version_cells c JOIN qa_text_revisions t ON t.id=c.text_revision_id
        WHERE c.version_id=?""",
        (vid,),
    ):
        cells.setdefault(row["question_id"], {})[row["column_id"]] = dict(row)
    meta = metadata.read(conn, vid) if vid else metadata.defaults()
    baseline = _Baselines(conn, lid, vid)
    field_targets = metadata.field_bindings(meta, plan)
    matches, column_matches = plan.get("matches", {}), plan.get("column_matches", {})
    for targets, known in ((matches, questions), (column_matches, columns)):
        if any(target and target != "new" and target not in known for target in targets.values()):
            store.fail("invalid_mapping", 422)
    choices = plan.get("content_choices", {})
    if not isinstance(choices, dict) or any(
        value not in ("keep", "incoming") for value in choices.values()
    ):
        store.fail("invalid_mapping", 422)
    blank_columns = plan.get("blank_columns", [])
    allowed_columns = {str(i) for i in plan.get("mapping", {}).get("replies", [])}
    if not isinstance(blank_columns, list) or any(
        key not in allowed_columns for key in blank_columns
    ):
        store.fail("invalid_mapping", 422)
    counts = dict.fromkeys(
        (
            "kept_replies",
            "updated_replies",
            "new_replies",
            "conflicts",
            "blank_kept",
            "cleared_replies",
        ),
        0,
    )
    conflicts, effective, regrouped_types = [], {}, set()

    def merge(
        row,
        qid,
        kind,
        label,
        current,
        remote,
        old,
        column_key=None,
        force=False,
        cleared=False,
        target_id=None,
        default_choice=None,
    ):
        if default_choice is None:
            if not force and _empty(remote):
                return current, False
            if force or _canonical(remote, kind) == _canonical(current, kind):
                return remote, False
            if old is not None:
                if _canonical(remote, kind) == _canonical(old, kind):
                    return current, False
                if _canonical(current, kind) == _canonical(old, kind):
                    return remote, False
            elif _empty(current) and not cleared:
                return remote, False
        digest = hashlib.sha256(
            json.dumps(
                [
                    plan.get("id"),
                    row["key"],
                    qid,
                    column_key,
                    target_id,
                    kind,
                    label,
                    current,
                    remote,
                    old,
                ],
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()[:32]
        choice = choices.get(digest, default_choice)
        conflicts.append(
            {
                "id": digest,
                "row_key": row["key"],
                "question_id": qid,
                "column_key": column_key,
                "kind": kind,
                "label": label,
                "current": _display(current),
                "incoming": _display(remote),
                "baseline": None if old is None else _display(old),
                "choice": choice,
            }
        )
        return (remote if choice == "incoming" else current), choice is None

    for row in plan.get("rows", []):
        qid = matches.get(row["key"])
        if not qid:
            continue
        current = questions.get(qid)
        values = deepcopy(row.get("effective_values", row["values"]))
        typed = deepcopy(
            row.get(
                "effective_typed",
                row.get("typed", [{"value": value, "kind": "s"} for value in values]),
            )
        )
        result = {
            "text": row["text"],
            "included": True,
            "replies": {},
            "effective_values": values,
            "effective_typed": typed,
        }
        external = {
            "version": 1,
            "question_id": qid if current else None,
            "question": row["text"],
            "replies": {},
            "fields": {},
        }
        if current:
            if not current["included"] and qid in (meta.get("layout") or {}).get("rows", {}):
                inclusion, _ = merge(
                    row,
                    qid,
                    "membership",
                    "Question inclusion",
                    "Removed from this version",
                    "Include in this version",
                    "Include in this version",
                    default_choice="keep",
                )
                result["included"] = inclusion == "Include in this version"
            result["text"], _ = merge(
                row,
                qid,
                "question",
                "Question",
                current["body"],
                row["text"],
                baseline.question(qid),
            )
            group_col = plan["mapping"].get("group")
            if group_col is not None and result["included"]:
                reset, prune = baseline.regrouped_type(
                    current,
                    plan["headers"][group_col],
                    row.get("group_value", ""),
                    meta["options"]["types"],
                )
                result["reset_type"] = reset
                if prune:
                    regrouped_types.add(prune)
        values[plan["mapping"]["question"]] = result["text"]
        for index in plan["mapping"].get("replies", []):
            key, cid = str(index), column_matches.get(str(index))
            if not cid:
                continue
            cell = cells.get(qid, {}).get(cid)
            local = cell["body"] if cell else ""
            remote = row.get("formatted_values", {}).get(key, row["values"][index])
            old = baseline.reply(qid, cid) if current and cid != "new" else None
            force = key in blank_columns and _empty(remote)
            chosen, unresolved = merge(
                row,
                qid,
                "reply",
                plan["headers"][index],
                local,
                remote,
                old,
                key,
                force=force,
                cleared=bool(cell and cell.get("historical_revision_id")),
                target_id=cid,
            )
            result["replies"][key] = chosen
            values[index] = chosen
            typed[index] = {"value": chosen, "kind": "s"}
            if not _empty(remote) or force:
                external["replies"][key] = remote
            elif old is not None:
                external["replies"][key] = old
            if unresolved or not result["included"]:
                continue
            local_empty = _empty(local)
            if _empty(remote) and not force and not local_empty:
                counts["blank_kept"] += 1
            if _empty(chosen):
                if not local_empty:
                    counts["cleared_replies"] += 1
            elif local_empty:
                counts["new_replies"] += 1
            elif _canonical(chosen, "reply") == _canonical(local, "reply"):
                counts["kept_replies"] += 1
            else:
                counts["updated_replies"] += 1
        fields = [(key, plan["mapping"].get(key), None) for key in ("status", "type")]
        for index in plan["mapping"].get("extras", []):
            fields.append(("extra", index, field_targets[index]))
        for kind, index, fid in fields:
            if index is None:
                continue
            remote = deepcopy(typed[index])
            old = baseline.field(qid, kind, fid) if current and (kind != "extra" or fid) else None
            if kind == "extra":
                local = deepcopy(meta["values"].get(qid, {}).get(fid, {"value": None, "kind": "s"}))
            else:
                options = meta["options"]["statuses" if kind == "status" else "types"]
                local = {
                    "value": next(
                        (o["label"] for o in options if current and o["id"] == current[kind]), ""
                    ),
                    "kind": "s",
                }
                remote = {"value": values[index], "kind": "s"}
                if kind == "type" and result.get("reset_type"):
                    local, old = {"value": "", "kind": "s"}, None
            chosen, _ = (
                merge(
                    row,
                    qid,
                    kind,
                    plan["headers"][index],
                    local,
                    remote,
                    old,
                    str(index),
                    target_id=fid or kind,
                )
                if current
                else (remote, False)
            )
            if _empty(remote.get("value")):
                chosen = local if current else remote
                if old is not None:
                    external["fields"][str(index)] = old
            else:
                external["fields"][str(index)] = remote
            typed[index] = chosen
            values[index] = _display(chosen)
        result["external_baseline"] = external
        effective[row["key"]] = result

    mapped = {target for target in matches.values() if target and target != "new"}
    pending = any(not target for target in matches.values())
    missing = (
        {qid for qid, q in questions.items() if q["included"] and qid not in mapped}
        if not pending
        else set()
    )
    retained, removed = plan.get("retained_questions", []), plan.get("removed_questions", [])
    if (
        not isinstance(retained, list)
        or not isinstance(removed, list)
        or set(retained) & set(removed)
    ):
        store.fail("invalid_mapping", 422)
    if strict and (set(retained) | set(removed)) - missing:
        store.fail("invalid_mapping", 422)
    drafts = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT question_id FROM qa_reply_drafts WHERE version_id=? AND length(trim(body))>0",
            (vid,),
        )
    }
    kept = {
        matches[key] for key, row in effective.items() if row["included"] and matches[key] != "new"
    }
    missing_review = []
    for qid, q in questions.items():
        if qid not in missing:
            continue
        reason = (
            "draft"
            if qid in drafts
            else "local_changes"
            if baseline.edited(q, cells.get(qid, {}), meta, questions)
            else "not_in_file"
        )
        # The file defines this version's membership. Local work remains in the
        # frozen prior version; only an explicit choice carries a missing item on.
        keep = qid in retained
        if keep:
            kept.add(qid)
        missing_review.append({"id": qid, "text": q["body"], "kept": keep, "reason": reason})
    if pending:
        kept.update(qid for qid, q in questions.items() if q["included"])
    column_ids = {cid for cid in column_matches.values() if cid and cid != "new"}
    if questions or drafts:
        column_ids.update(cid for cid, column in columns.items() if column["included"])
    retained_columns = [
        {"id": cid, "label": column["label"]}
        for cid, column in columns.items()
        if cid in column_ids and cid not in column_matches.values()
    ]
    keep_structure = False
    if not pending and plan.get("mapping_valid", True):
        from . import outline

        source_outline = (meta.get("layout") or {}).get("outline")
        preview = deepcopy(meta)
        preview["outline"] = deepcopy(
            source_outline if source_outline is not None else meta.get("outline", [])
        )
        identities = {
            row["key"]: matches[row["key"]] if matches[row["key"]] != "new" else "new:" + row["key"]
            for row in plan.get("rows", [])
        }
        outline.from_plan(preview, plan, identities)
        # Membership decisions handle new/missing questions separately. Compare
        # order and relationships only for identities occurring on both sides.
        shared = mapped & {qid for qid, q in questions.items() if q["included"]}

        def semantic(items):
            return [
                {
                    "id": item["id"],
                    "kind": item["kind"],
                    "group_id": item.get("group_id", ""),
                    "parent_id": item.get("parent_id", ""),
                    **({"text": item.get("text", "")} if item["kind"] != "question" else {}),
                }
                for item in items
                if item["kind"] in ("group", "note")
                or item["kind"] == "question"
                and item["id"] in shared
            ]

        local = semantic(meta.get("outline", []))
        remote = semantic(preview["outline"])
        old = semantic(source_outline) if source_outline is not None else None
        if local != remote and (local or old is not None):
            chosen, _ = merge(
                {"key": "structure"}, None, "structure", "Groups and order", local, remote, old
            )
            keep_structure = chosen == local
            if conflicts and conflicts[-1]["kind"] == "structure":
                names = {qid: q["body"] for qid, q in questions.items()}

                def describe(items):
                    return "\n".join(
                        (item.get("text") or names.get(item["id"], item["id"]))[:180]
                        + (" [subquestion]" if item.get("parent_id") else "")
                        for item in items
                    )

                conflicts[-1].update(
                    current=describe(local),
                    incoming=describe(remote),
                    baseline=None if old is None else describe(old),
                )
    valid_conflicts = {item["id"] for item in conflicts}
    if strict and set(choices) - valid_conflicts:
        store.fail("invalid_mapping", 422)
    counts["conflicts"] = sum(item["choice"] is None for item in conflicts)
    review = {
        "conflicts": conflicts,
        "unresolved": counts["conflicts"],
        "missing_questions": missing_review,
        "kept_columns": retained_columns,
        "counts": counts,
    }
    return {
        "rows": effective,
        "keep_structure": keep_structure,
        "kept_question_ids": [qid for qid in questions if qid in kept],
        "kept_column_ids": [cid for cid in columns if cid in column_ids],
        "regrouped_types": regrouped_types,
        "review": review,
    }
