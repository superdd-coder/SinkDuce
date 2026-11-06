"""Permanent list removal is scoped, confirmed and atomic."""

import uuid

import pytest
from fastapi import HTTPException
from qdrant_client import models
from qdrant_client.qdrant_client import QdrantClient

from tests.test_qa import create, mutate, qa_fixture  # noqa: F401
from tests.test_qa_excel import commit, upload


def remove(qa, listing, **overrides):
    client, base = qa
    return client.request(
        "DELETE",
        f"{base}/lists/{listing['id']}",
        json={
            "expected_revision": listing["revision"],
            "confirm_title": listing["title"],
            **overrides,
        },
    )


def test_delete_list_removes_all_versions_assets_and_only_its_index(qa, monkeypatch):
    from src.db.qdrant import QdrantManager
    from src.qa import materials, store
    from src.qa.imports import asset_path
    from src.services import services

    client, base = qa
    other = create(qa)
    listing = create(qa)
    listing = commit(
        qa,
        listing,
        upload(
            qa,
            listing,
            [
                ["Question", "Reply"],
                ["Describe the fictional garden gate.", "Painted blue."],
            ],
        ),
    )
    listing = mutate(qa, listing, "/versions", {})
    question = listing["questions"][0]
    reply = next(r for r in question["replies"] if r["included"])
    listing = mutate(
        qa,
        listing,
        f"/questions/{question['id']}",
        {"text": "Describe the invented garden gate hinges."},
        "patch",
    )
    path = f"{base}/lists/{listing['id']}"
    draft = client.put(
        f"{path}/questions/{question['id']}/drafts/{reply['column_id']}",
        json={
            "version_id": listing["version"]["id"],
            "body": "Draft inspection note.",
            "draft_revision": 0,
            "base_cell_revision_id": reply["text_revision_id"],
        },
    )
    assert draft.status_code == 200
    assert (
        client.post(f"{path}/exports", json={"expected_revision": listing["revision"]}).status_code
        == 200
    )
    with store.connection("col_qa", "update") as conn, conn:
        # References outside the visible question list must not block removal.
        preview_id = store.new_id()
        conn.execute(
            "INSERT INTO qa_correction_previews VALUES(?,?,?,?)",
            (preview_id, listing["id"], "{}", store.now()),
        )
        conn.execute(
            "INSERT INTO qa_revoked_associations VALUES(?,?)",
            (reply["text_revision_id"], preview_id),
        )
        conn.execute("INSERT INTO qa_reply_sources VALUES(?,?)", (reply["text_revision_id"], "[]"))
        conn.execute(
            "INSERT INTO qa_draft_proposals VALUES(?,?,?,?,?,?,?,?)",
            (
                store.new_id(),
                listing["version"]["id"],
                question["id"],
                reply["column_id"],
                "test",
                "context",
                "{}",
                store.now(),
            ),
        )
        conn.execute(
            "INSERT INTO qa_annotations VALUES(?,?,?,?,?,?)",
            (
                listing["version"]["id"],
                question["id"],
                "context",
                "Garden summary",
                "Reply summary",
                "Other",
            ),
        )
        template_id = store.new_id()
        conn.execute(
            "INSERT INTO qa_templates VALUES(?,?,?,1,?)",
            (template_id, "Shared example template", "{}", store.now()),
        )
        conn.execute("INSERT INTO qa_list_templates VALUES(?,?)", (listing["id"], template_id))
        conn.execute("INSERT INTO qa_list_templates VALUES(?,?)", (other["id"], template_id))
    materials.upload("col_qa", listing["id"], "guide.txt", b"Invented garden guide.")
    other_asset = asset_path("col_qa", other["id"], "imports", "example")
    other_asset.parent.mkdir(parents=True)
    other_asset.write_bytes(b"Other list source")

    db = QdrantManager.__new__(QdrantManager)
    db.client = QdrantClient(":memory:")
    db.client.create_collection(
        "col_qa", vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE)
    )
    ids = [str(uuid.uuid4()) for _ in range(3)]
    db.client.upsert(
        "col_qa",
        [
            models.PointStruct(id=pid, vector=[1.0, 0.0], payload=payload)
            for pid, payload in zip(
                ids,
                [
                    {"source_kind": "qa_material", "qa_list_id": listing["id"]},
                    {"source_kind": "qa_material", "qa_list_id": other["id"]},
                    {"source_kind": "project"},
                ],
            )
        ],
    )
    monkeypatch.setattr(services, "db", db)
    try:
        response = remove(qa, listing)
        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True, "cleanup_pending": False}
        assert client.get(path).status_code == 404
        assert client.get(f"{base}/lists/{other['id']}").status_code == 200
        assert [row["id"] for row in client.get(base + "/lists").json()] == [other["id"]]
        assert not other_asset.parents[1].with_name(listing["id"]).exists()
        assert other_asset.read_bytes() == b"Other list source"
        assert {p.id for p in db.client.scroll("col_qa")[0]} == set(ids[1:])
        with store.connection("col_qa") as conn:
            assert not conn.execute("PRAGMA foreign_key_check").fetchall()
            assert conn.execute("SELECT count(*) FROM qa_imports").fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM qa_reply_drafts").fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM qa_material_versions").fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM qa_templates").fetchone()[0] == 1
            assert (
                conn.execute("SELECT list_id FROM qa_list_templates").fetchone()[0] == other["id"]
            )
    finally:
        db.client.close()


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"confirm_title": "Wrong list"}, "confirmation_mismatch"),
        ({"expected_revision": 99}, "stale_revision"),
    ],
)
def test_delete_requires_exact_confirmation_and_current_revision(qa, overrides, code):
    listing = create(qa)
    response = remove(qa, listing, **overrides)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == code
    assert qa[0].get(f"{qa[1]}/lists/{listing['id']}").status_code == 200


def test_delete_requires_write_permission(qa, monkeypatch):
    from src.qa import store

    listing = create(qa)
    original = store.authorize

    def authorize(actor, action, resource):
        if action == "qa.delete":
            raise HTTPException(403)
        return original(actor, action, resource)

    monkeypatch.setattr(store, "authorize", authorize)
    assert remove(qa, listing).status_code == 403
    assert qa[0].get(f"{qa[1]}/lists/{listing['id']}").status_code == 200


def test_index_failure_preserves_list_and_files(qa, monkeypatch):
    from src.qa import materials
    from src.qa.imports import asset_path
    from src.services import services

    listing = create(qa)
    material = materials.upload("col_qa", listing["id"], "guide.txt", b"Invented guide.")
    source = (
        asset_path("col_qa", listing["id"], "materials", material["current_version_id"])
        / "blob.txt"
    )

    class UnavailableIndex:
        def delete_by_filter(self, *args):
            raise ConnectionError("Unavailable test index")

    monkeypatch.setattr(services, "db", UnavailableIndex())
    assert remove(qa, listing).status_code == 503
    assert qa[0].get(f"{qa[1]}/lists/{listing['id']}").status_code == 200
    assert source.read_bytes() == b"Invented guide."


def test_archived_and_empty_import_lists_can_be_deleted(qa):
    listing = mutate(qa, create(qa), "", {"archived": True}, "patch")
    assert remove(qa, listing).status_code == 200
    empty = qa[0].post(qa[1] + "/lists", json={"title": "Empty import", "mode": "import"}).json()
    assert remove(qa, empty).status_code == 200


def test_queued_material_task_does_not_recreate_deleted_data(qa, monkeypatch):
    from src.qa import materials
    from src.services import services

    listing = create(qa)
    material = materials.upload("col_qa", listing["id"], "guide.txt", b"Invented guide.")

    class EmptyIndex:
        def delete_by_filter(self, *args):
            return 0

    monkeypatch.setattr(services, "db", EmptyIndex())
    assert remove(qa, listing).status_code == 200
    materials.process("col_qa", listing["id"], material["id"], material["current_version_id"])
    assert qa[0].get(f"{qa[1]}/lists/{listing['id']}").status_code == 404


def test_material_finishing_parse_after_deletion_cannot_restore_content(qa, monkeypatch):
    from src.parsers import ParsedDocument
    from src.qa import materials, store
    from src.services import services

    listing = create(qa)
    material = materials.upload("col_qa", listing["id"], "guide.txt", b"Invented garden guide.")

    class LegacyIndex:
        def delete_by_filter(self, *args):
            return 0

    def parse_after_deletion(path):
        content = path.read_text()
        assert remove(qa, listing).status_code == 200
        return ParsedDocument(content=content)

    monkeypatch.setattr(services, "db", LegacyIndex())
    monkeypatch.setattr("src.parsers.parse_file", parse_after_deletion)
    materials.process("col_qa", listing["id"], material["id"], material["current_version_id"])
    assert qa[0].get(f"{qa[1]}/lists/{listing['id']}").status_code == 404
    with store.connection("col_qa") as conn:
        assert not conn.execute(
            "SELECT 1 FROM qa_material_versions WHERE id=?", (material["version_id"],)
        ).fetchone()
