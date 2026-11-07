"""Live Q&A assistance emits bounded progress without leaking provider internals."""

import asyncio
import json
import threading

import pytest
from fastapi import HTTPException

from tests.test_qa import create, mutate, qa_fixture  # noqa: F401


def events(body):
    parsed = []
    for block in body.split("\n\n"):
        lines = block.splitlines()
        name = next((line[7:] for line in lines if line.startswith("event: ")), None)
        payload = next((line[6:] for line in lines if line.startswith("data: ")), None)
        if name and payload:
            parsed.append((name, json.loads(payload)))
    return parsed


def test_draft_stream_emits_actual_stages_then_proposal(qa, monkeypatch):
    from src.qa import drafting

    data = mutate(qa, create(qa), "/questions", {"text": "Confirm the museum booking window."})
    question, column = data["questions"][0]["id"], data["columns"][0]["id"]
    proposal = {"id": "example_proposal", "body": "Please confirm the booking window."}

    def generate(collection, lid, qid, cid, request, *, on_progress, check_cancel):
        assert (collection, lid, qid, cid) == ("col_qa", data["id"], question, column)
        assert request.expected_revision == data["revision"]
        check_cancel()
        on_progress({"stage": "intent", "raw_reasoning": "private provider trace"})
        on_progress({"stage": "planning", "content": "private retrieval plan"})
        on_progress({"stage": "grading", "count": 3, "raw_reasoning": "private evidence review"})
        on_progress({"stage": "drafting", "operation": "polish"})
        on_progress({"stage": "finalizing", "count": 1})
        return proposal

    monkeypatch.setattr(drafting, "generate", generate)
    client, base = qa
    response = client.post(
        f"{base}/lists/{data['id']}/questions/{question}/drafts/{column}/generate/stream",
        json={"expected_revision": data["revision"]},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert events(response.text) == [
        ("progress", {"stage": "intent"}),
        ("progress", {"stage": "planning"}),
        ("progress", {"stage": "grading", "count": 3}),
        ("progress", {"stage": "drafting", "operation": "polish"}),
        ("progress", {"stage": "finalizing", "count": 1}),
        ("result", proposal),
    ]
    assert "private" not in response.text


@pytest.mark.parametrize(
    "failure,code,status",
    [
        (RuntimeError("private provider endpoint and request"), "ai_unavailable", 503),
        (HTTPException(409, detail={"code": "stale_draft", "message": "private text"}), "stale_draft", 409),
        (HTTPException(409, detail={"code": "material_not_ready", "message": "private text"}), "material_not_ready", 409),
        (HTTPException(409, detail={"code": "material_archived", "message": "private text"}), "material_archived", 409),
    ],
)
def test_draft_stream_reports_safe_error_codes(qa, monkeypatch, failure, code, status):
    from src.qa import drafting

    data = mutate(qa, create(qa), "/questions", {"text": "Provide the seed catalog."})

    def generate(*args, **kwargs):
        raise failure

    monkeypatch.setattr(drafting, "generate", generate)
    client, base = qa
    response = client.post(
        f"{base}/lists/{data['id']}/questions/{data['questions'][0]['id']}/drafts/{data['columns'][0]['id']}/generate/stream",
        json={"expected_revision": data["revision"]},
    )
    assert events(response.text) == [("error", {"code": code, "status": status})]
    assert "private" not in response.text


class ConnectedRequest:
    async def is_disconnected(self):
        return False


@pytest.mark.asyncio
async def test_draft_stream_captures_actor_and_source_scope_before_worker(qa, monkeypatch):
    from src.identity import Actor, get_actor, reset_actor, set_actor
    from src.qa import draft_stream, drafting
    from src.qa.models import DraftGenerate
    from src.rag.source_scope import qa_source_scope, source_filter

    seen = []

    def generate(*args, **kwargs):
        seen.append((get_actor(), source_filter().model_dump()))
        return {"id": "example_proposal"}

    monkeypatch.setattr(drafting, "generate", generate)
    actor = Actor(id="example_reader", kind="key", role_id="member")
    token = set_actor(actor)
    try:
        with qa_source_scope("example_list", ["example_version"]):
            response = draft_stream.response(
                ConnectedRequest(), "col_qa", "example_list", "example_question", "example_column",
                DraftGenerate(expected_revision=1),
            )
    finally:
        reset_actor(token)
    body = "".join([chunk async for chunk in response.body_iterator])
    assert events(body) == [("result", {"id": "example_proposal"})]
    assert seen[0][0] == actor
    assert "example_version" in json.dumps(seen[0][1])


@pytest.mark.asyncio
async def test_closing_draft_stream_stops_before_proposal_persistence(monkeypatch):
    from src.qa import draft_stream, drafting
    from src.qa.models import DraftGenerate

    release, finished = threading.Event(), threading.Event()
    persisted = []

    def generate(*args, on_progress, **kwargs):
        try:
            on_progress({"stage": "drafting", "operation": "draft"})
            assert release.wait(2)
            on_progress({"stage": "finalizing"})
            persisted.append(True)
            return {"id": "example_proposal"}
        finally:
            finished.set()

    monkeypatch.setattr(drafting, "generate", generate)
    response = draft_stream.response(
        ConnectedRequest(), "col_qa", "example_list", "example_question", "example_column",
        DraftGenerate(expected_revision=1),
    )
    iterator = response.body_iterator
    first = await anext(iterator)
    assert events(first) == [("progress", {"stage": "drafting", "operation": "draft"})]
    await iterator.aclose()
    release.set()
    assert await asyncio.to_thread(finished.wait, 2)
    assert persisted == []


@pytest.mark.asyncio
async def test_slow_cancelled_provider_keeps_worker_slot_until_it_finishes(monkeypatch):
    from src.qa import draft_stream, drafting
    from src.qa.models import DraftGenerate

    release, finished = threading.Event(), threading.Event()
    calls = []
    monkeypatch.setattr(draft_stream, "_workers", threading.BoundedSemaphore(1))

    def generate(*args, on_progress, **kwargs):
        calls.append(True)
        try:
            on_progress({"stage": "drafting"})
            assert release.wait(2)
            on_progress({"stage": "finalizing"})
            return {"id": "example_proposal"}
        finally:
            finished.set()

    monkeypatch.setattr(drafting, "generate", generate)

    def new_response():
        return draft_stream.response(
            ConnectedRequest(), "col_qa", "example_list", "example_question", "example_column",
            DraftGenerate(expected_revision=1),
        )

    first = new_response().body_iterator
    await anext(first)
    await first.aclose()
    try:
        body = "".join([chunk async for chunk in new_response().body_iterator])
        assert events(body) == [("error", {"code": "ai_busy", "status": 503})]
        assert calls == [True]
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 2)
