"""Version-aware Q&A use cases. Every mutation is a short SQLite transaction."""

import json

from src.identity import can, get_actor

from . import metadata, outline, store
from .numbering import display_numbers


def detail(collection_id, list_id, version_id=None):
    with store.connection(collection_id) as conn:
        conn.execute("BEGIN")
        listing = dict(store.get_list(conn, list_id))
        version_id = version_id or listing["current_version_id"]
        if version_id is None:
            return {**listing, "version": None, "columns": [], "questions": [], "can_edit": False}
        version = conn.execute(
            "SELECT * FROM qa_versions WHERE id=? AND list_id=?", (version_id, list_id)
        ).fetchone()
        if not version:
            store.fail("not_found", 404)
        columns = [
            dict(r)
            for r in conn.execute(
                "SELECT column_id AS id,label,included,position,absent_since FROM qa_version_columns WHERE version_id=? ORDER BY position",
                (version_id,),
            )
        ]
        meta = metadata.read(conn, version_id)
        source_columns = (meta.get("layout") or {}).get("columns", {})
        for column in columns:
            if column["id"] in source_columns:
                column["source_column"] = source_columns[column["id"]]
        item_index = {i["id"]: i for i in meta["outline"]}
        questions = []
        for row in conn.execute(
            """SELECT q.*,t.body AS text FROM qa_version_questions q
            JOIN qa_text_revisions t ON t.id=q.text_revision_id WHERE q.version_id=? ORDER BY q.position""",
            (version_id,),
        ):
            q = dict(row)
            q["id"] = q.pop("question_id")
            q["included"] = bool(q["included"])
            q["fields"] = metadata.public(meta, q["id"])
            q["group"] = meta["groups"].get(q["id"], "")
            item = item_index.get(q["id"], {})
            q["group_id"] = item.get("group_id", "")
            q["parent_id"] = item.get("parent_id", "")
            q["replies"] = []
            reply_version = version_id
            reply_columns = columns
            q["last_included_version"] = version["number"] if q["included"] else None
            if not q["included"]:
                last = conn.execute(
                    """SELECT v.id,v.number FROM qa_versions v JOIN qa_version_questions q ON q.version_id=v.id
                    WHERE v.list_id=? AND v.number<? AND q.question_id=? AND q.included=1 ORDER BY v.number DESC LIMIT 1""",
                    (list_id, version["number"], q["id"]),
                ).fetchone()
                if last:
                    reply_version = last["id"]
                    q["last_included_version"] = last["number"]
                    reply_columns = [
                        dict(c)
                        for c in conn.execute(
                            "SELECT column_id AS id,label,included,position,absent_since FROM qa_version_columns WHERE version_id=? AND included=1 ORDER BY position",
                            (reply_version,),
                        )
                    ]
            for col in reply_columns:
                cell = conn.execute(
                    """SELECT c.*,t.body,h.body AS historical_body,t.previous_id
                    FROM qa_version_cells c JOIN qa_text_revisions t ON t.id=c.text_revision_id
                    LEFT JOIN qa_text_revisions h ON h.id=c.historical_revision_id
                    WHERE c.version_id=? AND c.question_id=? AND c.column_id=?""",
                    (reply_version, q["id"], col["id"]),
                ).fetchone()
                if cell:
                    c = dict(cell)
                    c.update(
                        label=col["label"],
                        included=bool(col["included"]),
                        absent_since=col["absent_since"],
                    )
                    c["historical_body"] = c["historical_body"] or ""
                    c["modified"] = bool(c.pop("previous_id"))
                    q["replies"].append(c)
            q["latest_reply"] = next(
                (r["body"] for r in reversed(q["replies"]) if r["included"] and r["body"]), ""
            )
            annotation = conn.execute(
                "SELECT * FROM qa_annotations WHERE version_id=? AND question_id=?",
                (version_id, q["id"]),
            ).fetchone()
            valid = annotation and annotation["context_key"] == annotation_key(
                conn, version_id, q["id"]
            )
            q["summary"] = annotation["summary"] if valid else ""
            q["reply_summary"] = annotation["reply_summary"] if valid else ""
            questions.append(q)
            for reply in q["replies"]:
                sources = conn.execute(
                    "SELECT sources_json FROM qa_reply_sources WHERE text_revision_id=?",
                    (reply["text_revision_id"],),
                ).fetchone()
                reply["sources"] = json.loads(sources[0]) if sources else []
        for col in columns:
            col["included"] = bool(col["included"])
        numbers = display_numbers(meta, questions)
        for q in questions:
            q["display_number"] = numbers.get(q["id"], "")
        listing.update(
            version=dict(version),
            options=meta["options"],
            outline=[
                {
                    **{
                        k: v
                        for k, v in item.items()
                        if k not in ("source_rows", "source_values", "text_column", "text_columns")
                    },
                    "display_number": numbers.get(item["id"], ""),
                }
                for item in meta["outline"]
                if item["kind"] != "layout"
            ],
            original_layout=bool(meta["layout"]),
            columns=columns,
            questions=questions,
            can_edit=version["state"] == "working"
            and not listing["archived"]
            and can(get_actor(), "qa.update", {"collection_id": collection_id}),
        )
        pending = sum(
            q["included"] and (not q["summary"] or q["type_origin"] == "default") for q in questions
        )
        listing["annotation_pending"] = pending
        listing["annotation_state"] = meta.get("annotation_state", "idle") if pending else "ready"
        template = conn.execute(
            "SELECT template_id FROM qa_list_templates WHERE list_id=?", (list_id,)
        ).fetchone()
        listing["template_id"] = template[0] if template else None
        return listing


def project_questions(collection_id, include_omitted=False):
    with store.connection(collection_id) as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                """SELECT q.question_id AS id,q.included,q.status,q.type,q.type_origin,q.updated_at,q.external_number,
            t.body AS text,l.id AS list_id,l.title AS list_title,l.current_version_id AS version_id FROM qa_lists l
            JOIN qa_version_questions q ON q.version_id=l.current_version_id
            JOIN qa_text_revisions t ON t.id=q.text_revision_id
            WHERE l.archived=0 AND (? OR q.included=1) ORDER BY q.updated_at DESC""",
                (include_omitted,),
            )
        ]
        versions = {vid: metadata.read(conn, vid) for vid in {r["version_id"] for r in rows}}
        numbers = {
            vid: display_numbers(meta, [r for r in rows if r["version_id"] == vid])
            for vid, meta in versions.items()
        }
        for row in rows:
            vid = row.pop("version_id")
            options = versions[vid]["options"]
            status = next((o for o in options["statuses"] if o["id"] == row["status"]), {})
            kind = next((o for o in options["types"] if o["id"] == row["type"]), {})
            row.update(
                display_number=numbers[vid].get(row["id"], ""),
                status_label=status.get("label", ""),
                status_color=status.get("color", "#7c8580"),
                type_label=kind.get("label", ""),
            )
        return rows


def list_lists(collection_id):
    with store.connection(collection_id) as conn:
        return [
            dict(r)
            for r in conn.execute("""SELECT l.*,v.number AS version_number,
            (SELECT count(*) FROM qa_version_questions q WHERE q.version_id=v.id AND q.included=1) AS question_count
            FROM qa_lists l JOIN qa_versions v ON v.id=l.current_version_id ORDER BY l.updated_at DESC""")
        ]


def create_list(collection_id, data):
    lid, vid, timestamp = (
        store.new_id(),
        store.new_id() if data.mode == "blank" else None,
        store.now(),
    )
    with store.connection(collection_id, "create") as conn, conn:
        conn.execute(
            "INSERT INTO qa_lists(id,title,current_version_id,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (lid, data.title, vid, get_actor().id, timestamp, timestamp),
        )
        if vid:
            conn.execute(
                "INSERT INTO qa_versions(id,list_id,number,state,created_at) VALUES(?,?,1,'working',?)",
                (vid, lid, timestamp),
            )
            for pos, label in enumerate(data.columns):
                _add_column(conn, lid, vid, label, pos)
    return detail(collection_id, lid)


def _question(conn, vid, qid):
    row = conn.execute(
        "SELECT * FROM qa_version_questions WHERE version_id=? AND question_id=?", (vid, qid)
    ).fetchone()
    if not row:
        store.fail("not_found", 404)
    return row


def _column(conn, vid, cid):
    row = conn.execute(
        "SELECT * FROM qa_version_columns WHERE version_id=? AND column_id=?", (vid, cid)
    ).fetchone()
    if not row:
        store.fail("not_found", 404)
    return row


def _new_cell(conn, vid, qid, cid):
    rid = store.text_revision(conn, qid, "", cid)
    conn.execute(
        "INSERT INTO qa_version_cells(version_id,question_id,column_id,text_revision_id) VALUES(?,?,?,?)",
        (vid, qid, cid, rid),
    )


def _add_column(conn, lid, vid, label, pos):
    cid = store.new_id()
    conn.execute("INSERT INTO qa_columns VALUES(?,?,?)", (cid, lid, vid))
    conn.execute("INSERT INTO qa_version_columns VALUES(?,?,?,1,?,NULL)", (vid, cid, label, pos))
    for row in conn.execute(
        "SELECT question_id FROM qa_version_questions WHERE version_id=?", (vid,)
    ).fetchall():
        _new_cell(conn, vid, row[0], cid)
    return cid


def add_question(collection_id, lid, data):
    if not data.text.strip():
        store.fail("question_required", 422)
    with store.writing(collection_id, lid, data, "question.create") as (conn, _, v):
        meta = metadata.read(conn, v["id"])
        kind = (
            data.type if "type" in data.model_fields_set else metadata.default_value(meta, "types")
        )
        if not metadata.valid_option(meta, "types", kind):
            store.fail("invalid_option", 422)
        status = metadata.default_value(meta, "statuses")
        qid = store.new_id()
        conn.execute("INSERT INTO qa_questions VALUES(?,?,?)", (qid, lid, v["id"]))
        rid = store.text_revision(conn, qid, data.text)
        pos = conn.execute(
            "SELECT coalesce(max(position),-1)+1 FROM qa_version_questions WHERE version_id=?",
            (v["id"],),
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO qa_version_questions(version_id,question_id,text_revision_id,included,position,external_number,status,type,updated_at)
            VALUES(?,?,?,1,?,?,?,?,?)""",
            (v["id"], qid, rid, pos, data.external_number, status, kind, store.now()),
        )
        if "type" in data.model_fields_set:
            conn.execute(
                "UPDATE qa_version_questions SET type_origin='manual' WHERE version_id=? AND question_id=?",
                (v["id"], qid),
            )
        for c in conn.execute(
            "SELECT column_id FROM qa_version_columns WHERE version_id=?", (v["id"],)
        ).fetchall():
            _new_cell(conn, v["id"], qid, c[0])
        default_group = next(
            (i.get("group_id", "") for i in reversed(meta["outline"]) if i["kind"] == "question"),
            "",
        )
        meta["outline"].append(
            {"id": qid, "kind": "question", "group_id": "", "parent_id": "", "source_rows": []}
        )
        outline.append_question(
            meta["outline"],
            qid,
            group_id=data.group_id
            if data.group_id is not None
            else None
            if data.parent_id
            else default_group,
            parent_id=data.parent_id or "",
        )
        outline.persist(conn, v["id"], meta)
    return detail(collection_id, lid)


def update_question(collection_id, lid, qid, data):
    with store.writing(collection_id, lid, data, "question.update") as (conn, _, v):
        q = _question(conn, v["id"], qid)
        changes = data.model_dump(exclude_none=True, exclude={"expected_revision", "version_id"})
        if "text" in changes:
            text = changes.pop("text")
            if not text.strip():
                store.fail("question_required", 422)
            changes["text_revision_id"] = store.text_revision(
                conn, qid, text, previous_id=q["text_revision_id"]
            )
        meta = metadata.read(conn, v["id"])
        for key, field in [("status", "statuses"), ("type", "types")]:
            if key in changes and not metadata.valid_option(meta, field, changes[key]):
                store.fail("invalid_option", 422)
        if "type" in changes:
            changes["type_origin"] = "manual"
        changes["updated_at"] = store.now()
        conn.execute(
            f"UPDATE qa_version_questions SET {','.join(k + '=?' for k in changes)} WHERE version_id=? AND question_id=?",
            (*changes.values(), v["id"], qid),
        )
    return detail(collection_id, lid)


def membership(collection_id, lid, qid, data):
    with store.writing(collection_id, lid, data, "question.membership") as (conn, _, v):
        q = _question(conn, v["id"], qid)
        meta = metadata.read(conn, v["id"])
        family = outline.descendants(meta["outline"], qid)
        selected = next((i for i in meta["outline"] if i["id"] == qid), {})
        if data.included and selected.get("parent_id"):
            parent = _question(conn, v["id"], selected["parent_id"])
            if not parent["included"]:
                store.fail("parent_not_included", 422)
        absent = None if data.included else q["absent_since"] or v["number"]
        conn.execute(
            "UPDATE qa_version_questions SET included=?,absent_since=?,updated_at=? WHERE version_id=? AND question_id=?",
            (data.included, absent, store.now(), v["id"], qid),
        )
        for child in family - {qid}:
            conn.execute(
                "UPDATE qa_version_questions SET included=?,absent_since=?,updated_at=? WHERE version_id=? AND question_id=?",
                (data.included, absent, store.now(), v["id"], child),
            )
        if data.included and not q["included"] and not data.restore_replies:
            for member in family:
                for c in conn.execute(
                    "SELECT column_id FROM qa_version_cells WHERE version_id=? AND question_id=?",
                    (v["id"], member),
                ).fetchall():
                    _save_reply(conn, v, member, c[0], "")
        if data.included and not selected.get("source_rows"):
            outline.append_question(meta["outline"], qid)
            outline.persist(conn, v["id"], meta)
    return detail(collection_id, lid)


def add_columns(collection_id, lid, data):
    with store.writing(collection_id, lid, data, "columns.create") as (conn, _, v):
        pos = conn.execute(
            "SELECT coalesce(max(position),-1)+1 FROM qa_version_columns WHERE version_id=?",
            (v["id"],),
        ).fetchone()[0]
        if pos + len(data.labels) > 100:
            store.fail("column_limit", 422)
        for i, label in enumerate(data.labels):
            _add_column(conn, lid, v["id"], label, pos + i)
    return detail(collection_id, lid)


def update_column(collection_id, lid, cid, data):
    with store.writing(collection_id, lid, data, "column.update") as (conn, _, v):
        c = _column(conn, v["id"], cid)
        if data.label is not None:
            if not data.label.strip():
                store.fail("column_label_required", 422)
            conn.execute(
                "UPDATE qa_version_columns SET label=? WHERE version_id=? AND column_id=?",
                (data.label.strip(), v["id"], cid),
            )
        if data.included is not None:
            absent = None if data.included else c["absent_since"] or v["number"]
            conn.execute(
                "UPDATE qa_version_columns SET included=?,absent_since=? WHERE version_id=? AND column_id=?",
                (data.included, absent, v["id"], cid),
            )
            if data.included and not c["included"] and not data.restore_replies:
                for q in conn.execute(
                    "SELECT question_id FROM qa_version_cells WHERE version_id=? AND column_id=?",
                    (v["id"], cid),
                ).fetchall():
                    _save_reply(conn, v, q[0], cid, "")
    return detail(collection_id, lid)


def _save_reply(conn, v, qid, cid, body):
    old = conn.execute(
        """SELECT c.*,t.body FROM qa_version_cells c JOIN qa_text_revisions t ON t.id=c.text_revision_id
        WHERE c.version_id=? AND c.question_id=? AND c.column_id=?""",
        (v["id"], qid, cid),
    ).fetchone()
    rid = store.text_revision(conn, qid, body, cid, old["text_revision_id"])
    historical = (
        old["text_revision_id"]
        if not body and old["body"]
        else old["historical_revision_id"]
        if not body
        else None
    )
    cleared = (old["cleared_since"] or v["number"]) if historical else None
    conn.execute(
        "UPDATE qa_version_cells SET text_revision_id=?,historical_revision_id=?,cleared_since=? WHERE version_id=? AND question_id=? AND column_id=?",
        (rid, historical, cleared, v["id"], qid, cid),
    )


def save_reply(collection_id, lid, qid, cid, data):
    with store.writing(collection_id, lid, data, "reply.save") as (conn, _, v):
        q = _question(conn, v["id"], qid)
        c = _column(conn, v["id"], cid)
        if not q["included"] or not c["included"]:
            store.fail("not_included")
        cell = conn.execute(
            "SELECT text_revision_id FROM qa_version_cells WHERE version_id=? AND question_id=? AND column_id=?",
            (v["id"], qid, cid),
        ).fetchone()
        if data.base_cell_revision_id and cell[0] != data.base_cell_revision_id:
            store.fail("stale_draft")
        draft = None
        if data.draft_revision is not None:
            draft = conn.execute(
                "SELECT * FROM qa_reply_drafts WHERE version_id=? AND question_id=? AND column_id=? AND actor_id=?",
                (v["id"], qid, cid, get_actor().id),
            ).fetchone()
            if (
                not draft
                or draft["draft_revision"] != data.draft_revision
                or draft["base_cell_revision_id"] != cell[0]
            ):
                store.fail("stale_draft")
            conn.execute(
                "DELETE FROM qa_reply_drafts WHERE version_id=? AND question_id=? AND column_id=? AND actor_id=?",
                (v["id"], qid, cid, get_actor().id),
            )
        _save_reply(conn, v, qid, cid, data.body)
        if draft:
            saved = conn.execute(
                "SELECT text_revision_id FROM qa_version_cells WHERE version_id=? AND question_id=? AND column_id=?",
                (v["id"], qid, cid),
            ).fetchone()[0]
            conn.execute(
                "INSERT OR IGNORE INTO qa_reply_sources VALUES(?,?)", (saved, draft["sources_json"])
            )
        conn.execute(
            "UPDATE qa_version_questions SET updated_at=? WHERE version_id=? AND question_id=?",
            (store.now(), v["id"], qid),
        )
    return detail(collection_id, lid)


def clone_version(conn, lid, old):
    metadata.save(conn, old["id"], metadata.read(conn, old["id"]))
    vid = store.new_id()
    conn.execute(
        "UPDATE qa_versions SET state='frozen',frozen_at=? WHERE id=?", (store.now(), old["id"])
    )
    conn.execute(
        "INSERT INTO qa_versions(id,list_id,number,state,parent_id,created_at) VALUES(?,?,?,'working',?,?)",
        (vid, lid, old["number"] + 1, old["id"], store.now()),
    )
    for table in (
        "qa_version_questions",
        "qa_version_columns",
        "qa_version_cells",
        "qa_annotations",
        "qa_version_metadata",
    ):
        columns = [
            r[1] for r in conn.execute(f"PRAGMA table_info({table})") if r[1] != "version_id"
        ]
        fields = ",".join(columns)
        conn.execute(
            f"INSERT INTO {table}(version_id,{fields}) SELECT ?,{fields} FROM {table} WHERE version_id=?",
            (vid, old["id"]),
        )
    conn.execute("UPDATE qa_lists SET current_version_id=? WHERE id=?", (vid, lid))
    return conn.execute("SELECT * FROM qa_versions WHERE id=?", (vid,)).fetchone()


def next_version(collection_id, lid, data):
    with store.writing(collection_id, lid, data, "version.create") as (conn, _, v):
        clone_version(conn, lid, v)
    return detail(collection_id, lid)


def annotation_key(conn, vid, qid):
    import hashlib

    ids = [
        r[0]
        for r in conn.execute(
            """SELECT c.text_revision_id FROM qa_version_cells c
        JOIN qa_version_columns v ON v.version_id=c.version_id AND v.column_id=c.column_id
        JOIN qa_text_revisions t ON t.id=c.text_revision_id
        WHERE c.version_id=? AND c.question_id=? AND v.included=1 AND t.body!='' ORDER BY v.position""",
            (vid, qid),
        )
    ]
    ids.insert(0, _question(conn, vid, qid)["text_revision_id"])
    return hashlib.sha256("|".join(ids).encode()).hexdigest()


def save_annotation(conn, vid, qid, data):
    options = metadata.read(conn, vid)["options"]["types"]
    value = data["type"]
    if not any(o["id"] == value for o in options):
        matches = [o["id"] for o in options if o["label"].casefold() == value.casefold()]
        value = matches[0] if len(matches) == 1 else None
    conn.execute(
        "INSERT OR REPLACE INTO qa_annotations VALUES(?,?,?,?,?,?)",
        (
            vid,
            qid,
            annotation_key(conn, vid, qid),
            data["summary"][:240],
            data["reply_summary"][:400],
            value or "",
        ),
    )
    if value is not None:
        conn.execute(
            "UPDATE qa_version_questions SET type=?,type_origin='ai' WHERE version_id=? AND question_id=? AND type_origin!='manual'",
            (value, vid, qid),
        )


def versions(collection_id, lid):
    with store.connection(collection_id) as conn:
        store.get_list(conn, lid)
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM qa_versions WHERE list_id=? ORDER BY number DESC", (lid,)
            )
        ]


def update_list(collection_id, lid, data):
    with store.writing(collection_id, lid, data, "list.update") as (conn, _, _v):
        if data.title is not None:
            if not data.title.strip():
                store.fail("title_required", 422)
            conn.execute("UPDATE qa_lists SET title=? WHERE id=?", (data.title.strip(), lid))
        if data.archived is not None:
            conn.execute("UPDATE qa_lists SET archived=? WHERE id=?", (data.archived, lid))
        if data.template_id is not None:
            if data.template_id:
                if not conn.execute(
                    "SELECT 1 FROM qa_templates WHERE id=?", (data.template_id,)
                ).fetchone():
                    store.fail("not_found", 404)
                conn.execute(
                    "INSERT OR REPLACE INTO qa_list_templates VALUES(?,?)", (lid, data.template_id)
                )
            else:
                conn.execute("DELETE FROM qa_list_templates WHERE list_id=?", (lid,))
    return detail(collection_id, lid)


def reply_history(collection_id, lid, qid, cid, version_id=None):
    with store.connection(collection_id) as conn:
        listing = store.get_list(conn, lid)
        vid = version_id or listing["current_version_id"]
        if not conn.execute(
            "SELECT 1 FROM qa_versions WHERE id=? AND list_id=?", (vid, lid)
        ).fetchone():
            store.fail("not_found", 404)
        _question(conn, vid, qid)
        _column(conn, vid, cid)
        cell = conn.execute(
            "SELECT text_revision_id FROM qa_version_cells WHERE version_id=? AND question_id=? AND column_id=?",
            (vid, qid, cid),
        ).fetchone()
        result, rid = [], cell[0] if cell else None
        while rid:
            r = conn.execute("SELECT * FROM qa_text_revisions WHERE id=?", (rid,)).fetchone()
            result.append(dict(r))
            rid = r["previous_id"]
        return result
