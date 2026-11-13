"""Previewed current-version identity corrections, preserving frozen artifacts."""

import json

from src.identity import get_actor

from . import imports, metadata, service, store


def preview(collection, lid, iid, data):
    with store.connection(collection, "update") as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        listing = store.get_list(conn, lid)
        plan = imports.get_plan(conn, lid, iid)
        if (
            listing["archived"]
            or listing["revision"] != data.expected_revision
            or not plan["committed_version_id"]
        ):
            store.fail("stale_revision")
        row = next((r for r in plan["rows"] if r["key"] == data.row_key), None)
        if not row or not row.get("committed_question_id"):
            store.fail("not_found", 404)
        qid = row["committed_question_id"]
        vid = listing["current_version_id"]
        current = dict(service._question(conn, vid, qid))
        if not current["included"]:
            store.fail("not_included")
        target = data.target
        if target == qid:
            store.fail("same_identity", 422)
        target_row = None if target == "new" else service._question(conn, vid, target)
        if target_row and target_row["included"]:
            store.fail("duplicate_identity_assignment")
        body = conn.execute(
            "SELECT body FROM qa_text_revisions WHERE id=?", (current["text_revision_id"],)
        ).fetchone()[0]
        replies = [
            dict(r)
            for r in conn.execute(
                """SELECT c.column_id,c.text_revision_id,t.body,v.label FROM qa_version_cells c
            JOIN qa_text_revisions t ON t.id=c.text_revision_id JOIN qa_version_columns v ON v.version_id=c.version_id AND v.column_id=c.column_id
            WHERE c.version_id=? AND c.question_id=? ORDER BY v.position""",
                (vid, qid),
            )
        ]
        if "owned_columns" in row:
            replies = [
                r
                for r in replies
                if r["column_id"] in row["owned_columns"]
                or (
                    r["text_revision_id"] != row.get("committed_cells", {}).get(r["column_id"])
                    and (r["column_id"] in row.get("committed_cells", {}) or r["body"])
                )
            ]
        drafts = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM qa_reply_drafts WHERE version_id=? AND question_id=?", (vid, qid)
            )
        ]
        if (
            data.drafts == "move"
            and target != "new"
            and conn.execute(
                "SELECT 1 FROM qa_reply_drafts WHERE version_id=? AND question_id=? AND actor_id=?",
                (vid, target, get_actor().id),
            ).fetchone()
        ):
            store.fail("target_has_draft")
        original = conn.execute(
            "SELECT parent_id FROM qa_versions WHERE id=?", (plan["committed_version_id"],)
        ).fetchone()[0]
        target_text = (
            conn.execute(
                "SELECT body FROM qa_text_revisions WHERE id=?", (target_row["text_revision_id"],)
            ).fetchone()[0]
            if target_row
            else ""
        )
        initial_state = row.get("committed_state") or dict(
            service._question(conn, plan["committed_version_id"], qid)
        )
        overrides = (
            {"status": current["status"]} if current["status"] != initial_state["status"] else {}
        )
        if current["type_origin"] == "manual" and (
            current["type"] != initial_state["type"] or initial_state["type_origin"] != "manual"
        ):
            overrides.update(type=current["type"], type_origin="manual")
        packet = {
            **data.model_dump(),
            "id": store.new_id(),
            "list_id": lid,
            "import_id": iid,
            "version_id": vid,
            "source_id": qid,
            "target_text": target_text,
            "text": body,
            "replies": replies,
            "overrides": overrides,
            "draft_count": len(drafts),
            "draft_fingerprints": [
                (d["actor_id"], d["column_id"], d["draft_revision"]) for d in drafts
            ],
            "base_version_id": original,
            "source_snapshot": current,
            "actor_id": get_actor().id,
        }
        conn.execute(
            "INSERT INTO qa_correction_previews VALUES(?,?,?,?)",
            (packet["id"], lid, json.dumps(packet, ensure_ascii=False), store.now()),
        )
        return {
            k: v for k, v in packet.items() if k not in ("draft_fingerprints", "source_snapshot")
        }


def apply(collection, lid, iid, preview_id):
    with store.connection(collection, "update") as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        record = conn.execute(
            "SELECT payload_json FROM qa_correction_previews WHERE id=? AND list_id=?",
            (preview_id, lid),
        ).fetchone()
        if not record:
            store.fail("not_found", 404)
        p = json.loads(record[0])
        listing = store.get_list(conn, lid)
        if p["import_id"] != iid or p["actor_id"] != get_actor().id:
            store.fail("not_found", 404)
        if (
            listing["revision"] != p["expected_revision"]
            or listing["current_version_id"] != p["version_id"]
            or listing["archived"]
        ):
            store.fail("stale_revision")
        vid, oldid = p["version_id"], p["source_id"]
        v = conn.execute("SELECT * FROM qa_versions WHERE id=?", (vid,)).fetchone()
        current_drafts = [
            list(r)
            for r in conn.execute(
                "SELECT actor_id,column_id,draft_revision FROM qa_reply_drafts WHERE version_id=? AND question_id=?",
                (vid, oldid),
            )
        ]
        if sorted(current_drafts) != sorted(p["draft_fingerprints"]):
            store.fail("stale_draft")
        meta = metadata.read(conn, vid)
        source = service._question(conn, vid, oldid)
        target = p["target"]
        if target == "new":
            target = store.new_id()
            conn.execute("INSERT INTO qa_questions VALUES(?,?,?)", (target, lid, vid))
            rid = store.text_revision(conn, target, p["text"])
            conn.execute(
                """INSERT INTO qa_version_questions(version_id,question_id,text_revision_id,included,position,status,type,updated_at)
                VALUES(?,?,?,1,?,?,?,?)""",
                (
                    vid,
                    target,
                    rid,
                    source["position"],
                    metadata.default_status(meta),
                    "",
                    store.now(),
                ),
            )
            for col in conn.execute(
                "SELECT column_id FROM qa_version_columns WHERE version_id=?", (vid,)
            ).fetchall():
                service._new_cell(conn, vid, target, col[0])
        else:
            t = service._question(conn, vid, target)
            if t["included"]:
                store.fail("duplicate_identity_assignment")
            rid = store.text_revision(conn, target, p["text"], previous_id=t["text_revision_id"])
        conn.execute(
            "UPDATE qa_version_questions SET included=1,absent_since=NULL,text_revision_id=?,position=?,external_number=?,updated_at=? WHERE version_id=? AND question_id=?",
            (rid, source["position"], source["external_number"], store.now(), vid, target),
        )
        for field, value in p["overrides"].items():
            conn.execute(
                f"UPDATE qa_version_questions SET {field}=? WHERE version_id=? AND question_id=?",
                (value, vid, target),
            )
        for reply in p["replies"]:
            service._save_reply(conn, v, target, reply["column_id"], reply["body"])
            destination = conn.execute(
                "SELECT text_revision_id FROM qa_version_cells WHERE version_id=? AND question_id=? AND column_id=?",
                (vid, target, reply["column_id"]),
            ).fetchone()[0]
            sources = conn.execute(
                "SELECT sources_json FROM qa_reply_sources WHERE text_revision_id=?",
                (reply["text_revision_id"],),
            ).fetchone()
            if sources:
                conn.execute(
                    "INSERT OR IGNORE INTO qa_reply_sources VALUES(?,?)", (destination, sources[0])
                )
            if p["drafts"] == "move":
                if conn.execute(
                    "SELECT 1 FROM qa_reply_drafts WHERE version_id=? AND question_id=? AND column_id=? AND actor_id=?",
                    (vid, target, reply["column_id"], get_actor().id),
                ).fetchone():
                    store.fail("target_has_draft")
                conn.execute(
                    "UPDATE qa_reply_drafts SET question_id=?,base_cell_revision_id=?,context_hash=?,draft_revision=draft_revision+1 WHERE version_id=? AND question_id=? AND column_id=? AND actor_id=?",
                    (target, destination, "", vid, oldid, reply["column_id"], get_actor().id),
                )
        before = conn.execute(
            "SELECT * FROM qa_version_questions WHERE version_id=? AND question_id=?",
            (p["base_version_id"], oldid),
        ).fetchone()
        # Revoke only text revisions owned by the corrected row, up to its pre-import base.
        cursor = source["text_revision_id"]
        while cursor and (not before or cursor != before["text_revision_id"]):
            conn.execute(
                "INSERT OR IGNORE INTO qa_revoked_associations VALUES(?,?)", (cursor, preview_id)
            )
            cursor = conn.execute(
                "SELECT previous_id FROM qa_text_revisions WHERE id=?", (cursor,)
            ).fetchone()[0]
        if before:
            for field in ("text_revision_id", "external_number", "status", "type", "type_origin"):
                conn.execute(
                    f"UPDATE qa_version_questions SET {field}=? WHERE version_id=? AND question_id=?",
                    (before[field], vid, oldid),
                )
            for col in conn.execute(
                "SELECT column_id FROM qa_version_columns WHERE version_id=?", (vid,)
            ).fetchall():
                previous = conn.execute(
                    "SELECT t.body FROM qa_version_cells c JOIN qa_text_revisions t ON t.id=c.text_revision_id WHERE c.version_id=? AND c.question_id=? AND c.column_id=?",
                    (p["base_version_id"], oldid, col[0]),
                ).fetchone()
                service._save_reply(conn, v, oldid, col[0], previous[0] if previous else "")
        conn.execute(
            "UPDATE qa_version_questions SET included=0,absent_since=?,updated_at=? WHERE version_id=? AND question_id=?",
            (v["number"], store.now(), vid, oldid),
        )
        conn.execute(
            "DELETE FROM qa_annotations WHERE version_id=? AND question_id IN (?,?)",
            (vid, target, oldid),
        )
        plan = imports.get_plan(conn, lid, iid)
        # Move field values and source coordinates with the corrected question identity.
        meta["values"][target] = meta["values"].get(oldid, {}).copy()
        meta["groups"][target] = meta["groups"].get(oldid, "")
        for container in (meta, meta.get("layout") or {}):
            if "outline" in container:
                container["outline"] = [i for i in container["outline"] if i["id"] != target]
                for item in container["outline"]:
                    if item["id"] == oldid:
                        item["id"] = target
                    if item.get("parent_id") == oldid:
                        item["parent_id"] = target
        if meta["layout"] and oldid in meta["layout"]["rows"]:
            for field in ("rows", "baseline", "option_baseline"):
                if oldid in meta["layout"].get(field, {}):
                    meta["layout"][field][target] = meta["layout"][field].pop(oldid)
        for key in ("status", "type"):
            if plan["mapping"].get(key) is not None:
                conn.execute(
                    f"UPDATE qa_version_questions SET {key}=? WHERE version_id=? AND question_id=?",
                    (source[key], vid, target),
                )
        base_meta = metadata.read(conn, p["base_version_id"])
        meta["values"][oldid] = base_meta["values"].get(oldid, {})
        meta["groups"][oldid] = base_meta["groups"].get(oldid, "")
        metadata.save(conn, vid, meta)
        for row in plan["rows"]:
            if row["key"] == p["row_key"]:
                row["committed_question_id"] = target
        conn.execute(
            "UPDATE qa_imports SET plan_json=? WHERE id=?",
            (json.dumps(plan, ensure_ascii=False), iid),
        )
        conn.execute(
            "UPDATE qa_lists SET revision=revision+1,updated_at=? WHERE id=?", (store.now(), lid)
        )
        store.queue_index(conn, lid)
        conn.execute(
            "INSERT INTO qa_events VALUES(?,?,?,?,?,?,?)",
            (
                store.new_id(),
                lid,
                vid,
                "identity.corrected",
                get_actor().id,
                json.dumps(
                    {"from": oldid, "to": target, "preview_id": preview_id, "import_id": iid}
                ),
                store.now(),
            ),
        )
    return service.detail(collection, lid)
