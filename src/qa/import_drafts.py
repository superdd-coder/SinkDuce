"""Carry private reply drafts into a finalized import version inside its transaction."""

from . import store


def carry_forward(conn, old_vid, new_vid):
    """Retain actor ownership and draft content while rebasing onto the imported cells."""
    if not old_vid or old_vid == new_vid:
        return 0
    rows = conn.execute(
        """SELECT d.question_id,d.column_id,d.actor_id,d.body,d.draft_revision,
        d.sources_json,d.updated_at,c.text_revision_id
        FROM qa_reply_drafts d
        JOIN qa_version_cells c ON c.version_id=? AND c.question_id=d.question_id
            AND c.column_id=d.column_id
        JOIN qa_version_questions q ON q.version_id=c.version_id
            AND q.question_id=c.question_id AND q.included=1
        JOIN qa_version_columns v ON v.version_id=c.version_id
            AND v.column_id=c.column_id AND v.included=1
        WHERE d.version_id=?""",
        (new_vid, old_vid),
    ).fetchall()
    copied = 0
    for qid, cid, actor, body, revision, sources, updated, base in rows:
        if not body.strip():
            continue
        copied += conn.execute(
            """INSERT INTO qa_reply_drafts(
            version_id,question_id,column_id,actor_id,body,draft_revision,
            base_cell_revision_id,sources_json,context_hash,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(version_id,question_id,column_id,actor_id) DO NOTHING""",
            (new_vid, qid, cid, actor, body, revision, base, sources, "", updated or store.now()),
        ).rowcount
    return copied
