"""Confirmed, list-scoped removal; shared templates and project files survive."""

import logging
import shutil

from . import imports, store

logger = logging.getLogger(__name__)


def remove(collection, lid, data):
    from src.services import services

    staged = None
    root = imports.asset_path(collection, lid, "imports", "probe").parent.parent
    try:
        with store.connection(collection, "delete") as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            listing = store.get_list(conn, lid)
            if listing["revision"] != data.expected_revision:
                store.fail("stale_revision")
            if listing["title"] != data.confirm_title:
                store.fail("confirmation_mismatch")
            has_materials = conn.execute(
                "SELECT 1 FROM qa_materials WHERE list_id=?",
                (lid,),
            ).fetchone()
            versions = "SELECT id FROM qa_versions WHERE list_id=?"
            questions = "SELECT id FROM qa_questions WHERE list_id=?"
            texts = f"SELECT id FROM qa_text_revisions WHERE question_id IN ({questions})"
            # Delete children before owners, retaining the ownership subqueries until last.
            scopes = [
                ("qa_revoked_associations", f"text_revision_id IN ({texts})"),
                ("qa_reply_sources", f"text_revision_id IN ({texts})"),
                *[
                    (table, f"version_id IN ({versions})")
                    for table in (
                        "qa_draft_proposals",
                        "qa_reply_drafts",
                        "qa_annotations",
                        "qa_version_cells",
                        "qa_version_questions",
                        "qa_version_columns",
                        "qa_version_metadata",
                    )
                ],
                ("qa_text_revisions", f"question_id IN ({questions})"),
                (
                    "qa_material_versions",
                    "material_id IN (SELECT id FROM qa_materials WHERE list_id=?)",
                ),
                *[
                    (table, "list_id=?")
                    for table in (
                        "qa_materials",
                        "qa_imports",
                        "qa_exports",
                        "qa_events",
                        "qa_list_templates",
                        "qa_correction_previews",
                        "qa_questions",
                        "qa_columns",
                        "qa_versions",
                    )
                ],
                ("qa_lists", "id=?"),
            ]
            for table, scope in scopes:
                conn.execute(f"DELETE FROM {table} WHERE {scope}", (lid,))
            if conn.execute("PRAGMA foreign_key_check").fetchone():
                store.fail("delete_integrity_error", 500)
            # Move assets reversibly until the database transaction commits.
            if root.exists():
                staged = root.with_name(f"removed-{store.new_id()}")
                root.rename(staged)
            if has_materials:
                try:
                    if services.db is None:
                        raise RuntimeError("index_unavailable")
                    services.db.delete_by_filter(collection, "qa_list_id", lid)
                except Exception:  # noqa: BLE001 — roll back when the external index is unavailable
                    store.fail("delete_index_unavailable", 503)
    except BaseException:
        if staged is not None and staged.exists():
            staged.rename(root)
        raise
    cleanup_pending = False
    if staged is not None:
        try:
            shutil.rmtree(staged)
        except OSError:
            # Data is no longer accessible through the API; report incomplete disk cleanup.
            logger.exception("Q&A asset cleanup incomplete")
            cleanup_pending = True
    return {"ok": True, "cleanup_pending": cleanup_pending}
