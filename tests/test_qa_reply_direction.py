"""Reply direction and shared retrieval evidence use fictional museum data."""

import json

import pytest

from tests.qa_agentic_helper import install_agentic
from tests.test_qa import create, mutate, qa_fixture  # noqa: F401


def exchange(qa, label="Organizer follow-up"):
    d = mutate(qa, create(qa), "/questions", {"text": "Explain the gallery reservation capacity."})
    qid = d["questions"][0]["id"]
    first, current = [c["id"] for c in d["columns"]]
    d = mutate(qa, d, f"/columns/{first}", {"label": "Venue response"}, "patch")
    d = mutate(qa, d, f"/columns/{current}", {"label": label}, "patch")
    d = mutate(
        qa,
        d,
        f"/questions/{qid}/replies/{first}",
        {"body": "Please consult the Capacity Plan worksheet for the reservation limits."},
        "put",
    )
    return d, qid, current


def generate(qa, d, qid, cid, text=""):
    client, base = qa
    return client.post(
        f"{base}/lists/{d['id']}/questions/{qid}/drafts/{cid}/generate",
        json={"expected_revision": d["revision"], "instructions": text},
    )


@pytest.mark.parametrize(
    "label,action",
    [
        ("Organizer follow-up", "follow_up"),
        ("Venue answer", "answer"),
        ("Response to follow-up", "answer"),
    ],
)
def test_reply_target_is_fixed_independently_of_free_party_inference(
    qa, monkeypatch, label, action
):
    from src.services import services

    d, qid, cid = exchange(qa, label)
    packets = []

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = json.loads(prompt.split("\n", 1)[1])
            if packet.get("stage") == "reply_review":
                return json.dumps({"issues": []})
            packets.append(packet)
            if packet.get("stage") == "reply_intent":
                return json.dumps(
                    {
                        "kind": "guidance",
                        "needs_retrieval": False,
                        "contribution": action,
                        "turn_relations": [{"position": 1, "author_relation": "same_party"}],
                    }
                )
            return json.dumps({"body": "Please clarify the applicable reservation limit."})

    monkeypatch.setattr(services, "llm", LLM())
    monkeypatch.setattr(services, "agentic_query", None)
    response = generate(qa, d, qid, cid)
    assert response.status_code == 200, response.text
    for packet in packets:
        assert packet["responding_to"]["position"] == 1
        assert packet["responding_to"]["label"] == "Venue response"
    assert packets[-1]["contribution"] == action
    assert packets[-1]["previous_replies"][-1]["author_relation"] == "counterparty"


@pytest.mark.parametrize("intermediate", ["empty", "removed"])
def test_reply_target_skips_unavailable_columns_and_future_turns(qa, monkeypatch, intermediate):
    from src.services import services

    d, qid, middle = exchange(qa)
    if intermediate == "removed":
        d = mutate(qa, d, f"/questions/{qid}/replies/{middle}", {"body": "A removed reply."}, "put")
        d = mutate(qa, d, f"/columns/{middle}", {"included": False}, "patch")
    d = mutate(qa, d, "/columns", {"labels": ["Organizer follow-up", "Future venue response"]})
    current, future = [c["id"] for c in d["columns"][-2:]]
    d = mutate(
        qa, d, f"/questions/{qid}/replies/{future}", {"body": "Future-only statement."}, "put"
    )
    seen = []

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = json.loads(prompt.split("\n", 1)[1])
            if packet.get("stage") == "reply_intent":
                return json.dumps({"kind": "guidance", "needs_retrieval": False})
            if packet.get("stage") == "reply_review":
                return json.dumps({"issues": []})
            seen.append(packet)
            return json.dumps({"body": "Please clarify the reservation limit."})

    monkeypatch.setattr(services, "llm", LLM())
    monkeypatch.setattr(services, "agentic_query", None)
    response = generate(qa, d, qid, current)
    assert response.status_code == 200, response.text
    assert seen[-1]["responding_to"]["position"] == 1
    assert "Future-only statement." not in json.dumps(seen)


@pytest.mark.parametrize("keep_evidence", [True, False])
def test_shared_retrieval_keeps_previous_turn_focus_and_only_retained_sheet_evidence(
    qa, monkeypatch, keep_evidence
):
    from src.file_mgmt import service as files
    from src.rag.retriever import RetrievedChunk
    from src.services import services

    d, qid, cid = exchange(qa)
    queries, packets = [], []
    def unexpected_catalog(*args, **kwargs):
        raise AssertionError("Shared retrieval must not preselect a file directory")

    monkeypatch.setattr(files, "list_files_lean", unexpected_catalog)

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = json.loads(prompt.split("\n", 1)[1])
            if packet.get("stage") == "reply_review":
                return json.dumps({"issues": []})
            packets.append(packet)
            if packet.get("stage") == "reply_intent":
                return json.dumps(
                    {
                        "kind": "guidance",
                        "needs_retrieval": True,
                    }
                )
            return json.dumps(
                {
                    "body": "Please clarify which reservation limit is applicable.",
                    "sources": [1] if keep_evidence else [],
                    "insufficient_evidence": not keep_evidence,
                }
            )

    class Retriever:
        def retrieve(self, query, **kwargs):
            queries.append(query)
            return [
                RetrievedChunk(
                    text="The worksheet contains a provisional capacity allowance.",
                    score=0.8,
                    metadata={
                        "file_id": "fictional_file",
                        "version_id": "fictional_version",
                        "id": "fictional_chunk",
                        "source": "Exhibition model.xlsx",
                        "sheet_name": "Capacity Plan",
                    },
                )
            ]

    monkeypatch.setattr(services, "llm", LLM())
    agentic = install_agentic(
        monkeypatch, services, Retriever(), retain=lambda rows: rows if keep_evidence else []
    )
    monkeypatch.setattr(services, "reranker", None)
    response = generate(
        qa, d, qid, cid, "Check the applicable capacity assumption before drafting a follow-up."
    )
    assert response.status_code == 200, response.text
    assert len(queries) == 1
    assert len(agentic.calls) == 1
    assert agentic.calls[0][1] == ["col_qa"]
    assert agentic.calls[0][2]["generate_answer"] is False
    assert "Capacity Plan" in queries[0]
    assert "available_sources" not in packets[0]
    assert [e["sheet_name"] for e in packets[-1]["evidence"]] == (["Capacity Plan"] if keep_evidence else [])
    assert packets[-1]["retrieval_status"] == ("ok" if keep_evidence else "empty")
    assert response.json()["insufficient_evidence"] is not keep_evidence
    if keep_evidence:
        assert packets[-1]["evidence"][0]["file_id"] == "fictional_file"
        assert packets[-1]["evidence"][0]["version_id"] == "fictional_version"
        assert response.json()["sources"][0]["version_id"] == "fictional_version"
    else:
        assert response.json()["sources"] == []
        assert "provisional capacity allowance" not in json.dumps(packets[-1])


@pytest.mark.parametrize(
    "incoming,kind,operation",
    [
        ("", "guidance", "draft"),
        ("Please clarify the applicable reservation limit.", "message", "polish"),
        ("Please clarify the applicable reservation limit. Make this shorter.", "mixed", "revise"),
    ],
)
def test_finished_draft_is_offered_without_a_second_ai_approval(
    qa, monkeypatch, incoming, kind, operation
):
    from src.qa import store
    from src.services import services

    d, qid, cid = exchange(qa)
    calls = []
    body = "Please clarify the applicable reservation limit."

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = json.loads(prompt.split("\n", 1)[1])
            calls.append(packet)
            if packet.get("stage") == "reply_intent":
                return json.dumps(
                    {"kind": kind, "needs_retrieval": False, "contribution": "follow_up"}
                )
            if len(calls) > 2:
                raise AssertionError("A completed draft must not require another AI approval")
            return json.dumps({"body": body})

    monkeypatch.setattr(services, "llm", LLM())
    monkeypatch.setattr(services, "agentic_query", None)
    response = generate(qa, d, qid, cid, incoming)
    assert response.status_code == 200, response.text
    assert response.json()["body"] == body
    assert response.json()["operation"] == operation
    assert len(calls) == 2
    with store.connection("col_qa") as conn:
        assert conn.execute("SELECT count(*) FROM qa_draft_proposals").fetchone()[0] == 1
    client, base = qa
    current = client.get(f"{base}/lists/{d['id']}").json()
    reply = next(r for r in current["questions"][0]["replies"] if r["column_id"] == cid)
    assert reply["body"] == ""


@pytest.mark.parametrize("provider_failure", [False, True])
def test_missing_evidence_returns_a_focused_draft_with_a_notice(qa, monkeypatch, provider_failure):
    from src.services import services

    d, qid, cid = exchange(qa)
    calls = []

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = json.loads(prompt.split("\n", 1)[1])
            calls.append(packet)
            if packet.get("stage") == "reply_intent":
                return json.dumps({"kind": "guidance", "needs_retrieval": True})
            if len(calls) > 2:
                raise AssertionError("Missing evidence is not a generation approval failure")
            return json.dumps(
                {
                    "body": "Please provide the capacity calculation and its assumptions.",
                    "insufficient_evidence": True,
                }
            )

    class Retriever:
        def retrieve(self, *args, **kwargs):
            if provider_failure:
                raise RuntimeError("Synthetic retrieval failure")
            return []

    monkeypatch.setattr(services, "llm", LLM())
    agentic = install_agentic(monkeypatch, services, Retriever())
    monkeypatch.setattr(services, "reranker", None)
    response = generate(qa, d, qid, cid)
    assert response.status_code == 200, response.text
    assert response.json()["insufficient_evidence"] is True
    assert response.json()["sources"] == []
    assert len(agentic.calls) == 1
    assert len(calls) == 2
    assert calls[-1]["retrieval_status"] == ("unavailable" if provider_failure else "empty")
