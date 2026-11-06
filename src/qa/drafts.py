"""Per-actor, per-version drafts, separate from saved replies and list revisions."""

import hashlib
import json

from src.identity import get_actor

from . import service, store


def context_key(conn, vid, qid):
    columns = [
        list(row)
        for row in conn.execute(
            """SELECT column_id,label,included,position FROM qa_version_columns
            WHERE version_id=? ORDER BY position,column_id""",
            (vid,),
        )
    ]
    payload = json.dumps([service.annotation_key(conn, vid, qid), columns], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def cell(conn, lid, vid, qid, cid, *, editable=False):
    listing = store.get_list(conn, lid)
    if not conn.execute(
        "SELECT 1 FROM qa_versions WHERE id=? AND list_id=?", (vid, lid)
    ).fetchone():
        store.fail("not_found", 404)
    q, c = service._question(conn, vid, qid), service._column(conn, vid, cid)
    if editable and (
        vid != listing["current_version_id"]
        or listing["archived"]
        or not q["included"]
        or not c["included"]
    ):
        store.fail("read_only")
    return conn.execute(
        "SELECT * FROM qa_version_cells WHERE version_id=? AND question_id=? AND column_id=?",
        (vid, qid, cid),
    ).fetchone()


def read(collection, lid, vid, qid, cid):
    with store.connection(collection) as conn:
        cell(conn, lid, vid, qid, cid)
        row = conn.execute(
            "SELECT * FROM qa_reply_drafts WHERE version_id=? AND question_id=? AND column_id=? AND actor_id=?",
            (vid, qid, cid, get_actor().id),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["sources"] = json.loads(data.pop("sources_json"))
        return data


def put(collection, lid, qid, cid, data):
    with store.connection(collection, "update") as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        current = cell(conn, lid, data.version_id, qid, cid, editable=True)
        if current["text_revision_id"] != data.base_cell_revision_id:
            store.fail("stale_draft")
        old = conn.execute(
            "SELECT * FROM qa_reply_drafts WHERE version_id=? AND question_id=? AND column_id=? AND actor_id=?",
            (data.version_id, qid, cid, get_actor().id),
        ).fetchone()
        if (old["draft_revision"] if old else 0) != data.draft_revision:
            store.fail("stale_draft")
        sources = old["sources_json"] if old else "[]"
        context_hash = old["context_hash"] if old else ""
        if data.proposal_id:
            proposal = conn.execute(
                """SELECT * FROM qa_draft_proposals WHERE id=? AND version_id=? AND question_id=?
                AND column_id=? AND actor_id=?""",
                (data.proposal_id, data.version_id, qid, cid, get_actor().id),
            ).fetchone()
            if not proposal or proposal["context_hash"] != context_key(conn, data.version_id, qid):
                store.fail("stale_draft")
            sources = json.dumps(json.loads(proposal["result_json"])["sources"], ensure_ascii=False)
            context_hash = proposal["context_hash"]
        conn.execute(
            """INSERT INTO qa_reply_drafts VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(version_id,question_id,column_id,actor_id) DO UPDATE SET
            body=excluded.body,draft_revision=excluded.draft_revision,sources_json=excluded.sources_json,
            context_hash=excluded.context_hash,updated_at=excluded.updated_at""",
            (
                data.version_id,
                qid,
                cid,
                get_actor().id,
                data.body,
                data.draft_revision + 1,
                data.base_cell_revision_id,
                sources,
                context_hash,
                store.now(),
            ),
        )
    return read(collection, lid, data.version_id, qid, cid)


def discard(collection, lid, vid, qid, cid, revision):
    with store.connection(collection, "update") as conn, conn:
        cell(conn, lid, vid, qid, cid, editable=True)
        count = conn.execute(
            "DELETE FROM qa_reply_drafts WHERE version_id=? AND question_id=? AND column_id=? AND actor_id=? AND draft_revision=?",
            (vid, qid, cid, get_actor().id, revision),
        ).rowcount
        if not count:
            store.fail("stale_draft")
    return {"ok": True}
