"""The evidence shown in Chat stays attached to the cited Q&A revision."""

import asyncio
import json
from importlib.util import find_spec
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.chatbox.agent import ChatboxAgent
from src.chatbox.citations import EvidenceRegistry
from src.rag.direct_query import DirectQueryModule
from src.rag.reranker import Reranker
from src.rag.retriever import Retriever
from tests.test_chatbox_agent import store, mock_llm, _fake_llm_response, _fake_stream, _tc_delta
from tests.test_chat_qa_retrieval import Grader, Ranker
from tests.test_qa import mutate, qa_fixture  # noqa: F401
from tests.test_qa_search_index import PROJECT, QUESTION, ANSWER, saved_exchange, vector_index  # noqa: F401


def test_snapshot_keeps_the_exact_reply_and_round_label_after_editing(qa, vector_index, store):
    from src.qa import search_index

    data = saved_exchange(qa)
    qid = data["questions"][0]["id"]
    cid = data["columns"][0]["id"]
    data = mutate(qa, data, f"/questions/{qid}/replies/{data['columns'][1]['id']}", {"body": "Use the second entrance."}, "put")
    search_index.sync_project(PROJECT)
    hits = search_index.retrieve(PROJECT, "gate", 10)
    session = store.create_session(title="Example session")
    assert find_spec("src.chatbox.qa_evidence"), "Chat needs a Q&A snapshot evidence service"
    from src.chatbox.qa_evidence import QaEvidence, open_snapshot

    registry = EvidenceRegistry(session.id)
    turn = QaEvidence(store, session.id)
    bundle = turn.capture(hits, [], registry, "gate", None)
    assert len(bundle.posts) == 1
    assert {record["quote"] for record in registry.records()} == {QUESTION, ANSWER, "Use the second entrance."}
    assert not turn.capture(hits, [], registry, "gate", None).posts

    data = mutate(qa, data, f"/questions/{qid}/replies/{cid}", {"body": "Only before the first rehearsal."}, "put")
    mutate(qa, data, f"/columns/{cid}", {"label": "Updated round"}, "patch")
    snapshot = open_snapshot(store, session.id, bundle.posts[0].snapshot_id)
    assert snapshot["question"] == QUESTION
    assert [(r["label"], r["body"]) for r in snapshot["replies"]] == [
        ("A response", ANSWER), ("B response", "Use the second entrance.")
    ]
    assert not bundle.context(registry), "A changed post may be opened historically but must leave live context"


def test_overlong_question_leaves_room_for_the_graded_reply(qa, vector_index, store):
    from src.chatbox.qa_evidence import QaEvidence, open_snapshot
    from src.qa import search_index

    data = saved_exchange(qa)
    qid = data["questions"][0]["id"]
    question = "Describe how each visitor enters the rehearsal area. " * 100
    mutate(qa, data, f"/questions/{qid}", {"text": question}, "patch")
    search_index.sync_project(PROJECT)
    hits = [hit for hit in search_index.retrieve(PROJECT, "gate", 10) if hit.metadata["qa_column_id"]]
    session = store.create_session(title="Example session")
    registry = EvidenceRegistry(session.id)
    bundle = QaEvidence(store, session.id, budget=220).capture(hits, [], registry, "gate", None)
    post = bundle.posts[0]
    assert [round["matched"] for round in post.text["rounds"]] == [False, True]
    assert post.text["rounds"][0]["excerpt"]
    assert post.text["rounds"][1]["text"] == ANSWER
    assert all("[Excerpt]" not in record["quote"] for record in registry.records())
    assert open_snapshot(store, session.id, post.snapshot_id)["question"] == question


@pytest.mark.parametrize(("mode", "stream", "context_limit"), [
    ("direct", False, False), ("direct", True, False),
    ("agentic", False, False), ("agentic", True, False),
    ("direct", False, True), ("direct", True, True),
])
def test_chat_expands_graded_q_and_a_in_model_context_only(qa, vector_index, store, mock_llm, mode, stream, context_limit):
    from src.qa import search_index
    from src.rag.agentic_query import AgenticQueryService
    from src.rag.aggregator import Aggregator
    from src.rag.decomposer import AtomicQuery
    from src.rag.variant_fetcher import VariantFetcher
    from src.services import services

    data = saved_exchange(qa)
    search_index.sync_project(PROJECT)
    extra_reply = "Check the spare handle once per rehearsal. " * 330
    if context_limit:
        qid, cid = data["questions"][0]["id"], data["columns"][1]["id"]
        mutate(qa, data, f"/questions/{qid}/replies/{cid}", {"body": extra_reply}, "put")
    db, encoder = vector_index
    grader = Grader("keep")
    direct = DirectQueryModule(Retriever(db, encoder), db, Reranker(Ranker()), grader)
    catalog = SimpleNamespace(get_catalog=lambda *_args, **_kwargs: [SimpleNamespace(id=PROJECT)])
    decomposer = SimpleNamespace(decompose=lambda *_args, **_kwargs: [
        AtomicQuery(query="gate", target_collections=[PROJECT]),
    ])
    agentic = AgenticQueryService(
        direct, VariantFetcher(direct, grader), catalog, decomposer, Aggregator(grader), grader,
    )
    agent = ChatboxAgent(store, mock_llm, agentic, direct_module=direct)
    session = store.create_session(title="Example session", collections=[PROJECT])
    tool_name = "lookup_collection" if mode == "direct" else "search_knowledge_base"
    arguments = json.dumps({"query": "gate", "raw_query": "gate"})
    tool = {"id": "first", "type": "function", "function": {"name": tool_name, "arguments": arguments}}
    observed = []

    def response(**kwargs):
        observed.append(kwargs["messages"])
        if len(observed) == 1:
            if stream:
                return _fake_stream([("tool_calls", {"tool_calls": [_tc_delta(0, "first", tool_name, arguments)]})])
            return _fake_llm_response(tool_calls=[tool])
        if len(observed) == 2 and context_limit:
            assert any(extra_reply.strip() in m["content"] for m in observed[-1] if m["role"] == "tool")
            raise RuntimeError("maximum context length exceeded")
        if stream:
            return _fake_stream([("stop", {"content": "Example answer"})])
        return _fake_llm_response(content="Example answer")

    mock_llm._client.chat.completions.create.side_effect = response

    async def events():
        return [e async for e in agent.chat_stream(session.id, "gate", mode=mode, collections=[PROJECT])]

    with patch("src.config.get_config", return_value=services.config):
        result = (
            next(e for e in reversed(asyncio.run(events())) if e["type"] == "done")
            if stream else {"sources": agent.chat(session.id, "gate", mode=mode).sources}
        )
    assert len([row for row in result["sources"] if row["metadata"].get("qa_snapshot_id")]) == 1
    tools = [message["content"] for message in observed[-1] if message["role"] == "tool"]
    assert any(QUESTION in content and ANSWER in content for content in tools)
    assert all(ANSWER not in row.content for row in store.get_messages(session.id) if row.role == "tool")
    if context_limit:
        assert len(observed) == 3
        assert all(extra_reply.strip() not in content for content in tools)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("select_project", [False, True])
def test_followup_uses_previous_named_project_and_skips_redundant_search_tools(qa, vector_index, store, mock_llm, stream, select_project):
    from src.collections import store as collections
    from src.qa import search_index
    from src.rag.agentic_query import AgenticQueryService
    from src.rag.aggregator import Aggregator
    from src.rag.decomposer import AtomicQuery
    from src.rag.variant_fetcher import VariantFetcher
    from src.services import services

    saved_exchange(qa)
    collections.update_collection_meta(PROJECT, name="Project Juniper")
    collections.create_collection_meta("col_annex", "Project Cedar")
    search_index.sync_project(PROJECT)
    db, encoder = vector_index
    ranker, grader = Ranker(), Grader("keep")
    direct = DirectQueryModule(Retriever(db, encoder), db, Reranker(ranker), grader)
    catalog = SimpleNamespace(get_catalog=lambda *_args, **_kwargs: [SimpleNamespace(id=PROJECT, name="Project Juniper")])
    decomposer = SimpleNamespace(decompose=lambda *_args, **_kwargs: [AtomicQuery(query="gate", target_collections=[PROJECT])])
    service = AgenticQueryService(direct, VariantFetcher(direct, grader), catalog, decomposer, Aggregator(grader), grader)
    agent = ChatboxAgent(store, mock_llm, service, direct_module=direct)
    selected = [PROJECT] if select_project else []
    session = store.create_session(title="Example session", collections=selected)
    store.add_message(session.id, "user", "Juniper 项目的巡检安排是什么？")
    store.add_message(session.id, "assistant", "Let's check the inspection arrangements.")
    question = "为什么这个项目要提前做访客登记？"
    search, directory = "search_knowledge_base", "list_collections"
    observed_tools = []
    arguments = json.dumps({"raw_query": "visitor gate schedule", "decompose": False})

    def response(**kwargs):
        observed_tools.append({t["function"]["name"] for t in kwargs.get("tools", [])})
        if len(observed_tools) == 1:
            if stream:
                return _fake_stream([("tool_calls", {"tool_calls": [
                    _tc_delta(0, "search", search, arguments),
                    _tc_delta(1, "directory", directory, "{}"),
                ]})])
            return _fake_llm_response(tool_calls=[
                {"id": "search", "type": "function", "function": {"name": search, "arguments": arguments}},
                {"id": "directory", "type": "function", "function": {"name": directory, "arguments": "{}"}},
            ])
        if len(observed_tools) == 2:
            if stream:
                return _fake_stream([("tool_calls", {"tool_calls": [_tc_delta(0, "repeat", search, arguments)]})])
            return _fake_llm_response(tool_calls=[
                {"id": "repeat", "type": "function", "function": {"name": search, "arguments": arguments}},
            ])
        if stream:
            return _fake_stream([("stop", {"content": "Example answer"})])
        return _fake_llm_response(content="Example answer")

    mock_llm._client.chat.completions.create.side_effect = response

    async def events():
        return [event async for event in agent.chat_stream(session.id, question, collections=selected)]

    with patch("src.config.get_config", return_value=services.config):
        if stream:
            result = next(event for event in reversed(asyncio.run(events())) if event["type"] == "done")
        else:
            result = {"sources": agent.chat(session.id, question).sources}
    assert any(row["metadata"].get("qa_snapshot_id") for row in result["sources"])
    calls = [
        call["function"]["name"] for row in store.get_messages(session.id)
        if isinstance((row.metadata or {}).get("tool_calls"), list)
        for call in row.metadata["tool_calls"]
    ]
    assert calls == [search]
    assert not ({search, directory} & observed_tools[-1])
    assert len(ranker.batches) == 1


def test_snapshot_api_opens_the_pinned_material_passage(qa, vector_index, store, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.routes.sessions import router
    from src.qa import materials, search_index
    from src.qa.store import connection
    from src.services import services

    data = saved_exchange(qa)
    reply = data["questions"][0]["replies"][0]
    client, base = qa
    material_url = f"{base}/lists/{data['id']}/materials"
    uploaded = client.post(material_url, files={"file": ("sample.txt", b"An observatory gate is tested before rehearsal.")})
    assert uploaded.status_code == 200
    material = uploaded.json()
    sources, _ = materials.search_references(PROJECT, data["id"], "observatory gate", allow_auto=True)
    assert len(sources) == 1
    with connection(PROJECT, "update") as conn, conn:
        conn.execute("INSERT INTO qa_reply_sources VALUES(?,?)", (reply["text_revision_id"], json.dumps(sources)))

    from src.chatbox import qa_evidence

    search_index.sync_project(PROJECT)
    session = store.create_session(title="Example session")
    turn = qa_evidence.QaEvidence(store, session.id)
    bundle = turn.capture(search_index.retrieve(PROJECT, "gate", 10), [], EvidenceRegistry(session.id), "gate", None)
    replacement = client.post(
        f"{material_url}/{material['id']}/versions", data={"expected_revision": material["revision"]},
        files={"file": ("sample.txt", b"An observatory gate is tested after rehearsal.")},
    )
    assert replacement.status_code == 200
    monkeypatch.setattr(services, "session_store", store)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    response = TestClient(app).get(f"/api/sessions/{session.id}/qa-sources/{bundle.posts[0].snapshot_id}")
    assert response.status_code == 200, response.text
    ref = response.json()["replies"][0]["sources"][0]
    assert ref["version_id"] == material["current_version_id"]
    assert ref["historical"] and ref["available"]
    assert ref["excerpt"] == "An observatory gate is tested before rehearsal."


def test_linked_project_passage_is_pinned_and_deduped_against_document_search(qa, vector_index, store, monkeypatch):
    from src.chatbox.qa_evidence import QaEvidence, open_snapshot
    from src.file_mgmt import storage_paths, store as files
    from src.qa import search_index
    from src.qa.store import connection
    from src.rag.retriever import RetrievedChunk

    data = saved_exchange(qa)
    text = "Inspect the lower hinge before opening the gate."
    file_id, version_id, chunk_id = "sample-file", "sample-v1", "973943ae-af38-4e6d-80b4-de8fe3d42ea4"
    name = "Gate guide.txt"
    monkeypatch.setattr(storage_paths, "COLLECTIONS_DIR", files.COLLECTIONS_DIR)
    storage_paths.write_version_blob(PROJECT, file_id, version_id, text.encode(), name)
    reference = {
        "kind": "project", "file_id": file_id, "version_id": version_id, "chunk_id": chunk_id,
        "filename": name, "excerpt": text,
    }
    with connection(PROJECT, "update") as conn, conn:
        conn.execute("PRAGMA defer_foreign_keys=ON")
        conn.execute(
            "INSERT INTO files (file_id,current_version_id,created_by) VALUES (?,?,'example')",
            (file_id, "sample-v2"),
        )
        conn.executemany(
            """INSERT INTO file_versions (version_id,file_id,version_no,storage_file_id,created_by,created_at)
               VALUES (?,?,?,?,'example','2026-01-01T00:00:00Z')""",
            [(version_id, file_id, 1, name), ("sample-v2", file_id, 2, name)],
        )
        reply = data["questions"][0]["replies"][0]
        conn.execute("INSERT INTO qa_reply_sources VALUES(?,?)", (reply["text_revision_id"], json.dumps([reference])))
    db, _ = vector_index
    meta = {"id": chunk_id, "collection": PROJECT, "file_id": file_id, "version_id": version_id, "char_offset": 14}
    db.upsert_points(PROJECT, [chunk_id], [[1.0, 0.0, 0.0]], [{**meta, "text": text}])
    search_index.sync_project(PROJECT)
    session = store.create_session(title="Example session")
    registry = EvidenceRegistry(session.id)
    turn = QaEvidence(store, session.id)
    retrieval = SimpleNamespace(
        reranker=Reranker(Ranker()), llm=SimpleNamespace(generate=lambda *_args, **_kwargs: json.dumps(
            {"relevant_indices": [0], "retained_info": "", "gap_analysis": ""}
        )),
    )
    bundle = turn.capture(search_index.retrieve(PROJECT, "gate", 10), [], registry, "gate", retrieval)
    assert text in bundle.context(registry)
    link = next(record for record in registry.records() if record["kind"] == "document")
    assert link["source"]["qa_column_id"] == reply["column_id"]
    assert link["location"]["char_offset"] == 14

    turn.finish_search(SimpleNamespace(qa_hits=[]), [RetrievedChunk(text, 1.0, meta)], registry, "gate", retrieval, text)
    assert text not in bundle.context(registry)
    snapshot = open_snapshot(store, session.id, bundle.posts[0].snapshot_id)
    saved = snapshot["replies"][0]["sources"][0]
    assert saved["version_id"] == version_id and saved["historical"] and saved["excerpt"] == text


def test_revoked_qa_scope_redacts_saved_answer_and_preview(qa, vector_index, store, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.routes.sessions import router
    from src.chatbox.qa_evidence import QaEvidence
    from src.qa import search_index
    from src.services import services

    saved_exchange(qa)
    search_index.sync_project(PROJECT)
    session = store.create_session(title="Example session")
    registry = EvidenceRegistry(session.id)
    turn = QaEvidence(store, session.id)
    bundle = turn.capture(search_index.retrieve(PROJECT, "gate", 10), [], registry, "gate", None)
    store.add_message(session.id, "assistant", ANSWER, sources=turn.sources(registry),
                      metadata={**turn.metadata(), "citations": registry.records()})
    monkeypatch.setattr(services, "session_store", store)
    monkeypatch.setattr(search_index, "can_read", lambda *_args, **_kwargs: False)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)
    preview = client.get(f"/api/sessions/{session.id}/qa-sources/{bundle.posts[0].snapshot_id}")
    assert preview.status_code == 404
    history = client.get(f"/api/sessions/{session.id}")
    assert history.status_code == 200
    assert ANSWER not in history.text
    assert not history.json()["messages"][0]["sources"]
