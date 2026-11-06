"""Shared materials, immutable source versions and retrieval isolation."""

import uuid

import pytest

from tests.test_qa import create, mutate, qa_fixture  # noqa: F401


def test_material_space_is_shared_and_old_blob_stays_addressable(qa, monkeypatch):
    from src.services import services

    monkeypatch.setattr(services, "db", None)
    client, base = qa
    d = create(qa)
    path = f"{base}/lists/{d['id']}/materials"
    r = client.post(path, files={"file": ("guide.txt", b"Fictional garden guide version one.")})
    assert r.status_code == 200, r.text
    material = r.json()
    first = material["current_version_id"]
    d = mutate(qa, d, "/versions", {})
    assert client.get(path).json()[0]["id"] == material["id"]
    r = client.post(
        f"{path}/{material['id']}/versions",
        files={"file": ("guide.txt", b"Fictional garden guide version two.")},
        data={"expected_revision": 1},
    )
    assert r.status_code == 200, r.text
    assert r.json()["current_version_id"] != first
    original = f"{path}/{material['id']}/versions/{first}/file"
    assert client.get(original).content == b"Fictional garden guide version one."
    other = create(qa)
    assert client.get(original.replace(d["id"], other["id"])).status_code == 404
    assert client.get(path).json()[0]["parse_state"] == "ready"


@pytest.mark.parametrize("mode", ["search", "hybrid", "scroll", "ids"])
def test_project_retrieval_excludes_other_list_materials_before_llm(mode):
    from qdrant_client import models
    from qdrant_client.qdrant_client import QdrantClient

    from src.db.qdrant import QdrantManager

    db = QdrantManager.__new__(QdrantManager)
    db.client = QdrantClient(":memory:")
    db.client.create_collection(
        "col_scope",
        vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE),
        sparse_vectors_config={"sparse": models.SparseVectorParams()},
    )
    ids = [str(uuid.uuid4()) for _ in range(4)]
    payloads = [
        {"text": "Project common"},
        {
            "text": "First list",
            "source_kind": "qa_material",
            "qa_list_id": "first",
            "is_current": True,
        },
        {
            "text": "Other list",
            "source_kind": "qa_material",
            "qa_list_id": "second",
            "is_current": True,
        },
        {
            "text": "Old version",
            "source_kind": "qa_material",
            "qa_list_id": "first",
            "is_current": False,
        },
    ]
    db.client.upsert(
        "col_scope",
        [
            models.PointStruct(
                id=pid,
                vector={"": [1.0, 0.0], "sparse": models.SparseVector(indices=[1], values=[1.0])},
                payload=p,
            )
            for pid, p in zip(ids, payloads)
        ],
    )

    def retrieve():
        if mode == "search":
            return db.search("col_scope", [1.0, 0.0], 10)
        if mode == "hybrid":
            return db.hybrid_search("col_scope", [1.0, 0.0], {1: 1.0}, 10)
        if mode == "scroll":
            return db.scroll_points("col_scope")[0]
        return db.get_points_by_ids("col_scope", ids)

    assert {r["payload"]["text"] for r in retrieve()} == {"Project common"}
    from src.rag.source_scope import qa_source_scope

    with qa_source_scope("first"):
        assert {r["payload"]["text"] for r in retrieve()} == {"Project common", "First list"}
    db.client.close()


def test_material_parse_never_uses_vector_services_and_preserves_page_provenance(qa, monkeypatch):
    from src.parsers import ParsedDocument
    from src.qa import materials
    from src.services import services

    calls = []

    class Forbidden:
        def __getattr__(self, name):
            calls.append(name)
            raise AssertionError("Material parsing does not use vector services")

    first = "A fictional inventory of storage shelves."
    second = "The greenhouse irrigation schedule requires a daily inspection."
    monkeypatch.setattr(services, "db", Forbidden())
    monkeypatch.setattr(services, "embedding", Forbidden())
    monkeypatch.setattr(
        "src.parsers.parse_file",
        lambda path: ParsedDocument(
            content=first + "\n\n" + second,
            position_map=[
                {"char_offset": 0, "type": "page", "page_number": 1},
                {"char_offset": len(first) + 2, "type": "page", "page_number": 2},
            ],
        ),
    )
    d = create(qa)
    client, base = qa
    path = f"{base}/lists/{d['id']}/materials"
    r = client.post(
        path,
        files={"file": ("manual.pdf", b"Fictional document bytes")},
    )
    assert r.status_code == 200, r.text
    m = client.get(path).json()[0]
    assert m["parse_state"] == "ready" and calls == []
    results, missing = materials.search_references(
        "col_qa", d["id"], "greenhouse irrigation schedule", allow_auto=True
    )
    assert missing == []
    assert [(s["version_id"], s["page_number"], s["excerpt"]) for s in results] == [
        (m["version_id"], 2, second)
    ]


def test_upload_reuses_exact_versions_and_never_rewinds_the_current_version(qa):
    client, base = qa
    listing, other = create(qa), create(qa)
    path = f"{base}/lists/{listing['id']}/materials"

    def upload(body, url=path):
        response = client.post(url, files={"file": ("manual.txt", body)})
        assert response.status_code == 200, response.text
        return response.json()

    first = upload(b"Fictional instructions for the display room.")
    same = upload(b"Fictional instructions for the display room.")
    second = upload(b"Revised instructions for the display room.")
    older = upload(b"Fictional instructions for the display room.")
    assert same["id"] == first["id"] == second["id"] == older["id"]
    assert same["version_id"] == first["version_id"] == older["version_id"]
    assert second["version_id"] == older["current_version_id"] != older["version_id"]
    assert older["number"] == 1 and older["parse_state"] == "ready"
    assert older["revision"] == second["revision"] == 2
    assert client.get(path).json()[0]["number"] == 2
    version_path = f"{path}/{first['id']}/versions/{first['version_id']}"
    assert client.get(version_path).json() == older
    assert not ({"content", "content_hash", "metadata_json"} & older.keys())
    assert client.get(version_path.replace(listing["id"], other["id"])).status_code == 404
    foreign = upload(
        b"Fictional instructions for the display room.", f"{base}/lists/{other['id']}/materials"
    )
    assert foreign["id"] != first["id"] and foreign["version_id"] != first["version_id"]


def test_legacy_same_name_objects_require_explicit_destination_for_new_bytes(qa):
    from src.qa import materials

    listing = create(qa)
    client, base = qa
    path = f"{base}/lists/{listing['id']}/materials"
    a = materials.upload("col_qa", listing["id"], "manual.txt", b"First fictional manual")
    b = materials.upload("col_qa", listing["id"], "other.txt", b"Second fictional manual")
    renamed = materials.upload(
        "col_qa", listing["id"], "manual.txt", b"Another fictional manual", b["id"], b["revision"]
    )
    response = client.post(path, files={"file": ("manual.txt", b"Unseen fictional manual")})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ambiguous_material_name"
    same = client.post(path, files={"file": ("manual.txt", b"First fictional manual")})
    assert same.status_code == 200 and same.json()["id"] == a["id"]
    assert {m["id"]: m["current_version_id"] for m in client.get(path).json()} == {
        a["id"]: a["current_version_id"],
        b["id"]: renamed["current_version_id"],
    }


def test_canonically_equivalent_upload_names_reuse_legacy_filename(qa):
    import unicodedata

    from src.qa import materials, store

    lid = create(qa)["id"]
    name = "Café operations.txt"
    raw = b"Fictional gallery entrance instructions."
    first = materials.upload("col_qa", lid, name, raw)
    with store.connection("col_qa", "update") as conn, conn:
        conn.execute(
            "UPDATE qa_material_versions SET filename=? WHERE id=?",
            (unicodedata.normalize("NFD", name), first["version_id"]),
        )
    same = materials.upload("col_qa", lid, name, raw)
    next_version = materials.upload(
        "col_qa", lid, name, b"Updated fictional entrance instructions."
    )
    assert same["id"] == first["id"] == next_version["id"]
    assert same["version_id"] == first["version_id"] != next_version["version_id"]
    assert next_version["number"] == 2


def test_retry_can_process_failed_old_version_without_changing_current_or_archiving(
    qa, monkeypatch
):
    from src.parsers import ParsedDocument
    from src.qa import materials

    should_fail = True

    def parse(path):
        if should_fail:
            raise ValueError("unreadable")
        return ParsedDocument(content=path.read_text())

    monkeypatch.setattr("src.parsers.parse_file", parse)
    client, base = qa
    lid = create(qa)["id"]
    path = f"{base}/lists/{lid}/materials"
    first = client.post(path, files={"file": ("manual.txt", b"Fictional old manual")}).json()
    should_fail = False
    second = client.post(path, files={"file": ("manual.txt", b"Fictional new manual")}).json()
    versions = f"{path}/{first['id']}/versions"
    old_path = f"{versions}/{first['version_id']}"
    assert client.get(old_path).json()["parse_state"] == "failed"
    real_process = materials.process
    jobs = []
    monkeypatch.setattr(materials, "process", lambda *args, **kwargs: jobs.append(args))
    r = client.post(f"{path}/{first['id']}/retry", params={"version_id": first["version_id"]})
    assert r.status_code == 200 and r.json()["version_id"] == first["version_id"]
    assert client.get(old_path).json()["parse_state"] == "queued"
    assert jobs == [("col_qa", lid, first["id"], first["version_id"])]
    real_process(*jobs[0])
    old = client.get(old_path).json()
    assert old["parse_state"] == "ready" and old["current_version_id"] == second["version_id"]
    r = client.patch(
        f"{path}/{first['id']}", json={"expected_revision": second["revision"], "archived": True}
    )
    assert r.status_code == 200
    assert client.get(old_path).json()["archived"]
    assert client.get(old_path + "/file").content == b"Fictional old manual"


def test_material_search_scopes_exact_refs_names_and_auto_relevance(qa):
    from fastapi import HTTPException

    from src.qa import materials, store

    client, base = qa
    lid, other = create(qa)["id"], create(qa)["id"]

    def upload(target, filename, body):
        response = client.post(
            f"{base}/lists/{target}/materials", files={"file": (filename, body.encode())}
        )
        assert response.status_code == 200, response.text
        return response.json()

    old = upload(lid, "greenhouse.txt", "The greenhouse ventilation schedule starts on Tuesday.")
    current = upload(lid, "greenhouse.txt", "The greenhouse ventilation schedule starts on Friday.")
    cn = upload(lid, "温室记录.txt", "温室灌溉安排在每天清晨，工作人员负责检查阀门。")
    foreign = upload(
        other, "foreign.txt", "The greenhouse ventilation schedule starts on Saturday."
    )
    unrelated = upload(lid, "cabinet.txt", "A display cabinet contains sample ceramic pieces.")
    # Legacy ready parses remain searchable even if their old vector indexing failed.
    with store.connection("col_qa", "update") as conn, conn:
        conn.execute(
            "UPDATE qa_material_versions SET index_state='failed' WHERE id=?",
            (current["version_id"],),
        )

    def search(query, **kwargs):
        return materials.search_references("col_qa", lid, query, **kwargs)

    assert search("greenhouse ventilation") == ([], [])
    found, missing = search("greenhouse ventilation", allow_auto=True)
    assert missing == [] and [s["version_id"] for s in found] == [current["version_id"]]
    assert [s["version_id"] for s in search("请问温室灌溉安排", allow_auto=True)[0]] == [
        cn["version_id"]
    ]
    assert search("Provide the waterfall repair timetable", allow_auto=True) == ([], [])
    assert search("greenhouse ventilation", names=["Absent.txt"], allow_auto=True) == (
        [],
        ["Absent.txt"],
    )
    named, missing = search(
        "greenhouse ventilation", names=["GREENHOUSE", "Absent.txt"], allow_auto=True
    )
    assert missing == ["Absent.txt"] and [s["version_id"] for s in named] == [current["version_id"]]
    refs = materials.resolve_references("col_qa", lid, [old["version_id"], old["version_id"]])
    assert len(refs) == 1
    attached, _ = search("something unrelated", versions=refs, allow_auto=True)
    assert [(s["kind"], s["file_id"], s["version_id"], s["excerpt"]) for s in attached] == [
        (
            "material",
            old["id"],
            old["version_id"],
            "The greenhouse ventilation schedule starts on Tuesday.",
        )
    ]
    assert search("ceramic pieces", versions=refs, names=["cabinet.txt"], allow_auto=True) == (
        [],
        ["cabinet.txt"],
    )
    queued = materials.upload("col_qa", lid, "pending.txt", b"An unread fictional note.")
    materials.archive("col_qa", lid, unrelated["id"], unrelated["revision"], True)
    for version, code in [
        (foreign["version_id"], "not_found"),
        (queued["version_id"], "material_not_ready"),
        (unrelated["version_id"], "material_archived"),
    ]:
        with pytest.raises(HTTPException) as exc:
            materials.resolve_references("col_qa", lid, [version])
        assert exc.value.detail["code"] == code
    assert search("ceramic pieces", allow_auto=True) == ([], [])


def test_auto_search_finds_older_body_only_match_beyond_original_file_limit(qa):
    from src.qa import materials

    client, base = qa
    lid = create(qa)["id"]
    path = f"{base}/lists/{lid}/materials"
    expected = "The observatory shutter inspection is due every autumn."
    response = client.post(path, files={"file": ("appendix.txt", expected.encode())})
    assert response.status_code == 200, response.text
    old = response.json()
    for number in range(17):
        response = client.post(
            path,
            files={
                "file": (
                    f"observatory shutter {number}.txt",
                    b"The observatory displays fossils from the reference collection.",
                ),
            },
        )
        assert response.status_code == 200, response.text
    sources, missing = materials.search_references(
        "col_qa", lid, "observatory shutter", allow_auto=True
    )
    assert missing == []
    assert [(s["version_id"], s["excerpt"]) for s in sources] == [(old["version_id"], expected)]


def test_auto_search_keeps_global_and_per_document_source_limits(qa):
    from collections import Counter

    from src.qa import materials

    client, base = qa
    lid = create(qa)["id"]
    path = f"{base}/lists/{lid}/materials"
    for number in range(20):
        response = client.post(
            path,
            files={"file": (f"entry-{number}.txt", b"Fictional orchard irrigation log. " * 100)},
        )
        assert response.status_code == 200, response.text
    sources, missing = materials.search_references(
        "col_qa", lid, "orchard irrigation", allow_auto=True
    )
    assert missing == []
    assert len(sources) == materials.MAX_SOURCES
    assert (
        max(Counter(source["version_id"] for source in sources).values())
        <= materials.CHUNKS_PER_DOCUMENT
    )
    assert all("orchard irrigation" in source["excerpt"] for source in sources)


def test_auto_search_can_cancel_inside_a_large_document(qa):
    from src.qa import materials

    client, base = qa
    lid = create(qa)["id"]
    response = client.post(
        f"{base}/lists/{lid}/materials",
        files={"file": ("inspection.txt", b"Fictional orchard irrigation log. " * 2600)},
    )
    assert response.status_code == 200, response.text

    class Cancelled(BaseException):
        pass

    checkpoints = 0

    def checkpoint():
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints > 1:
            raise Cancelled

    with pytest.raises(Cancelled):
        materials.search_references(
            "col_qa", lid, "orchard irrigation", allow_auto=True, check_cancel=checkpoint
        )


def test_sparse_rebuild_includes_material_vectors_without_widening_search():
    from qdrant_client import models
    from qdrant_client.qdrant_client import QdrantClient

    from src.db.qdrant import QdrantManager
    from src.rag.source_scope import qa_source_scope
    from src.rag.sparse_encoder import SparseEncoder
    from src.rag.sparse_recalc import run_sparse_recalc

    db = QdrantManager.__new__(QdrantManager)
    db.client = QdrantClient(":memory:")
    db.client.create_collection(
        "col_sparse_qa",
        vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE),
        sparse_vectors_config={"sparse": models.SparseVectorParams()},
    )
    pid = str(uuid.uuid4())
    db.upsert_points(
        "col_sparse_qa",
        [pid],
        [[1.0, 0.0]],
        [
            {
                "text": "orchard irrigation schedule",
                "source_kind": "qa_material",
                "qa_list_id": "first",
                "is_current": True,
            }
        ],
    )
    result = run_sparse_recalc(db, "col_sparse_qa")
    assert result["rebuilt_chunks"] == 1
    encoder = SparseEncoder()
    encoder.load(db, "col_sparse_qa")
    assert encoder.encode_query("irrigation")
    exclude_config = models.Filter(
        must_not=[
            models.FieldCondition(key="chunk_type", match=models.MatchValue(value="__config__"))
        ]
    )
    assert (
        db.hybrid_search(
            "col_sparse_qa",
            [1.0, 0.0],
            encoder.encode_query("irrigation"),
            filter_condition=exclude_config,
        )
        == []
    )
    with qa_source_scope("first"):
        assert (
            db.hybrid_search(
                "col_sparse_qa",
                [1.0, 0.0],
                encoder.encode_query("irrigation"),
                filter_condition=exclude_config,
            )[0]["id"]
            == pid
        )
    db.client.close()
