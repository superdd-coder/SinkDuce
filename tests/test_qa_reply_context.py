"""Existing outward correspondence stays in the current writer's editing context."""

import json

from tests.qa_agentic_helper import install_agentic
from tests.test_qa import create, mutate, qa_fixture  # noqa: F401


def test_existing_outward_request_is_polished_in_its_column_context(qa, monkeypatch):
    from src.services import services

    d = mutate(qa, create(qa), "/questions", {"text": "Describe the museum access arrangements."})
    qid, first = d["questions"][0]["id"], d["columns"][0]["id"]
    d = mutate(
        qa,
        d,
        f"/questions/{qid}/replies/{first}",
        {"body": "The access fee includes guided entry."},
        "put",
    )
    cid = d["columns"][1]["id"]
    d = mutate(qa, d, f"/columns/{cid}", {"label": "Organizer follow-up"}, "patch")
    incoming = "Please clarify which guided-entry services the fee includes."
    seen = []

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = json.loads(prompt.split("\n", 1)[1])
            if packet.get("stage") == "reply_review":
                return json.dumps({"issues": []})
            seen.append(packet)
            if packet.get("stage") == "reply_intent":
                return json.dumps(
                    {"kind": "message", "speech_act": "question", "needs_retrieval": True}
                )
            return json.dumps(
                {"body": "Could you clarify which guided-entry services are included in the fee?"}
            )

    class Retriever:
        def retrieve(self, *args, **kwargs):
            raise AssertionError("Existing correspondence needs editing, not retrieval")

    monkeypatch.setattr(services, "llm", LLM())
    agentic = install_agentic(monkeypatch, services, Retriever())
    client, base = qa
    response = client.post(
        f"{base}/lists/{d['id']}/questions/{qid}/drafts/{cid}/generate",
        json={"expected_revision": d["revision"], "instructions": incoming},
    )
    assert response.status_code == 200, response.text
    assert response.json()["operation"] == "polish"
    intent = seen[0]
    assert intent["reply_column"] == "Organizer follow-up"
    assert intent["reply_position"] == 2
    assert intent["previous_replies"][0]["body"] == "The access fee includes guided entry."
    assert seen[-1]["content_scope"] == "input_only"
    assert seen[-1]["speech_act"] == "question"
    assert seen[-1]["evidence"] == []
    assert agentic.calls == []


def test_factual_expansion_searches_current_focus_and_emits_actual_stages(qa, monkeypatch):
    from src.qa import drafting
    from src.qa.models import DraftGenerate
    from src.services import services

    d = mutate(qa, create(qa), "/questions", {"text": "Describe museum operations."})
    qid, cid = d["questions"][0]["id"], d["columns"][0]["id"]
    incoming = "Check the project records for the current sculpture conservation schedule."
    searched, stages, packets = [], [], []

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = json.loads(prompt.split("\n", 1)[1])
            if packet.get("stage") == "reply_review":
                return json.dumps({"issues": []})
            packets.append(packet)
            if packet.get("stage") == "reply_intent":
                return json.dumps({"kind": "guidance"})
            return json.dumps(
                {
                    "body": "Please confirm the current conservation schedule.",
                    "insufficient_evidence": True,
                }
            )

    class Retriever:
        def retrieve(self, query, **kwargs):
            searched.append(query)
            return []

    monkeypatch.setattr(services, "llm", LLM())
    agentic = install_agentic(monkeypatch, services, Retriever())
    monkeypatch.setattr(services, "reranker", None)
    result = drafting.generate(
        "col_qa",
        d["id"],
        qid,
        cid,
        DraftGenerate(expected_revision=d["revision"], instructions=incoming),
        on_progress=stages.append,
    )
    assert searched[0].startswith(incoming)
    assert len(agentic.calls) == 1
    assert d["questions"][0]["text"] in searched[0]
    assert packets[-1]["retrieval_status"] == "empty"
    assert [p["stage"] for p in stages] == [
        "preparing",
        "intent",
        "retrieving",
        "drafting",
        "finalizing",
    ]
    assert all(set(p) <= {"stage", "operation", "count"} for p in stages)
    assert result["insufficient_evidence"] is True
