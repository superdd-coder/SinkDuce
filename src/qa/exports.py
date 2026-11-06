"""Immutable Excel export snapshots."""

import json

from . import excel, metadata, service, store
from .imports import asset_path
from .workbook_layout import write_original


def create(collection, lid, options):
    snapshot = service.detail(collection, lid)
    if not snapshot["can_edit"]:
        store.fail("read_only", 403)
    if snapshot["revision"] != options["expected_revision"]:
        store.fail("stale_revision")
    eid = store.new_id()
    with store.connection(collection) as conn:
        meta = metadata.read(conn, snapshot["version"]["id"])
        template = conn.execute(
            "SELECT t.settings_json FROM qa_templates t JOIN qa_list_templates l ON t.id=l.template_id WHERE l.list_id=?",
            (lid,),
        ).fetchone()
        options = {**(json.loads(template[0]) if template else {}), **options}
    options = {**options, "date": store.now()[:10]}
    logo = (
        asset_path(collection, "templates", "logos", options["logo_id"])
        if options.get("logo_id")
        else None
    )
    if meta["layout"]:
        original = asset_path(collection, lid, "imports", meta["layout"]["import_id"])
        if not original.is_file():
            store.fail("source_unavailable", 404)
        blob = write_original(original.read_bytes(), snapshot, meta)
    else:
        blob = excel.write_workbook(snapshot, options, logo)
    with store.connection(collection, "update") as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        store.get_list(conn, lid)
        path = asset_path(collection, lid, "exports", eid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
        conn.execute(
            "INSERT INTO qa_exports VALUES(?,?,?,?,?,?)",
            (
                eid,
                lid,
                snapshot["version"]["id"],
                json.dumps(snapshot, ensure_ascii=False),
                json.dumps(options),
                store.now(),
            ),
        )
    return {"id": eid, "version_id": snapshot["version"]["id"]}


def download(collection, lid, eid):
    with store.connection(collection) as conn:
        store.get_list(conn, lid)
        if not conn.execute(
            "SELECT 1 FROM qa_exports WHERE id=? AND list_id=?", (eid, lid)
        ).fetchone():
            store.fail("not_found", 404)
        path = asset_path(collection, lid, "exports", eid)
        if not path.is_file():
            store.fail("source_unavailable", 404)
        return path


def list_exports(collection, lid):
    with store.connection(collection) as conn:
        store.get_list(conn, lid)
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id,version_id,created_at FROM qa_exports WHERE list_id=? ORDER BY created_at DESC",
                (lid,),
            )
        ]
