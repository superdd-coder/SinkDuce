"""Private actor drafts survive import versions without carrying stale AI proposals."""

import json
import sqlite3

import pytest

from src.qa import store


@pytest.fixture
def draft_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    for sql in store.SCHEMA:
        conn.execute(sql)
    conn.execute("INSERT INTO qa_lists(id,title,current_version_id,created_by,created_at,updated_at) VALUES('list-example','Example list','new-version','actor-one','earlier','earlier')")
    for vid, number, state in [("old-version", 1, "frozen"), ("new-version", 2, "working")]:
        conn.execute("INSERT INTO qa_versions(id,list_id,number,state,created_at) VALUES(?,'list-example',?,?, 'earlier')", (vid, number, state))
    for qid in ["q-one", "q-two"]:
        conn.execute("INSERT INTO qa_questions VALUES(?,'list-example','old-version')", (qid,))
        conn.execute("INSERT INTO qa_text_revisions VALUES(?,?,NULL,'Describe the cabinet.',NULL,'actor-one','earlier')", (f"text-{qid}", qid))
        for vid in ["old-version", "new-version"]:
            conn.execute("INSERT INTO qa_version_questions(version_id,question_id,text_revision_id,included,position,status,type,updated_at) VALUES(?,?,?,?,0,'waiting','Clarify','earlier')", (vid, qid, f"text-{qid}", int(vid == "old-version" or qid == "q-one")))
    for cid in ["c-one", "c-two"]:
        conn.execute("INSERT INTO qa_columns VALUES(?,'list-example','old-version')", (cid,))
        for vid in ["old-version", "new-version"]:
            conn.execute("INSERT INTO qa_version_columns VALUES(?,?,?, ?,0,NULL)", (vid, cid, f"Reply {cid}", int(vid == "old-version" or cid == "c-one")))
            for qid in ["q-one", "q-two"]:
                rid = f"{vid}-{qid}-{cid}"
                body = "The cabinet is ready." if vid == "new-version" else "Earlier saved reply."
                conn.execute("INSERT INTO qa_text_revisions VALUES(?,?,?,?,NULL,'actor-one','earlier')", (rid, qid, cid, body))
                conn.execute("INSERT INTO qa_version_cells(version_id,question_id,column_id,text_revision_id) VALUES(?,?,?,?)", (vid, qid, cid, rid))
    sources = json.dumps([{"kind": "project", "file_id": "file-example", "version_id": "file-version", "filename": "example.txt"}])
    for qid, cid, actor, body, revision, updated in [
        ("q-one", "c-one", "actor-one", "First actor's cabinet draft.", 3, "original-time"),
        ("q-one", "c-one", "actor-two", "The cabinet is ready.", 7, ""),
        ("q-one", "c-one", "actor-blank", " \t\n ", 1, "original-time"),
        ("q-two", "c-one", "actor-one", "Draft for an omitted question.", 2, "original-time"),
        ("q-one", "c-two", "actor-one", "Draft for an omitted column.", 2, "original-time"),
    ]:
        conn.execute("INSERT INTO qa_reply_drafts VALUES('old-version',?,?,?,?,?,?,?,?,?)", (qid, cid, actor, body, revision, f"old-version-{qid}-{cid}", sources, "stale-context", updated))
    conn.execute("INSERT INTO qa_draft_proposals VALUES('proposal-example','old-version','q-one','c-one','actor-one','stale-context','{}','earlier')")
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def rows(conn, table, vid):
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table} WHERE version_id=? ORDER BY actor_id,question_id,column_id", (vid,))]


def test_import_carries_each_actors_nonblank_draft_with_new_base_and_preserved_sources(draft_db, monkeypatch):
    from src.qa.import_drafts import carry_forward

    monkeypatch.setattr(store, "now", lambda: "fallback-time")
    old = rows(draft_db, "qa_reply_drafts", "old-version")
    with draft_db:
        carry_forward(draft_db, "old-version", "new-version")
    carried = rows(draft_db, "qa_reply_drafts", "new-version")
    assert [draft["actor_id"] for draft in carried] == ["actor-one", "actor-two"]
    originals = {draft["actor_id"]: draft for draft in old if draft["question_id"] == "q-one" and draft["column_id"] == "c-one"}
    for draft in carried:
        before = originals[draft["actor_id"]]
        assert draft["body"] == before["body"]
        assert draft["sources_json"] == before["sources_json"]
        assert draft["draft_revision"] == before["draft_revision"]
        assert draft["base_cell_revision_id"] == "new-version-q-one-c-one"
        assert draft["context_hash"] == ""
        assert draft["updated_at"] == (before["updated_at"] or "fallback-time")
    assert rows(draft_db, "qa_reply_drafts", "old-version") == old
    assert rows(draft_db, "qa_draft_proposals", "new-version") == []
    assert len(rows(draft_db, "qa_draft_proposals", "old-version")) == 1
    assert draft_db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_repeated_copy_is_idempotent_and_cannot_overwrite_a_newer_current_draft(draft_db):
    from src.qa.import_drafts import carry_forward

    draft_db.execute("INSERT INTO qa_reply_drafts VALUES('new-version','q-one','c-one','actor-one','Newer current draft.',11,'new-version-q-one-c-one','[]','current-context','current-time')")
    draft_db.commit()
    before = rows(draft_db, "qa_reply_drafts", "new-version")[0]
    with draft_db:
        carry_forward(draft_db, "old-version", "new-version")
        carry_forward(draft_db, "old-version", "new-version")
    after = rows(draft_db, "qa_reply_drafts", "new-version")
    assert len(after) == 2
    assert after[0] == before


def test_draft_copy_rolls_back_with_its_import_transaction(draft_db):
    from src.qa.import_drafts import carry_forward

    before = rows(draft_db, "qa_reply_drafts", "old-version")
    with pytest.raises(RuntimeError, match="abort import"), draft_db:
        carry_forward(draft_db, "old-version", "new-version")
        raise RuntimeError("abort import")
    assert rows(draft_db, "qa_reply_drafts", "new-version") == []
    assert rows(draft_db, "qa_reply_drafts", "old-version") == before
