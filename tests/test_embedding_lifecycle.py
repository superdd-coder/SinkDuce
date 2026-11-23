"""Exercise same-dimension model changes, disconnection, and atomic index replacement."""
from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from qdrant_client.qdrant_client import QdrantClient

from src.config import AppConfig, EmbeddingBinding, EmbeddingProviderConfig, load_config, save_config
from src.db.qdrant import QdrantManager
from src.meeting import embedding_index, store, transcript_index
from src.rag import collection_utils, embedding_rebuild
from src.rag.retriever import Retriever
from src.services import services


class Model:
    def __init__(self, config, calls):
        self.name = config.model
        self.dimensions = config.dimensions or 4
        self.calls = calls

    def embed_texts(self, texts):
        self.calls.append((self.name, list(texts)))
        axis = 0 if self.name == "A" else 1
        return [[float(i == axis) for i in range(self.dimensions)] for _ in texts]

    def embed_query(self, text):
        return self.embed_texts([text])[0]


@pytest.fixture
def indexes(monkeypatch, tmp_path):
    from src.collections import store as collections
    from src.file_mgmt import store as files
    from src.providers import embedding
    from src.tasks import history_store

    a, b = [EmbeddingProviderConfig(id=name, model=name, dimensions=3,
             base_url="https://example.invalid/v1", is_default=name == "A") for name in ("A", "B")]
    config = AppConfig(embedding={"providers": [a, b]})
    db = object.__new__(QdrantManager)
    db.client = QdrantClient(":memory:")
    calls = []
    factory = lambda p: Model(p, calls)
    monkeypatch.setattr(services, "db", db)
    monkeypatch.setattr(services, "config", config)
    monkeypatch.setattr(store, "MEETINGS_DIR", tmp_path / "meetings")
    monkeypatch.setattr(collections, "COLLECTIONS_DIR", tmp_path / "collections")
    monkeypatch.setattr(files, "COLLECTIONS_DIR", tmp_path / "collections")
    monkeypatch.setattr(history_store, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(history_store, "_cache", {})
    monkeypatch.setattr(embedding, "create_embedding_provider", factory)
    monkeypatch.setattr(collection_utils, "create_embedding_provider", factory)
    monkeypatch.setattr(embedding_rebuild, "create_embedding_provider", factory)
    monkeypatch.setattr("src.meeting.service._resolve_meeting_llm", lambda: None)
    monkeypatch.setattr(services, "retriever", Retriever(db, factory(a)))
    monkeypatch.setattr(services, "reranker", None)
    yield db, config.embedding, a, b, calls
    db.client.close()


def create(db, cid, model_id, dim=3):
    db.create_collection(cid, dim, {"embedding_provider_id": model_id})
    pid = str(uuid.uuid4())
    db.upsert_points(cid, [pid], [[1.] + [0.] * (dim - 1)],
                     [{"text": cid, "chunk_type": "normal", "source": "notes.txt"}])
    return pid


def test_upgrade_snapshot_and_last_provider_round_trip(indexes, tmp_path):
    db, settings, a, b, _ = indexes
    config_path = tmp_path / "config.yml"
    config_path.write_text("embedding:\n  providers:\n    - {id: a, model: A, dimensions: 3}\n    - {id: b, model: B, dimensions: 3}\n")
    app = load_config(config_path)
    assert app.embedding.default.id == "a"
    app.embedding.providers[0].is_default = False
    app.embedding.providers[1].is_default = True
    save_config(app, config_path)
    app = load_config(config_path)
    pin = collection_utils.collection_binding({}, app.embedding, 3)
    assert pin.model == "A" and app.embedding.default.model == "B"
    app.embedding.providers = []
    save_config(app, config_path)
    app = load_config(config_path)
    assert app.embedding.default is None and not pin.resolve(app.embedding.providers)
    app.embedding.providers.append(EmbeddingProviderConfig(id="replacement", model="A", dimensions=3))
    save_config(app, config_path)
    assert app.embedding.default.is_default and pin.resolve(app.embedding.providers).id == "replacement"


def test_upgrade_without_a_previous_default_keeps_unidentified_vectors_offline(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text("embedding: {providers: []}")
    app = load_config(path)
    app.embedding.providers.append(EmbeddingProviderConfig(model="B", dimensions=3))
    save_config(app, path)
    loaded = load_config(path).embedding
    assert loaded.default.model == "B"
    assert collection_utils.collection_binding({}, loaded, 3) is None


def test_legacy_create_requests_only_select_a_matching_configured_model(indexes):
    _, _, a, b, _ = indexes
    assert collection_utils.new_collection_provider(model="B").id == b.id
    with pytest.raises(ValueError, match="do not match"):
        collection_utils.new_collection_provider(a.id, model="B")


def test_distinct_spaces_route_offline_and_reconnect(indexes):
    from src.chatbox.qa_search import QaTurn
    from src.collections import store as collections
    from src.rag.direct_query import DirectQueryModule

    db, cfg, a, b, calls = indexes
    for cid, model in [("books", "A"), ("papers", "B")]:
        create(db, cid, model)
        collections.create_collection_meta(cid, cid.title())
    a.is_default, b.is_default = False, True
    direct = DirectQueryModule(services.retriever, db)
    result = direct.retrieve("which?", ["books", "papers"])
    assert len(result.chunks) == 2
    assert {(name, text[0]) for name, text in calls} == {("A", "which?"), ("B", "which?")}
    cfg.providers = [b]
    turn = QaTurn("question", [], None)
    assert [c.text for c in turn.direct(direct, "find", collections=[]).chunks] == ["papers"]
    assert turn.search_metadata() == {"skipped_collections": ["books"], "embedding_searches": [{
        "collection": "papers", "model": "B", "dimensions": 3, "mode": "dense",
        "provider_id": "B", "provider_name": "",
    }]}
    from src.rag.agentic_query import AgenticQueryResult
    from src.rag.agent_prompts import PARTIAL_COLLECTION_COVERAGE
    service = Mock()
    service.run.return_value = AgenticQueryResult(answer=None, context="available evidence", coverage_complete=True)
    partial = turn.agentic(service, "find", collections=["books", "papers"])
    assert service.run.call_args.kwargs["collections"] == ["papers"]
    assert partial.context.startswith(PARTIAL_COLLECTION_COVERAGE) and not partial.coverage_complete
    assert turn.search_metadata()["skipped_collections"] == ["books"]
    cfg.providers = []
    assert not QaTurn("question", ["books"], None).direct(direct, "find", collections=["books"]).chunks
    cfg.providers = [a.model_copy(update={"id": "new-record", "api_key": "rotated"})]
    assert collection_utils.embedding_status(db.get_collection_config("books"), "books")["embedding_provider_id"] == "new-record"
    assert direct.retrieve("restored", ["books"]).chunks


def test_rebuild_changes_dimension_keeps_points_and_sparse(indexes):
    from qdrant_client.models import SparseVector
    db, cfg, a, b, _ = indexes
    pid = create(db, "books", a.id)
    db.upsert_sparse_vectors("books", [pid], [{2: 1.5}])
    b.dimensions = 4
    embedding_rebuild.rebuild_collection("books", EmbeddingBinding.from_provider(b))
    assert db.get_vector_size("books") == 4
    assert db.list_collections() == ["books"]
    assert db.collection_exists("books")
    point = db.client.retrieve("books", [pid], with_vectors=True)[0]
    assert point.payload["text"] == "books"
    assert point.vector[""] == [0.0, 1.0, 0.0, 0.0]
    assert point.vector["sparse"] == SparseVector(indices=[2], values=[1.5])
    assert collection_utils.embedding_status(db.get_collection_config("books"), "books")["embedding_model"] == "B"
    db.delete_collection("books")
    assert db.list_collections() == []


def test_query_is_embedded_once_per_connection_and_dimension(indexes):
    from src.rag.direct_query import DirectQueryModule

    db, _, a, b, calls = indexes
    for name, model, size in [("first", a, 3), ("second", a, 3), ("short", a, 2), ("other", b, 3)]:
        create(db, name, model.id, size)
    result = DirectQueryModule(services.retriever, db).retrieve("question", ["first", "second", "short", "other"])
    assert len(result.chunks) == 4
    assert sorted(model for model, texts in calls if texts == ["question"]) == ["A", "A", "B"]
    assert {(e["collection"], e["model"], e["dimensions"]) for e in result.trace} == {
        ("first", "A", 3), ("second", "A", 3), ("short", "A", 2), ("other", "B", 3),
    }


def test_dense_child_and_parent_lookup_uses_current_binding_after_cutover(indexes):
    from src.rag.direct_query import DirectQueryModule

    db, _, a, b, calls = indexes
    db.create_collection("books", 3, {"embedding_provider_id": a.id, "chunk_mode": "parent_child"})
    parent, child = (str(uuid.uuid4()) for _ in range(2))
    db.upsert_points("books", [parent, child], [[0., 0., 0.], [1., 0., 0.]], [
        {"text": "chapter one", "chunk_type": "parent"},
        {"text": "paragraph", "chunk_type": "child", "parent_id": parent},
    ])
    old = collection_utils.get_embedding_overrides(["books"])
    embedding_rebuild.rebuild_collection("books", EmbeddingBinding.from_provider(b))
    calls.clear()
    result = DirectQueryModule(services.retriever, db).retrieve("question", ["books"], embedding_overrides=old)
    assert [p.text for p in result.chunks] == ["chapter one"]
    assert result.child_groups[parent][0]["id"] == child
    assert calls == [("B", ["question"])]
    assert [(event["model"], event["dimensions"]) for event in result.trace] == [("B", 3)]


def test_search_started_before_cutover_finishes_on_original_index(indexes):
    from contextvars import Context

    db, cfg, a, b, calls = indexes
    pid = create(db, "books", a.id)
    with db.client.snapshot("books"):
        Context().run(embedding_rebuild.rebuild_collection, "books", EmbeddingBinding.from_provider(b))
        assert db.get_collection_config("books")["embedding_model"] == "A"
        assert db.get_points_by_ids("books", [pid])
    assert db.get_collection_config("books")["embedding_model"] == "B"
    assert not db.client.raw.collection_exists("books")


def test_search_details_match_model_requests_and_vector_index_results(indexes, monkeypatch):
    import json
    import httpx
    from openai import OpenAI
    from fastapi.testclient import TestClient
    from src.main import app
    from src.chatbox.qa_search import QaTurn
    from src.providers.embedding import openai_compat
    from src.rag.direct_query import DirectQueryModule

    db, _, a, b, _ = indexes
    requests = []
    def respond(request):
        body = json.loads(request.content)
        requests.append((body["model"], body["input"], body["dimensions"]))
        vector = [1., 0., 0.] if body["model"] == "A" else [0., 1., 0.]
        return httpx.Response(200, json={"object": "list", "model": body["model"],
            "data": [{"object": "embedding", "index": 0, "embedding": vector}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        monkeypatch.setattr(openai_compat, "OpenAI", lambda **kw: OpenAI(**kw, http_client=transport, max_retries=0))
        monkeypatch.setattr(collection_utils, "create_embedding_provider", openai_compat.OpenAICompatEmbedding)
        for name, provider in [("first", a), ("second", b)]:
            provider.name = "Connection " + provider.id
            provider.api_key = "fixture-key"
            db.create_collection(name, 3, {"embedding_provider_id": provider.id})
            db.upsert_points(name, [str(uuid.uuid4()), str(uuid.uuid4())], [[1., 0., 0.], [0., 1., 0.]],
                             [{"text": "first axis"}, {"text": "second axis"}])
        a.is_default, b.is_default = False, True
        direct = DirectQueryModule(services.retriever, db)
        turn = QaTurn("compare", [], None)
        result = turn.direct(direct, "which axis?", collections=["first", "second"], top_k=2)
        assert sorted(requests) == [("A", ["which axis?"], 3), ("B", ["which axis?"], 3)]
        assert {(c.metadata["collection"], c.text) for c in result.chunks} == {
            ("first", "first axis"), ("second", "second axis"),
        }
        assert [row["model"] for row in turn.search_metadata()["embedding_searches"]] == ["A", "B"]
        for row in turn.search_metadata()["embedding_searches"]:
            assert row["dimensions"] == 3 and row["mode"] == "dense"
            assert row["provider_name"] == "Connection " + row["provider_id"]

        monkeypatch.setattr(services, "direct_query", direct)
        client = TestClient(app, base_url="http://127.0.0.1:18900", client=("127.0.0.1", 50000))
        response = client.post("/api/recall/search", json={
            "query": "fixture question", "collections": ["first", "second"],
            "search_mode": "dense", "use_reranker": False, "top_k": 2,
        })
        client.close()
        assert response.status_code == 200, response.text
        assert {e["collection"]: e["model"] for e in response.json()["retrieval_trace"]} == {"first": "A", "second": "B"}

        db.create_collection("empty", 3, {"embedding_provider_id": a.id})
        empty = direct.retrieve("unknown keyword", ["empty"], search_mode="hybrid")
        assert not empty.chunks
        assert [(e["kind"], e["model"], e["mode"]) for e in empty.trace] == [("retrieval_fallback", "A", "dense")]
        monkeypatch.setattr(db, "search", Mock(side_effect=RuntimeError("index offline")))
        failed = direct.retrieve("unavailable", ["empty"], search_mode="hybrid")
        assert failed.errors and not failed.trace


def test_ingest_history_tracks_batches_and_only_finishes_after_index_switch(indexes, monkeypatch):
    import asyncio
    import threading
    import src.tasks
    from src.api.routes.documents import get_ingest_history
    from src.tasks import history_store
    from src.tasks.task_manager import TaskManager, TaskStatus

    db, _, a, b, _ = indexes
    db.create_collection("batch", 3, {"embedding_provider_id": a.id})
    count = 140
    db.upsert_points("batch", [str(uuid.uuid4()) for _ in range(count)], [[1., 0., 0.]] * count,
                     [{"text": str(i)} for i in range(count)])
    halfway, resume = threading.Event(), threading.Event()
    old_encode = Model.embed_texts
    def encode(self, texts):
        if len(texts) > 1 and history_store.list_records("batch")[0]["processed"]:
            halfway.set()
            assert resume.wait(5)
        return old_encode(self, texts)
    monkeypatch.setattr(Model, "embed_texts", encode)
    manager = TaskManager()
    manager.register_handler("embedding_rebuild", embedding_rebuild.rebuild_task)
    monkeypatch.setattr(src.tasks, "task_manager", manager)
    async def run():
        await manager.start()
        try:
            task = embedding_rebuild.queue_rebuild("batch", EmbeddingBinding.from_provider(b))
            queued, = get_ingest_history("batch")["records"]
            assert queued["status"] == "queued" and queued["model"] == b.model and queued["task_id"] == task.id
            assert await asyncio.to_thread(halfway.wait, 5)
            live, = get_ingest_history("batch")["records"]
            assert live["stage"] == "reindexing" and live["status"] == "processing"
            assert live["total"] == count and 0 < live["processed"] < count
            assert live["progress"] == 100 * live["processed"] // count
            assert db.get_collection_config("batch")["embedding_model"] == a.model
            resume.set()
            async with asyncio.timeout(10):
                while task.status in (TaskStatus.PENDING, TaskStatus.PROCESSING):
                    await asyncio.sleep(0.01)
            assert task.status == TaskStatus.COMPLETED, task.error
            history_store._cache.clear()
            finished, = get_ingest_history("batch")["records"]
            assert finished["record_id"] == queued["record_id"]
            assert finished["status"] == "success" and finished["progress"] == 100
            assert finished["processed"] == finished["total"] == count
            assert db.get_collection_config("batch")["embedding_model"] == b.model
        finally:
            resume.set()
            await manager.stop()
    asyncio.run(run())


def test_manual_rebuild_with_current_model_keeps_collection_dimensions(indexes, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from src.api.routes.collections import rebuild_embedding
    from src.collections import store as collections

    db, _, a, _, _ = indexes
    create(db, "books", a.id, 2)
    collections.create_collection_meta("books", "Books")
    def enqueue(cid, binding):
        assert binding.dimensions == 2
        embedding_rebuild.rebuild_collection(cid, binding)
        return SimpleNamespace(id="rebuild")
    monkeypatch.setattr(embedding_rebuild, "queue_rebuild", enqueue)
    assert asyncio.run(rebuild_embedding("books", a.id))["task_id"] == "rebuild"
    assert db.get_vector_size("books") == 2


@pytest.mark.parametrize("fail_probe", [True, False])
def test_failed_rebuild_keeps_original(indexes, monkeypatch, fail_probe):
    db, cfg, a, b, _ = indexes
    pid = create(db, "books", a.id)
    bad = Mock(dimensions=3)
    bad.embed_query.return_value = [0.0] if fail_probe else [0., 1., 0.]
    bad.embed_texts.return_value = []
    monkeypatch.setattr(embedding_rebuild, "create_embedding_provider", lambda p: bad)
    with pytest.raises(ValueError):
        embedding_rebuild.rebuild_collection("books", EmbeddingBinding.from_provider(b))
    assert db.get_collection_config("books")["embedding_model"] == "A"
    assert db.get_points_by_ids("books", [pid])
    assert list(db.client.aliases().values()) == ["books"]
    assert len(db.client.raw.get_collections().collections) == 1


def test_meetings_keep_original_or_automatically_rebuild(indexes, monkeypatch):
    from src.meeting.models import MeetingStatus
    from src.tasks import task_manager

    db, cfg, a, b, calls = indexes
    meetings = []
    def add(title):
        m = store.create_meeting(title)
        store.save_sentences(m.id, [{"sentence_id": "stt_0001", "original_text": title}])
        store.update_meeting(m.id, status=MeetingStatus.completed)
        meetings.append(m.id)
        return m.id
    first = add("first")
    assert transcript_index.index_from_store(first) == 1
    a.is_default, b.is_default = False, True
    second = add("second")
    assert transcript_index.index_from_store(second) == 1
    task_calls = []
    monkeypatch.setattr(task_manager, "get_active_tasks", lambda **kw: [])
    monkeypatch.setattr(task_manager, "create_task", lambda **kw: task_calls.append(kw["meeting_id"]))
    embedding_index.reconcile()
    assert not task_calls
    calls.clear()
    hits = transcript_index.search_group_transcript_packs("find", meetings=[{"meeting_id": mid} for mid in meetings],
                                                         retriever=services.retriever)
    assert {p["meeting_id"] for p in hits} == set(meetings)
    assert {(name, text[0]) for name, text in calls} == {("A", "find"), ("B", "find")}
    cfg.providers = []
    embedding_index.reconcile()
    assert all(store.get_meeting(mid).transcript_index_status == "waiting_model" for mid in meetings)
    cfg.providers = [b.model_copy(update={"id": "re-added"})]
    embedding_index.reconcile()
    assert task_calls == [first]
    assert store.get_meeting(second).transcript_index_status == "ready"
    transcript_index.index_from_store(first)
    assert store.get_meeting(first).transcript_embedding.model == "B"
    assert not db.get_points_by_ids(embedding_index.space_collection(EmbeddingBinding.from_provider(a)),
                                    [transcript_index._point_id(first, 0)])


def test_settings_delete_impact_and_default_succession(indexes, monkeypatch, tmp_path):
    import asyncio
    from unittest.mock import AsyncMock
    from src.api.routes import config as routes
    from src.collections import store as collections

    db, cfg, a, b, _ = indexes
    for cid, provider in [("books", a), ("papers", b), ("hidden", a)]:
        create(db, cid, provider.id)
        collections.create_collection_meta(cid, cid.title())
    path = tmp_path / "config.yml"
    save_config(services.config, path)
    monkeypatch.setattr(routes, "get_config", lambda: load_config(path))
    monkeypatch.setattr(routes, "reload_config", lambda: load_config(path))
    monkeypatch.setattr(routes, "save_config", lambda config: save_config(config, path))
    monkeypatch.setattr(routes, "async_reload_services", AsyncMock())
    monkeypatch.setattr("src.identity.visibility.filter_collection_ids",
                        lambda actor, ids: [i for i in ids if i != "hidden"])

    assert routes.embedding_provider_impact("A") == {"collections": [{"id": "books", "name": "Books"}], "last_model": False}

    async def change():
        await routes.delete_embedding_provider(a.id)
        remaining = load_config(path).embedding
        assert remaining.default.id == b.id
        assert routes.embedding_provider_impact("B")["last_model"]
        await routes.delete_embedding_provider(b.id)
        assert load_config(path).embedding.default is None
        restored = await routes.add_embedding_provider(a.model_copy(update={"id": "", "is_default": False}))
        assert restored["is_default"] and restored["id"] != a.id
        assert EmbeddingBinding.from_provider(a).resolve(load_config(path).embedding.providers).id == restored["id"]

    asyncio.run(change())


def test_only_administrators_can_change_global_transcript_connections(indexes, monkeypatch, tmp_path):
    from unittest.mock import AsyncMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src import config as config_module
    from src.api.routes import config as routes
    from src.identity import Actor, acl, can, reset_actor, roles, set_actor

    _, _, first, second, _ = indexes
    path = tmp_path / "config.yaml"
    save_config(services.config, path)
    original = path.read_bytes()
    monkeypatch.setattr(config_module, "CONFIG_PATH", path)
    monkeypatch.setattr(config_module, "_config", None)
    monkeypatch.setattr(roles, "IDENTITY_DIR", tmp_path / "identity")
    monkeypatch.setattr(roles, "ROLES_PATH", roles.IDENTITY_DIR / "roles.json")
    monkeypatch.setattr(acl, "DOMAIN_ACL_PATH", roles.IDENTITY_DIR / "domain_acl.json")
    role = roles.create_custom_role(name="Settings editor", capabilities={"config.write": True})
    editor = Actor(id="settings-device", kind="key", role_id=role.id)
    assert can(editor, "config.write") and not can(editor, "meeting.read")
    refresh = AsyncMock()
    wake = Mock()
    monkeypatch.setattr(routes, "async_reload_services", refresh)
    monkeypatch.setattr(embedding_index, "connections_changed", wake)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    providers = "/api/embedding/providers"
    with TestClient(app) as client:
        token = set_actor(editor)
        try:
            assert client.get(providers).status_code == 200
            for method, url, payload in [
                ("POST", providers, {"id": "C", "model": "C"}),
                ("PUT", f"{providers}/{first.id}", {"base_url": "https://alternate.example.test/v1"}),
                ("POST", f"{providers}/{second.id}/set-default", None),
                ("DELETE", f"{providers}/{first.id}", None),
                ("PUT", "/api/config", {"section": "embedding", "data": {"providers": []}}),
            ]:
                response = client.request(method, url, json=payload)
                assert response.status_code == 403, (method, url)
                assert response.json()["detail"]["code"] == "embedding_admin_required"
            assert path.read_bytes() == original
            refresh.assert_not_awaited()
            wake.assert_not_called()
            list_meetings = Mock(return_value=[])
            monkeypatch.setattr(store, "list_meetings", list_meetings)
            embedding_index.reconcile(retry_now=True)
            list_meetings.assert_not_called()
        finally:
            reset_actor(token)

        token = set_actor(Actor(id="admin-device", kind="key", role_id="admin"))
        try:
            assert client.post(f"{providers}/{second.id}/set-default").status_code == 200
        finally:
            reset_actor(token)
        assert load_config(path).embedding.default.id == second.id
        refresh.assert_awaited_once()
        wake.assert_called_once()


def test_upgrade_pins_legacy_vectors_without_embedding(indexes):
    from qdrant_client.models import Distance, VectorParams

    db, cfg, a, b, calls = indexes
    db.client.raw.create_collection("legacy", vectors_config=VectorParams(size=2, distance=Distance.COSINE))
    a.is_default, b.is_default = False, True
    stored = db.pin_legacy_embedding("legacy", cfg)
    assert stored["embedding_binding"]["model"] == a.model
    assert stored["embedding_binding"]["dimensions"] == 2
    cfg.providers = [b]
    assert db.pin_legacy_embedding("legacy", cfg) == stored
    assert collection_utils.embedding_status(stored, "legacy")["embedding_disconnected"]
    assert not calls


def test_deletion_during_rebuild_is_never_republished(indexes, monkeypatch):
    db, cfg, a, b, calls = indexes
    point = create(db, "books", "A")
    class ConcurrentEdit(Model):
        def embed_texts(self, texts):
            if texts != ["Embedding connection test"]:
                db.delete_points("books", [point])
            return super().embed_texts(texts)
    monkeypatch.setattr(embedding_rebuild, "create_embedding_provider", lambda p: ConcurrentEdit(p, calls))
    with pytest.raises(RuntimeError, match="changed during"):
        embedding_rebuild.rebuild_collection("books", EmbeddingBinding.from_provider(b))
    assert not db.get_points_by_ids("books", [point])
    assert db.get_collection_config("books")["embedding_model"] == "A"


def test_qdrant_guard_rejects_stale_ingest_after_switch(indexes):
    db, cfg, a, b, calls = indexes
    create(db, "books", "A")
    embedding_rebuild.rebuild_collection("books", EmbeddingBinding.from_provider(b))
    with pytest.raises(ValueError, match="changed"):
        db.upsert_points("books", [str(uuid.uuid4())], [[1., 0., 0.]], [{"text": "late"}],
                         expected_binding=EmbeddingBinding.from_provider(a))
    assert db.get_collection_info("books")["points_count"] == 1


def test_cancelled_queued_rebuild_can_be_retried_through_task_manager(indexes, monkeypatch):
    import asyncio
    import src.tasks
    from src.tasks.task_manager import TaskManager, TaskStatus

    db, _, a, b, _ = indexes
    create(db, "books", a.id)
    manager = TaskManager()
    manager.register_handler("embedding_rebuild", embedding_rebuild.rebuild_task)
    monkeypatch.setattr(src.tasks, "task_manager", manager)

    async def run():
        cancelled = embedding_rebuild.queue_rebuild("books", EmbeddingBinding.from_provider(b))
        assert manager.cancel_task(cancelled.id)
        assert src.tasks.history_store.get_record_by_task("books", cancelled.id)["status"] == "failed"
        cfg = db.get_collection_config("books")
        assert cfg["embedding_rebuild"]["state"] == "failed" and cfg["embedding_model"] == "A"
        retried = manager.retry_task(cancelled.id)
        assert retried and retried.id != cancelled.id
        assert manager.retry_task(cancelled.id) is None
        await manager.start()
        try:
            async with asyncio.timeout(10):
                while retried.status in (TaskStatus.PENDING, TaskStatus.PROCESSING):
                    await asyncio.sleep(0.01)
            assert cancelled.status == TaskStatus.FAILED
            assert retried.status == TaskStatus.COMPLETED, retried.error
            assert db.get_collection_config("books")["embedding_model"] == "B"
        finally:
            await manager.stop()

    asyncio.run(run())


def test_recovered_rebuild_keeps_one_history_row(indexes, monkeypatch):
    import asyncio
    import src.tasks
    from src.tasks import history_store
    from src.tasks.task_manager import TaskManager, TaskStatus

    db, _, a, b, _ = indexes
    db.create_collection("empty", 3, {"embedding_provider_id": a.id})
    async def run():
        monkeypatch.setattr(src.tasks, "task_manager", TaskManager())
        original = embedding_rebuild.queue_rebuild("empty", EmbeddingBinding.from_provider(b))
        original_row = history_store.get_record_by_task("empty", original.id)
        history_store._cache.clear()
        recovered = TaskManager()
        recovered.register_handler("embedding_rebuild", embedding_rebuild.rebuild_task)
        monkeypatch.setattr(src.tasks, "task_manager", recovered)
        embedding_rebuild.recover_rebuilds()
        task, = recovered.get_all_tasks()
        assert task.id == original.id and history_store.mark_interrupted({task.id}) == 0
        await recovered.start()
        try:
            async with asyncio.timeout(5):
                while task.status in (TaskStatus.PENDING, TaskStatus.PROCESSING):
                    await asyncio.sleep(0.01)
            record, = history_store.list_records("empty")
            assert record["record_id"] == original_row["record_id"] and record["status"] == "success"
            assert record["total"] == record["processed"] == 0 and record["progress"] == 100
        finally:
            await recovered.stop()
    asyncio.run(run())


@pytest.mark.parametrize("revoked", [False, True])
def test_rebuild_recovery_rechecks_the_original_device(indexes, monkeypatch, revoked):
    import asyncio
    import src.tasks
    from src.identity import Actor, reset_actor, set_actor
    from src.tasks import history_store
    from src.tasks.task_manager import TaskManager

    db, _, a, b, calls = indexes
    create(db, "library", a.id)
    device = Actor(id="fixture-device", kind="key", role_id="member")
    authorize = Mock()
    monkeypatch.setattr("src.identity.authorize", authorize)
    monkeypatch.setattr("src.pairing.service.device_actor_is_active", lambda actor: actor == device and not revoked)

    async def run():
        monkeypatch.setattr(src.tasks, "task_manager", TaskManager())
        token = set_actor(device)
        try:
            task = embedding_rebuild.queue_rebuild("library", EmbeddingBinding.from_provider(b))
        finally:
            reset_actor(token)
        history_store._cache.clear()
        restarted = TaskManager()
        monkeypatch.setattr(src.tasks, "task_manager", restarted)
        embedding_rebuild.recover_rebuilds()
        record, = history_store.list_records("library")
        assert "fixture-device" not in str(record)
        if revoked:
            assert restarted.get_task(task.id) is None
            assert record["status"] == "failed"
            assert db.get_collection_config("library")["embedding_model"] == a.model
            assert not calls
        else:
            assert restarted.get_task(task.id).requester == device
            authorize.assert_called_once_with(device, "collection.update", {"collection_id": "library"})

    asyncio.run(run())
