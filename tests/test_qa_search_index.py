"""Q&A index tests with real SQLite and in-memory Qdrant."""

import asyncio
import threading
from importlib.util import find_spec

import pytest
from qdrant_client.qdrant_client import QdrantClient

from src.config import AppConfig, EmbeddingProviderConfig
from src.db.qdrant import QdrantManager
from src.services import services
from tests.test_qa import create, mutate, qa_fixture  # noqa: F401
from tests.test_qa_excel import commit, upload

PROJECT = "col_qa"
QUESTION = "When is the observatory gate tested?"
ANSWER = "Before each rehearsal."


class ExampleEmbedding:
    dimensions = 3

    def __init__(self):
        self.inputs = []

    def embed_texts(self, texts):
        self.inputs.extend(texts)
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.0, 0.0]


@pytest.fixture
def vector_index(monkeypatch):
    db = object.__new__(QdrantManager)
    db.client = QdrantClient(":memory:")
    db.create_collection(PROJECT, vector_size=3)
    encoder = ExampleEmbedding()
    model = EmbeddingProviderConfig(
        model="example-vectors", dimensions=3, is_default=True
    )
    monkeypatch.setattr(services, "db", db)
    monkeypatch.setattr(services, "config", AppConfig(embedding={"providers": [model]}))
    monkeypatch.setattr("src.rag.collection_utils.create_embedding_provider", lambda config: encoder)
    try:
        yield db, encoder
    finally:
        db.client.close()


def index():
    assert find_spec("src.qa.search_index"), "Current Q&A needs a searchable index"
    from src.qa import search_index

    return search_index


def saved_exchange(qa):
    data = mutate(qa, create(qa), "/questions", {"text": QUESTION})
    qid = data["questions"][0]["id"]
    cid = data["columns"][0]["id"]
    return mutate(qa, data, f"/questions/{qid}/replies/{cid}", {"body": ANSWER}, "put")


def test_current_parts_are_independently_searchable_and_only_edits_are_embedded(qa, vector_index):
    db, encoder = vector_index
    data = saved_exchange(qa)
    search = index()
    search.sync_project(PROJECT)
    assert set(encoder.inputs) == {QUESTION, ANSWER}
    assert {part.text for part in search.retrieve(PROJECT, "gate", 10)} == {QUESTION, ANSWER}
    assert db.search(PROJECT, [1.0, 0.0, 0.0]) == []
    assert db.scroll_points(PROJECT, include_qa_materials=True)[0] == []

    new_question = "Who checks the observatory gate?"
    mutate(qa, data, f"/questions/{data['questions'][0]['id']}", {"text": new_question}, "patch")
    assert [part.text for part in search.retrieve(PROJECT, "gate", 10)] == [ANSWER]
    search.sync_project(PROJECT)
    assert encoder.inputs[2:] == [new_question]
    assert {part.text for part in search.retrieve(PROJECT, "gate", 10)} == {new_question, ANSWER}


def test_imported_rewrite_reuses_saved_reply_vector_and_omits_previous_identity(qa, vector_index):
    _, encoder = vector_index
    data = saved_exchange(qa)
    old_id = data["questions"][0]["id"]
    search = index()
    search.sync_project(PROJECT)

    incoming = "What is the rehearsal entry check schedule?"
    plan = upload(qa, data, [["Question", "A response"], [incoming, ANSWER]])
    client, base = qa
    resolved = client.patch(
        f"{base}/lists/{data['id']}/imports/{plan['id']}",
        json={"plan_revision": plan["plan_revision"], "matches": {plan["rows"][0]["key"]: "new"}},
    )
    assert resolved.status_code == 200, resolved.text
    data = commit(qa, data, resolved.json())
    assert not search.retrieve(PROJECT, "gate", 10)
    search.sync_project(PROJECT)
    assert encoder.inputs[2:] == [incoming]
    hits = search.retrieve(PROJECT, "gate", 10)
    assert {part.text for part in hits} == {incoming, ANSWER}
    assert all(part.metadata["qa_question_id"] != old_id for part in hits)
    assert sum(not q["included"] for q in data["questions"]) == 1


def test_worker_discovers_pending_current_lists_and_new_project_model(qa, vector_index):
    from src.collections import store as collections

    saved_exchange(qa)
    search = index()
    assert hasattr(search, "pending_projects"), "The application needs durable backfill discovery"
    assert search.pending_projects() == [PROJECT]
    search.sync_project(PROJECT)
    assert search.pending_projects() == []
    services.config.embedding.default.model = "another-example-model"
    assert search.retrieve(PROJECT, "gate", 10) == []
    assert search.pending_projects() == [PROJECT]
    collections.set_archived(PROJECT, True)
    assert search.pending_projects() == []


def test_qdrant_retry_reuses_completed_embeddings(qa, vector_index, monkeypatch):
    db, encoder = vector_index
    saved_exchange(qa)
    search = index()
    save = db.upsert_points

    def unavailable(*args, **kwargs):
        raise OSError("Example storage interrupted")

    monkeypatch.setattr(db, "upsert_points", unavailable)
    with pytest.raises(OSError):
        search.sync_project(PROJECT)
    assert not search.retrieve(PROJECT, "gate", 10)
    monkeypatch.setattr(db, "upsert_points", save)
    search.sync_project(PROJECT)
    assert sorted(encoder.inputs) == sorted([QUESTION, ANSWER])
    assert {part.text for part in search.retrieve(PROJECT, "gate", 10)} == {QUESTION, ANSWER}


def test_a_failed_list_does_not_block_other_lists_in_the_same_project(qa, vector_index, monkeypatch):
    db, _ = vector_index
    lists = sorted([
        mutate(qa, create(qa), "/questions", {"text": "Where is the toolbox?"}),
        mutate(qa, create(qa), "/questions", {"text": "Who checks the locks?"}),
    ], key=lambda item: item["id"])
    save = db.upsert_points

    def write(collection, ids, vectors, payloads):
        if payloads[0]["qa_list_id"] == lists[0]["id"]:
            raise OSError("Example first list unavailable")
        save(collection, ids, vectors, payloads)

    monkeypatch.setattr(db, "upsert_points", write)
    search = index()
    with pytest.raises(OSError):
        search.sync_project(PROJECT)
    assert [part.metadata["qa_list_id"] for part in search.retrieve(PROJECT, "gate", 10)] == [lists[1]["id"]]
    assert search.pending_projects() == [PROJECT]


def test_index_task_checks_cancellation_after_embedding(qa, vector_index, monkeypatch):
    from src.tasks.task_manager import Task, _cancel_events

    _, encoder = vector_index
    saved_exchange(qa)
    task = Task(id="example-task", filename="Example list")
    cancelled = threading.Event()
    monkeypatch.setitem(_cancel_events, task.id, cancelled)
    embed = encoder.embed_texts

    def cancel_during_embedding(texts):
        output = embed(texts)
        cancelled.set()
        return output

    monkeypatch.setattr(encoder, "embed_texts", cancel_during_embedding)
    search = index()
    with pytest.raises(RuntimeError):
        asyncio.run(search.index_task(task, PROJECT))
    assert not search.retrieve(PROJECT, "gate", 10)
