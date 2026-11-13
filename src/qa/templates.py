"""Small named project templates; no arbitrary spreadsheet layout editor."""

import json
from io import BytesIO

from . import store
from .imports import asset_path


def list_templates(collection):
    with store.connection(collection) as conn:
        return [decode(r) for r in conn.execute("SELECT * FROM qa_templates ORDER BY name")]


def decode(row):
    settings = json.loads(row["settings_json"])
    settings.pop("include_type", None)
    return {
        **settings,
        "id": row["id"],
        "name": row["name"],
        "revision": row["revision"],
    }


def save(collection, data, tid=None):
    with store.connection(collection, "update") as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        old = (
            conn.execute("SELECT * FROM qa_templates WHERE id=?", (tid,)).fetchone()
            if tid
            else None
        )
        if tid and not old:
            store.fail("not_found", 404)
        if old and old["revision"] != data.expected_revision:
            store.fail("stale_revision")
        settings = json.loads(old["settings_json"]) if old else {}
        settings.pop("include_type", None)
        settings.update(data.model_dump(exclude_none=True, exclude={"name", "expected_revision"}))
        logo_id = settings.get("logo_id")
        if logo_id and not asset_path(collection, "templates", "logos", logo_id).is_file():
            store.fail("not_found", 404)
        tid = tid or store.new_id()
        conn.execute(
            "INSERT INTO qa_templates VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,settings_json=excluded.settings_json,revision=excluded.revision,updated_at=excluded.updated_at",
            (
                tid,
                data.name.strip(),
                json.dumps(settings, ensure_ascii=False),
                old["revision"] + 1 if old else 1,
                store.now(),
            ),
        )
        return decode(conn.execute("SELECT * FROM qa_templates WHERE id=?", (tid,)).fetchone())


def upload_logo(collection, raw):
    from PIL import Image

    if len(raw) > 3 * 1024 * 1024:
        store.fail("parse_limit_exceeded", 413)
    try:
        with Image.open(BytesIO(raw)) as im:
            if im.format not in {"PNG", "JPEG"} or im.width * im.height > 16_000_000:
                raise ValueError("invalid_logo")
            im.verify()
    except (OSError, ValueError, Image.DecompressionBombError):
        store.fail("invalid_logo", 422)
    with store.connection(collection, "update"):
        lid = store.new_id()
        path = asset_path(collection, "templates", "logos", lid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return {"id": lid}
