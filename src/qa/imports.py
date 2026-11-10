"""Persistent import review plans and atomic version commits."""

import json
import re
from collections import Counter
from copy import deepcopy

from . import excel, metadata, service, store
from .workbook_reader import classify_rows, header_role, validate_question_parents


def normalize(value):
    return re.sub(r"\s+", " ", value).strip()


def asset_path(collection, lid, kind, resource_id):
    from src.file_mgmt import store as files
    from src.paths import assert_resource_id, confine

    for value in (collection, lid, resource_id):
        assert_resource_id(value)
    root = files.COLLECTIONS_DIR / collection / "qa"
    return confine(root / lid / kind / resource_id, root)


def get_plan(conn, lid, iid):
    row = conn.execute("SELECT * FROM qa_imports WHERE id=? AND list_id=?", (iid, lid)).fetchone()
    if not row:
        store.fail("not_found", 404)
    result = json.loads(row["plan_json"])
    result["committed_version_id"] = row["committed_version_id"]
    if not result["committed_version_id"] and not result.get("cancelled"):
        resolve_empty_rounds(conn, lid, result)
    return result


def resolve_empty_rounds(conn, lid, plan):
    """Fresh or wholly empty columns can safely receive independent identities."""
    matches = plan.get("matches", {})
    if not matches:
        return
    all_new = all(value == "new" for value in matches.values())
    for key, target in plan.get("column_matches", {}).items():
        empty = all(not row["values"][int(key)].strip() for row in plan["rows"])
        if target is None and (all_new or empty):
            plan["column_matches"][key] = "new"


def review_plan(conn, lid, plan, *, strict=False):
    """Return an authoritative preview; a review never changes the working version."""
    if not plan.get("committed_version_id") and not plan.get("cancelled"):
        from .import_merge import evaluate

        plan["review"] = evaluate(conn, lid, plan, strict=strict)["review"]
        plan["stale"] = store.get_list(conn, lid)["revision"] != plan["base_revision"]
    return plan


def build_plan(conn, lid, parsed, mapping, *, validate=False, row_actions=None, parents=None):
    mapping = {**mapping}
    active = range(parsed.get("region", {}).get("start_column", 1) - 1, len(parsed["headers"]))
    used = {
        mapping["question"],
        *mapping["replies"],
        mapping.get("number"),
        mapping.get("status"),
        mapping.get("type"),
        mapping.get("group"),
        *mapping.get("ignored", []),
    }
    mapping.setdefault("extras", [i for i in active if i not in used])
    size = len(parsed["headers"])
    if not size and not validate:
        return {
            **parsed,
            "mapping": mapping,
            "matches": {},
            "column_matches": {},
            "mapping_valid": False,
        }
    indices = [
        mapping["question"],
        *mapping["replies"],
        *mapping.get("extras", []),
        *mapping.get("ignored", []),
    ]
    indices += [
        mapping[k] for k in ("number", "status", "type", "group") if mapping.get(k) is not None
    ]
    if len(set(indices)) != len(indices) or any(
        not isinstance(i, int) or i < 0 or i >= size for i in indices
    ):
        store.fail("invalid_mapping", 422)
    history = {}
    for r in conn.execute(
        """SELECT DISTINCT t.question_id,t.body FROM qa_text_revisions t
        JOIN qa_questions q ON q.id=t.question_id WHERE q.list_id=? AND t.column_id IS NULL
        AND NOT EXISTS(SELECT 1 FROM qa_revoked_associations a WHERE a.text_revision_id=t.id)""",
        (lid,),
    ):
        history.setdefault(normalize(r["body"]), set()).add(r["question_id"])
    row_actions = row_actions or parsed.get("row_actions", {})
    if set(row_actions) - {r["key"] for r in parsed["rows"]}:
        store.fail("invalid_mapping", 422)
    parents = parents or parsed.get("parents", {})
    table_rows, question_rows = classify_rows(parsed, mapping, row_actions, parents)
    rows, matches = [], {}
    for row in question_rows:
        text = row["values"][mapping["question"]]
        rows.append({**row, "text": text})
        candidates = history.get(normalize(text), set())
        matches[row["key"]] = (
            next(iter(candidates)) if len(candidates) == 1 else "new" if not history else None
        )
    # Duplicate inputs require explicit decisions, even when the same text uniquely matched.
    counts = Counter(v for v in matches.values() if v and v != "new")
    matches = {k: None if counts[v] > 1 else v for k, v in matches.items()}
    prior = {}
    listing = store.get_list(conn, lid)
    for c in conn.execute(
        "SELECT * FROM qa_version_columns WHERE version_id=?", (listing["current_version_id"],)
    ):
        prior.setdefault(normalize(c["label"]), []).append(c["column_id"])
    column_matches = {}
    for i in mapping["replies"]:
        candidates = prior.get(normalize(parsed["headers"][i]), [])
        column_matches[str(i)] = (
            candidates[0] if len(candidates) == 1 else "new" if not candidates else None
        )
    counts = Counter(v for v in column_matches.values() if v and v != "new")
    column_matches = {k: None if counts[v] > 1 else v for k, v in column_matches.items()}
    used = {v for v in column_matches.values() if v and v != "new"}
    all_columns = [
        r[0]
        for r in conn.execute(
            "SELECT column_id FROM qa_version_columns WHERE version_id=?",
            (listing["current_version_id"],),
        )
    ]
    proposals = {}
    for i in mapping["replies"]:
        if column_matches[str(i)] != "new":
            continue
        samples = [
            (matches[row["key"]], normalize(row["values"][i]))
            for row in rows
            if matches[row["key"]] not in (None, "new")
        ]
        remaining = [cid for cid in all_columns if cid not in used]
        candidates = []
        for cid in remaining:
            comparable = []
            for qid, value in samples:
                prior_cell = conn.execute(
                    """SELECT t.body FROM qa_version_cells c JOIN qa_text_revisions t ON t.id=c.text_revision_id
                    WHERE c.version_id=? AND c.question_id=? AND c.column_id=?""",
                    (listing["current_version_id"], qid, cid),
                ).fetchone()
                comparable.append((value, normalize(prior_cell[0]) if prior_cell else ""))
            if (
                comparable
                and any(len(a) >= 20 for a, _ in comparable)
                and all(a == b for a, b in comparable)
            ):
                candidates.append(cid)
        if len(candidates) == 1:
            proposals[str(i)] = candidates[0]
        elif history and remaining:
            column_matches[str(i)] = None
    proposal_counts = Counter(proposals.values())
    for key, candidate in proposals.items():
        column_matches[key] = candidate if proposal_counts[candidate] == 1 else None
    mapping_confirmed = (
        validate
        or parsed.get("mapping_confirmed", False)
        or header_role(parsed["headers"][mapping["question"]]) == "question"
    )
    return {
        **parsed,
        "rows": rows,
        "mapping": mapping,
        "matches": matches,
        "column_matches": column_matches,
        "mapping_confirmed": mapping_confirmed,
        "mapping_valid": mapping_confirmed
        and bool(rows)
        and all(row["text"].strip() for row in rows)
        and not any(r["kind"] == "review" for r in table_rows),
        "table_rows": table_rows,
        "row_actions": row_actions,
        "parents": parents,
    }


def reading_order(current, previous):
    order = list(current)
    for index, cid in enumerate(previous):
        if cid in order:
            continue
        before = next((c for c in reversed(previous[:index]) if c in order), None)
        after = next((c for c in previous[index + 1 :] if c in order), None)
        position = (
            order.index(before) + 1 if before else order.index(after) if after else len(order)
        )
        order.insert(position, cid)
    return order


def prepare(collection, lid, raw, filename, expected_revision):
    if not filename.lower().endswith(".xlsx"):
        store.fail("unsupported_workbook", 422)
    with store.connection(collection, "update") as conn:
        listing = store.get_list(conn, lid)
        if listing["archived"] or listing["revision"] != expected_revision:
            store.fail("stale_revision")
        base_revision = listing["revision"]
        parsed = excel.read_workbook(raw)
        from .intelligence import map_headers

        parsed, mapping, ai_status = map_headers(raw, parsed)
        from .pipeline import recognize_structure

        parsed = recognize_structure(parsed, mapping)
        iid = store.new_id()
        plan = build_plan(conn, lid, parsed, mapping)
        plan["ai_status"] = ai_status
        plan.update(
            id=iid,
            plan_revision=1,
            base_revision=base_revision,
            filename=filename,
            committed_version_id=None,
        )
        review_plan(conn, lid, plan)
        path = asset_path(collection, lid, "imports", iid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        with conn:
            conn.execute(
                "INSERT INTO qa_imports(id,list_id,base_revision,plan_json,created_at) VALUES(?,?,?,?,?)",
                (iid, lid, base_revision, json.dumps(plan, ensure_ascii=False), store.now()),
            )
    return plan


def read(collection, lid, iid):
    with store.connection(collection) as conn:
        store.get_list(conn, lid)
        return review_plan(conn, lid, get_plan(conn, lid, iid))


def update(collection, lid, iid, payload):
    # Parse outside the write transaction; confirmation still uses revision checks.
    with store.connection(collection, "update") as conn:
        plan = get_plan(conn, lid, iid)
        if (
            plan.get("cancelled")
            or plan["committed_version_id"]
            or payload["plan_revision"] != plan["plan_revision"]
        ):
            store.fail("stale_import")
        if store.get_list(conn, lid)["revision"] != plan["base_revision"] and not payload.get(
            "rebase"
        ):
            store.fail("stale_import")
    parsed = None
    if payload.get("rebase") or any(
        k in payload for k in ("mapping", "sheet", "header_row", "region", "row_actions", "parents")
    ):
        parsed = excel.read_workbook(
            asset_path(collection, lid, "imports", iid).read_bytes(),
            payload.get("sheet", plan["sheet"]),
            payload.get(
                "header_row",
                plan["header_row"]
                if payload.get("sheet", plan["sheet"]) == plan["sheet"]
                else None,
            ),
            payload.get(
                "region",
                plan.get("region")
                if not any(k in payload for k in ("sheet", "header_row"))
                else None,
            ),
        )
        if not payload.get("rebase") and not any(
            k in payload for k in ("mapping", "row_actions", "parents")
        ):
            from .intelligence import map_headers

            parsed, detected_mapping, _ = map_headers(
                asset_path(collection, lid, "imports", iid).read_bytes(), parsed, fixed_region=True
            )
        else:
            detected_mapping = plan["mapping"]
            parsed["mapping_confirmed"] = plan.get("mapping_confirmed", plan["mapping_valid"])
    with store.connection(collection, "update") as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        current = get_plan(conn, lid, iid)
        if (
            current.get("cancelled")
            or current["committed_version_id"]
            or current["plan_revision"] != plan["plan_revision"]
        ):
            store.fail("stale_import")
        if payload.get("rebase"):
            plan["base_revision"] = store.get_list(conn, lid)["revision"]
            for key in (
                "content_choices",
                "blank_columns",
                "retained_questions",
                "removed_questions",
            ):
                plan.pop(key, None)
        if parsed is not None:
            parsed["pipeline_cache"] = plan.get("pipeline_cache", {})
            mapping = payload.get("mapping") or detected_mapping

            def role_signature(value):
                return (
                    *(value.get(key) for key in ("question", "number", "status", "type", "group")),
                    *(
                        tuple(sorted(value.get(key, [])))
                        for key in ("replies", "extras", "ignored")
                    ),
                )

            same_sheet = parsed["sheet"] == plan["sheet"]
            same_roles = same_sheet and role_signature(mapping) == role_signature(plan["mapping"])
            previous_sources = {row["key"]: row for row in plan.get("table_rows", plan["rows"])}
            present_keys = {row["key"] for row in parsed["rows"]}
            unchanged = {
                row["key"]
                for row in parsed["rows"]
                if same_roles
                and row["key"] in previous_sources
                and row["values"] == previous_sources[row["key"]]["values"]
            }
            if same_roles:
                parsed["mapping_confirmed"] = plan.get("mapping_confirmed", plan["mapping_valid"])
            actions = {
                **parsed.get("row_actions", {}),
                **{
                    key: value
                    for key, value in plan.get("row_actions", {}).items()
                    if key in unchanged
                },
                **payload.get("row_actions", {}),
            }
            # Inspect new group boundaries without inheriting any source-row parent edge.
            table, _ = classify_rows(parsed, mapping, actions, dict.fromkeys(present_keys, ""))
            source_groups = {row["key"]: row.get("group_key", "") for row in table}
            eligible_parents = {
                row["key"]
                for row in table
                if row["kind"] in ("question", "child")
                or actions.get(row["key"]) == "child"
                and row["values"][mapping["question"]].strip()
            }
            previous_parents = {
                **{
                    key: row.get("parent_key", "")
                    for key, row in previous_sources.items()
                    if row.get("parent_key")
                },
                **plan.get("parents", {}),
            }
            parents = {}
            valid_parent_choices = set()
            for key, parent in previous_parents.items():
                if key not in unchanged or key not in eligible_parents:
                    continue
                valid = not parent or (
                    parent in unchanged
                    and parent in eligible_parents
                    and source_groups.get(parent) == source_groups.get(key)
                )
                parents[key] = parent if valid else ""
                if valid:
                    valid_parent_choices.add(key)
            parents.update(payload.get("parents", {}))
            if set(parents) - present_keys:
                store.fail("invalid_parent", 422)
            # If a retained child loses its own parent, descendants cannot point to
            # that now-unresolved row. Explicit new edges still get normal validation.
            while True:
                unresolved = {
                    key
                    for key in eligible_parents
                    if actions.get(key) == "child" and not parents.get(key)
                }
                affected = [
                    key
                    for key, parent in parents.items()
                    if parent in unresolved and key not in payload.get("parents", {})
                ]
                if not affected:
                    break
                for key in affected:
                    parents[key] = ""
                    valid_parent_choices.discard(key)
            plan["manual_row_actions"] = {
                key: value
                for key, value in plan.get("manual_row_actions", {}).items()
                if key in unchanged
            }
            plan["manual_parents"] = {
                key: value
                for key, value in plan.get("manual_parents", {}).items()
                if key in valid_parent_choices
            }
            compatible_columns = {
                str(index)
                for index in mapping["replies"]
                if same_sheet
                and index in plan["mapping"]["replies"]
                and index < len(plan["headers"])
                and index < len(parsed["headers"])
                and plan["headers"][index] == parsed["headers"][index]
            }
            rebuilt = build_plan(
                conn,
                lid,
                parsed,
                mapping,
                row_actions=actions,
                validate="mapping" in payload,
                parents=parents,
            )
            previous_text = {row["key"]: row["text"] for row in plan["rows"]}
            safe_questions = {
                row["key"]
                for row in rebuilt["rows"]
                if row["key"] in unchanged and previous_text.get(row["key"]) == row["text"]
            }
            for key in safe_questions:
                if plan["matches"].get(key):
                    rebuilt["matches"][key] = plan["matches"][key]
            rebuilt["column_matches"].update(
                {
                    key: value
                    for key, value in plan["column_matches"].items()
                    if key in compatible_columns
                }
            )
            # A column-wide clear choice must not grow to include unreviewed question rows.
            clear_scope_safe = all(row["key"] in safe_questions for row in rebuilt["rows"])
            plan["blank_columns"] = [
                key
                for key in plan.get("blank_columns", [])
                if clear_scope_safe
                and key in compatible_columns
                and plan["column_matches"].get(key) == rebuilt["column_matches"].get(key)
            ]
            plan["manual_matches"] = {
                key: value
                for key, value in plan.get("manual_matches", {}).items()
                if key in safe_questions
            }
            plan["manual_column_matches"] = {
                key: value
                for key, value in plan.get("manual_column_matches", {}).items()
                if key in compatible_columns
            }
            plan.update(rebuilt)
            for field in ("matches", "column_matches"):
                plan["manual_" + field] = {
                    k: v
                    for k, v in plan.get("manual_" + field, {}).items()
                    if k in plan[field] and plan[field][k] == v
                }
            plan.pop("suggestions", None)
            plan.pop("field_matches", None)
            plan["analysis_complete"] = False
        for key in ("row_actions", "parents"):
            if key in payload:
                plan["manual_" + key] = {**plan.get("manual_" + key, {}), **payload[key]}
        for key in ("matches", "column_matches"):
            if key in payload:
                if set(payload[key]) != set(plan[key]):
                    store.fail("invalid_mapping", 422)
                if plan[key] != payload[key]:
                    plan.pop("content_choices", None)
                plan[key] = payload[key]
                plan["manual_" + key] = payload[key]
        active_targets = set(plan["matches"].values())
        for key in ("retained_questions", "removed_questions"):
            plan[key] = [qid for qid in plan.get(key, []) if qid not in active_targets]
        for key in ("content_choices", "blank_columns", "retained_questions", "removed_questions"):
            if key in payload:
                plan[key] = payload[key]
        resolve_empty_rounds(conn, lid, plan)
        if parsed is not None and "content_choices" not in payload and plan.get("content_choices"):
            review_plan(conn, lid, plan)
            valid = {conflict["id"] for conflict in plan["review"]["conflicts"]}
            plan["content_choices"] = {
                key: value for key, value in plan["content_choices"].items() if key in valid
            }
        review_plan(conn, lid, plan, strict=True)
        plan["plan_revision"] += 1
        conn.execute(
            "UPDATE qa_imports SET plan_json=?,base_revision=? WHERE id=?",
            (json.dumps(plan, ensure_ascii=False), plan["base_revision"], iid),
        )
        return plan


def _validate_assignments(conn, lid, plan):
    if plan.get("cancelled"):
        store.fail("stale_import")
    if not plan.get("mapping_valid"):
        store.fail("question_required", 422)
    validate_question_parents(plan["rows"])
    for key, table in (("matches", "qa_questions"), ("column_matches", "qa_columns")):
        values = list(plan[key].values())
        if any(not v for v in values):
            store.fail("unresolved_match")
        selected = [v for v in values if v != "new"]
        if len(set(selected)) != len(selected):
            store.fail("duplicate_identity_assignment")
        known = {r[0] for r in conn.execute(f"SELECT id FROM {table} WHERE list_id=?", (lid,))}
        if any(v not in known for v in selected):
            store.fail("invalid_mapping", 422)


def commit(collection, lid, iid, plan_revision):
    with store.connection(collection, "update") as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        listing = store.get_list(conn, lid)
        plan = get_plan(conn, lid, iid)
        if plan["committed_version_id"]:
            vid = plan["committed_version_id"]
        else:
            if (
                listing["archived"]
                or listing["revision"] != plan["base_revision"]
                or plan_revision != plan["plan_revision"]
            ):
                store.fail("stale_import")
            _validate_assignments(conn, lid, plan)
            from .import_merge import evaluate

            merged = evaluate(conn, lid, plan)
            if merged["review"]["unresolved"]:
                store.fail("unresolved_content")
            old = conn.execute(
                "SELECT * FROM qa_versions WHERE id=?", (listing["current_version_id"],)
            ).fetchone()
            previous_columns = (
                [
                    r[0]
                    for r in conn.execute(
                        "SELECT column_id FROM qa_version_columns WHERE version_id=? ORDER BY position",
                        (old["id"],),
                    )
                ]
                if old
                else []
            )
            if old:
                v = service.clone_version(conn, lid, old)
            else:
                first_id = store.new_id()
                conn.execute(
                    "INSERT INTO qa_versions(id,list_id,number,state,created_at) VALUES(?,?,1,'working',?)",
                    (first_id, lid, store.now()),
                )
                conn.execute("UPDATE qa_lists SET current_version_id=? WHERE id=?", (first_id, lid))
                v = conn.execute("SELECT * FROM qa_versions WHERE id=?", (first_id,)).fetchone()
            vid = v["id"]
            conn.execute(
                "UPDATE qa_version_questions SET included=0,absent_since=coalesce(absent_since,?) WHERE version_id=?",
                (v["number"], vid),
            )
            conn.execute(
                "UPDATE qa_version_columns SET included=0,absent_since=coalesce(absent_since,?) WHERE version_id=?",
                (v["number"], vid),
            )
            for qid in merged["kept_question_ids"]:
                conn.execute(
                    "UPDATE qa_version_questions SET included=1,absent_since=NULL WHERE version_id=? AND question_id=?",
                    (vid, qid),
                )
            for cid in merged["kept_column_ids"]:
                conn.execute(
                    "UPDATE qa_version_columns SET included=1,absent_since=NULL WHERE version_id=? AND column_id=?",
                    (vid, cid),
                )
            old_meta = metadata.read(conn, vid)
            previous_outline = deepcopy(old_meta.get("outline", []))
            source_outline = deepcopy(
                (old_meta.get("layout") or {}).get("outline", previous_outline)
            )
            meta, extra_fields = metadata.import_setup(conn, vid, plan)
            column_ids = {}
            for pos, index in enumerate(plan["mapping"]["replies"]):
                cid = plan["column_matches"][str(index)]
                label = plan["headers"][index] or f"Reply {pos + 1}"
                if cid == "new":
                    cid = service._add_column(conn, lid, vid, label, pos)
                conn.execute(
                    "UPDATE qa_version_columns SET included=1,absent_since=NULL,label=?,position=? WHERE version_id=? AND column_id=?",
                    (label, pos, vid, cid),
                )
                column_ids[index] = cid
            for pos, cid in enumerate(reading_order(list(column_ids.values()), previous_columns)):
                conn.execute(
                    "UPDATE qa_version_columns SET position=? WHERE version_id=? AND column_id=?",
                    (pos, vid, cid),
                )
            meta["layout"]["columns"] = {cid: index for index, cid in column_ids.items()}
            meta["layout"]["header_labels"] = {
                cid: plan["headers"][index] for index, cid in column_ids.items()
            }
            for pos, row in enumerate(plan["rows"]):
                effective = merged["rows"][row["key"]]
                qid = plan["matches"][row["key"]]
                if qid == "new":
                    qid = store.new_id()
                    conn.execute("INSERT INTO qa_questions VALUES(?,?,?)", (qid, lid, vid))
                    rid = store.text_revision(conn, qid, effective["text"])
                    conn.execute(
                        """INSERT INTO qa_version_questions(version_id,question_id,text_revision_id,included,position,status,type,updated_at)
                        VALUES(?,?,?,1,?,?,?,?)""",
                        (
                            vid,
                            qid,
                            rid,
                            pos,
                            metadata.default_value(meta, "statuses"),
                            metadata.default_value(meta, "types"),
                            store.now(),
                        ),
                    )
                    for c in conn.execute(
                        "SELECT column_id FROM qa_version_columns WHERE version_id=?", (vid,)
                    ).fetchall():
                        service._new_cell(conn, vid, qid, c[0])
                else:
                    q = service._question(conn, vid, qid)
                    rid = store.text_revision(
                        conn, qid, effective["text"], previous_id=q["text_revision_id"]
                    )
                number_col = plan["mapping"].get("number")
                number = row["values"][number_col] if number_col is not None else ""
                conn.execute(
                    "UPDATE qa_version_questions SET included=?,absent_since=CASE WHEN ? THEN NULL ELSE coalesce(absent_since,?) END,position=?,text_revision_id=?,external_number=?,updated_at=? WHERE version_id=? AND question_id=?",
                    (
                        effective.get("included", True),
                        effective.get("included", True),
                        v["number"],
                        pos,
                        rid,
                        number,
                        store.now(),
                        vid,
                        qid,
                    ),
                )
                for index, cid in column_ids.items():
                    service._save_reply(
                        conn,
                        v,
                        qid,
                        cid,
                        effective["replies"][str(index)],
                    )
                if effective.get("reset_type"):
                    default_type = (
                        "Other"
                        if plan["mapping"].get("type") is None
                        and metadata.valid_option(meta, "types", "Other")
                        else ""
                    )
                    conn.execute(
                        "UPDATE qa_version_questions SET type=?,type_origin='default' WHERE version_id=? AND question_id=?",
                        (default_type, vid, qid),
                    )
                metadata.import_row(
                    conn,
                    vid,
                    meta,
                    {
                        **row,
                        "source_effective_values": row.get("effective_values", row["values"]),
                        "source_effective_typed": row.get("effective_typed", row["typed"]),
                        "effective_values": effective["effective_values"],
                        "effective_typed": effective["effective_typed"],
                        "reset_imported_type": effective.get("reset_type", False),
                    },
                    qid,
                    extra_fields,
                    plan["mapping"],
                )
                metadata.save(conn, vid, meta)
                # Identity suggestions contain targets, not summaries or question types.
                # Optional display annotations are generated after a successful commit.
                row["committed_question_id"] = qid
                row["external_baseline"] = {**effective["external_baseline"], "question_id": qid}
                row["committed_fields"] = {str(index): fid for index, fid in extra_fields.items()}
                row["committed_state"] = dict(service._question(conn, vid, qid))
                row["owned_columns"] = list(column_ids.values())
                row["committed_cells"] = dict(
                    conn.execute(
                        "SELECT column_id,text_revision_id FROM qa_version_cells WHERE version_id=? AND question_id=?",
                        (vid, qid),
                    )
                )
            if merged["regrouped_types"]:
                used_types = {
                    item[0]
                    for item in conn.execute(
                        "SELECT DISTINCT type FROM qa_version_questions WHERE version_id=?", (vid,)
                    )
                }
                obsolete = merged["regrouped_types"] - used_types
                meta["options"]["types"] = [
                    option for option in meta["options"]["types"] if option["id"] not in obsolete
                ]

            from . import outline

            # Resolve source identities against the immutable external structure,
            # not a locally renamed note/group that the source has not observed.
            meta["outline"] = source_outline
            outline.from_plan(
                meta, plan, {row["key"]: row["committed_question_id"] for row in plan["rows"]}
            )
            if merged.get("keep_structure"):
                imported_outline = meta["outline"]
                incoming_by_id = {item["id"]: item for item in imported_outline}
                kept = []
                for item in previous_outline:
                    # Keep local semantic structure, with this file's current bindings.
                    bindings = {
                        k: value
                        for k, value in incoming_by_id.get(item["id"], {}).items()
                        if k in ("source_rows", "source_values", "text_column", "text_columns")
                    }
                    kept.append(
                        {
                            **item,
                            "source_rows": [],
                            "source_values": [],
                            "text_columns": [],
                            **bindings,
                        }
                    )
                known = {item["id"] for item in kept}
                source_ids = {item["id"] for item in source_outline}
                kept.extend(
                    item
                    for item in imported_outline
                    if item["id"] not in known
                    and (item["kind"] == "question" or item["id"] not in source_ids)
                )
                meta["outline"] = kept
            present = {item["id"] for item in meta["outline"]}
            for item in previous_outline:
                if (
                    item["kind"] == "question"
                    and item["id"] in merged["kept_question_ids"]
                    and item["id"] not in present
                ):
                    retained = {**item, "source_rows": []}
                    if retained.get("group_id") not in present:
                        retained["group_id"] = ""
                    if retained.get("parent_id") not in present:
                        retained["parent_id"] = ""
                    meta["outline"].append(retained)
                    present.add(item["id"])
            included = {
                row[0]
                for row in conn.execute(
                    "SELECT question_id FROM qa_version_questions WHERE version_id=? AND included=1",
                    (vid,),
                )
            }
            by_id = {item["id"]: item for item in meta["outline"]}
            for item in meta["outline"]:
                if item.get("group_id") not in by_id:
                    item["group_id"] = ""
                if item["id"] in included and item.get("parent_id") not in included:
                    item["parent_id"] = ""
                parent = by_id.get(item.get("parent_id"))
                if parent:
                    item["group_id"] = parent.get("group_id", "")
            outline.persist(conn, vid, meta)
            if old:
                from .import_drafts import carry_forward

                carry_forward(conn, old["id"], vid)
            conn.execute(
                "UPDATE qa_lists SET revision=revision+1,updated_at=? WHERE id=?",
                (store.now(), lid),
            )
            store.queue_index(conn, lid)
            conn.execute(
                "UPDATE qa_imports SET committed_version_id=?,plan_json=? WHERE id=?",
                (vid, json.dumps(plan, ensure_ascii=False), iid),
            )
            conn.execute(
                "INSERT INTO qa_events VALUES(?,?,?,?,?,?,?)",
                (
                    store.new_id(),
                    lid,
                    vid,
                    "import.commit",
                    service.get_actor().id,
                    json.dumps({"import_id": iid}),
                    store.now(),
                ),
            )
    return service.detail(collection, lid, vid)
