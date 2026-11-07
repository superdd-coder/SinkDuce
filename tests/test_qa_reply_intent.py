"""Automatic reply assistance using invented gardening correspondence."""

import json

import pytest

from tests.qa_agentic_helper import install_agentic
from tests.test_qa import create, mutate, qa_fixture  # noqa: F401


@pytest.mark.parametrize(
    "text,decision,operation,searches",
    [
        ("", None, "draft", 1),
        ("", {"kind": "guidance"}, "draft", 1),
        (
            "Explain the visiting schedule concisely.",
            {"kind": "guidance"},
            "draft",
            1,
        ),
        ("The garden opens on Saturdays.", {"kind": "answer"}, "polish", 0),
        (
            "The garden opens on Saturdays. Make this more formal.",
            {"kind": "mixed", "needs_retrieval": False},
            "revise",
            0,
        ),
        (
            "The garden opens on Saturdays. Add the visiting hours from the project files.",
            {"kind": "mixed", "needs_retrieval": True},
            "revise",
            1,
        ),
        ("Saturday opening.", {"kind": "unclear", "needs_retrieval": True}, "polish", 0),
        ("Saturday opening.", {"unexpected": "response"}, "polish", 0),
    ],
)
def test_reply_intent_runs_automatically_and_only_searches_when_needed(
    qa, monkeypatch, text, decision, operation, searches
):
    from src.rag.retriever import RetrievedChunk
    from src.services import services

    d = mutate(
        qa, create(qa), "/questions", {"text": "Describe the botanical garden visiting schedule."}
    )
    q, col = d["questions"][0], d["columns"][0]
    seen, retrieved = [], []

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = json.loads(prompt.split("\n", 1)[1])
            if packet.get("stage") == "reply_review":
                return json.dumps({"issues": []})
            seen.append(packet)
            if packet.get("stage") == "reply_intent":
                return json.dumps(decision)
            return json.dumps(
                {
                    "body": "The garden is open on Saturdays.",
                    "sources": [1],
                    "insufficient_evidence": False,
                }
            )

    class Retriever:
        def retrieve(self, query, **kwargs):
            retrieved.append(kwargs)
            return [
                RetrievedChunk(
                    text="The garden opens on Saturdays.",
                    score=0.9,
                    metadata={
                        "file_id": "fictional_file",
                        "version_id": "fictional_version",
                        "source": "Garden guide",
                        "id": "fictional_chunk",
                    },
                )
            ]

    monkeypatch.setattr(services, "llm", LLM())
    agentic = install_agentic(monkeypatch, services, Retriever())
    monkeypatch.setattr(services, "reranker", None)
    monkeypatch.setattr(services, "db", None)
    client, base = qa
    r = client.post(
        f"{base}/lists/{d['id']}/questions/{q['id']}/drafts/{col['id']}/generate",
        json={"expected_revision": d["revision"], "instructions": text},
    )
    assert r.status_code == 200, r.text
    proposal = r.json()
    assert proposal["operation"] == operation
    assert len(retrieved) == searches
    assert len(agentic.calls) == searches
    if retrieved:
        assert retrieved[0]["collection"] == "col_qa"
    assert len([p for p in seen if p.get("stage") == "reply_intent"]) == 1
    assert seen[-1]["input_text"] == text
    assert seen[-1]["operation"] == operation
    assert len(seen[-1]["evidence"]) == searches
    assert proposal["insufficient_evidence"] is False
    assert seen[-1]["retrieval_status"] == ("ok" if searches else "skipped")
    assert len(proposal["sources"]) == searches
    current = client.get(f"{base}/lists/{d['id']}").json()
    assert current["revision"] == d["revision"]
    assert current["questions"][0]["replies"][0]["body"] == ""


def test_retired_material_space_is_excluded_from_new_ai_replies(qa, monkeypatch):
    from src.rag.retriever import RetrievedChunk
    from src.rag.source_scope import source_filter
    from src.services import services

    d = mutate(qa, create(qa), "/questions", {"text": "Describe the herb display."})
    q, col = d["questions"][0], d["columns"][0]
    seen, scopes = [], []

    class Retriever:
        def retrieve(self, *args, **kwargs):
            scopes.append(source_filter().model_dump())
            return [
                RetrievedChunk(
                    text="Hidden material content.",
                    score=0.9,
                    metadata={
                        "source_kind": "qa_material",
                        "qa_list_id": d["id"],
                        "qa_material_id": "old_material",
                        "qa_material_version_id": "old_version",
                    },
                )
            ]

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = json.loads(prompt.split("\n", 1)[1])
            if packet.get("stage") == "reply_review":
                return json.dumps({"issues": []})
            seen.append(packet)
            if packet.get("stage") == "reply_intent":
                return json.dumps({"kind": "guidance"})
            return json.dumps(
                {
                    "body": "Please supply the display details.",
                    "sources": [1],
                    "insufficient_evidence": True,
                }
            )

    monkeypatch.setattr(services, "llm", LLM())
    agentic = install_agentic(monkeypatch, services, Retriever())
    monkeypatch.setattr(services, "reranker", None)
    monkeypatch.setattr(services, "db", None)
    client, base = qa
    r = client.post(
        f"{base}/lists/{d['id']}/questions/{q['id']}/drafts/{col['id']}/generate",
        json={"expected_revision": d["revision"]},
    )
    assert r.status_code == 200, r.text
    assert scopes and '"any": []' in json.dumps(scopes[0])
    assert len(agentic.calls) == 1
    assert seen[-1]["evidence"] == []
    assert r.json()["sources"] == []


def test_polishing_a_saved_answer_preserves_its_existing_citations(qa, monkeypatch):
    from src.qa import store
    from src.services import services

    d = mutate(qa, create(qa), "/questions", {"text": "Confirm the greenhouse opening day."})
    qid, cid = d["questions"][0]["id"], d["columns"][0]["id"]
    answer = "The greenhouse opens on Saturday."
    d = mutate(qa, d, f"/questions/{qid}/replies/{cid}", {"body": answer}, "put")
    source = {
        "kind": "project",
        "file_id": "fictional_file",
        "version_id": "fictional_version",
        "filename": "Garden guide",
        "excerpt": answer,
        "chunk_id": "fictional_chunk",
    }
    with store.connection("col_qa", "update") as conn, conn:
        conn.execute(
            "INSERT INTO qa_reply_sources VALUES(?,?)",
            (d["questions"][0]["replies"][0]["text_revision_id"], json.dumps([source])),
        )

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = json.loads(prompt.split("\n", 1)[1])
            if packet.get("stage") == "reply_review":
                return json.dumps({"issues": []})
            if packet.get("stage") == "reply_intent":
                return json.dumps({"kind": "answer"})
            return json.dumps({"body": "The greenhouse is open on Saturday.", "sources": []})

    monkeypatch.setattr(services, "llm", LLM())
    monkeypatch.setattr(services, "agentic_query", None)
    client, base = qa
    r = client.post(
        f"{base}/lists/{d['id']}/questions/{qid}/drafts/{cid}/generate",
        json={"expected_revision": d["revision"], "instructions": answer},
    )
    assert r.status_code == 200, r.text
    assert r.json()["operation"] == "polish"
    assert r.json()["sources"] == [source]


def conversation(qa):
    d = mutate(
        qa, create(qa), "/questions", {"text": "Describe the museum ticket booking process."}
    )
    qid = d["questions"][0]["id"]
    first, current = [c["id"] for c in d["columns"]]
    d = mutate(qa, d, f"/columns/{first}", {"label": "Visitor desk response"}, "patch")
    d = mutate(qa, d, f"/columns/{current}", {"label": "Tour coordinator follow-up"}, "patch")
    body = "Group tickets are available. The booking deadline is still being decided."
    d = mutate(qa, d, f"/questions/{qid}/replies/{first}", {"body": body}, "put")
    d = mutate(qa, d, "/columns", {"labels": ["Later desk response"]})
    d = mutate(
        qa,
        d,
        f"/questions/{qid}/replies/{d['columns'][-1]['id']}",
        {"body": "A later decision."},
        "put",
    )
    return d, qid, current, body


def test_followup_packet_identifies_current_turn_and_excludes_future_turns(qa, monkeypatch):
    from src.services import services

    d, qid, cid, body = conversation(qa)
    seen = []

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = json.loads(prompt.split("\n", 1)[1])
            if packet.get("stage") == "reply_review":
                return json.dumps({"issues": []})
            if packet.get("stage") == "reply_intent":
                return json.dumps({"kind": "guidance"})
            seen.append(packet)
            return json.dumps(
                {"body": "Please let us know when the booking deadline is confirmed."}
            )

    monkeypatch.setattr(services, "llm", LLM())
    monkeypatch.setattr(services, "agentic_query", None)
    client, base = qa
    r = client.post(
        f"{base}/lists/{d['id']}/questions/{qid}/drafts/{cid}/generate",
        json={"expected_revision": d["revision"]},
    )
    assert r.status_code == 200, r.text
    assert seen[0]["conversation_stage"] == "continuation"
    assert seen[0]["reply_position"] == 2
    assert seen[0]["reply_column"] == "Tour coordinator follow-up"
    assert seen[0]["previous_replies"] == [
        {
            "position": 1,
            "label": "Visitor desk response",
            "body": body,
            "author_relation": "counterparty",
        }
    ]
    assert "A later decision." not in json.dumps(seen)


@pytest.mark.parametrize("explicit_guidance", [False, True])
def test_empty_followup_retries_copied_history_without_overriding_explicit_guidance(
    qa, monkeypatch, explicit_guidance
):
    from src.services import services

    d, qid, cid, body = conversation(qa)
    seen = []
    followup = "Please confirm when the booking deadline will be available."

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = json.loads(prompt.split("\n", 1)[1])
            if packet.get("stage") == "reply_review":
                return json.dumps({"issues": []})
            if packet.get("stage") == "reply_intent":
                return json.dumps({"kind": "guidance"})
            seen.append(packet)
            return json.dumps(
                {"body": body.upper().replace(" ", "\n") if len(seen) == 1 else followup}
            )

    monkeypatch.setattr(services, "llm", LLM())
    monkeypatch.setattr(services, "agentic_query", None)
    client, base = qa
    r = client.post(
        f"{base}/lists/{d['id']}/questions/{qid}/drafts/{cid}/generate",
        json={
            "expected_revision": d["revision"],
            "instructions": "Quote the previous answer verbatim." if explicit_guidance else "",
        },
    )
    assert r.status_code == 200, r.text
    assert len(seen) == (1 if explicit_guidance else 2)
    if not explicit_guidance:
        assert r.json()["body"] == followup
        assert seen[1]["retry_reason"] == "repeated_previous_turn"
    current = client.get(f"{base}/lists/{d['id']}").json()
    assert current["revision"] == d["revision"]
    assert current["questions"][0]["replies"][1]["body"] == ""


def test_repeated_copy_after_one_retry_is_not_offered_as_a_new_reply(qa, monkeypatch):
    from src.qa import store
    from src.services import services

    d, qid, cid, body = conversation(qa)
    calls = []

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = json.loads(prompt.split("\n", 1)[1])
            if packet.get("stage") == "reply_review":
                return json.dumps({"issues": []})
            if packet.get("stage") == "reply_intent":
                return json.dumps({"kind": "guidance"})
            calls.append(1)
            return json.dumps({"body": body})

    monkeypatch.setattr(services, "llm", LLM())
    monkeypatch.setattr(services, "agentic_query", None)
    client, base = qa
    r = client.post(
        f"{base}/lists/{d['id']}/questions/{qid}/drafts/{cid}/generate",
        json={"expected_revision": d["revision"]},
    )
    assert r.status_code == 503
    assert "ai_repeated_reply" in r.text
    assert len(calls) == 2
    with store.connection("col_qa") as conn:
        assert conn.execute("SELECT count(*) FROM qa_draft_proposals").fetchone()[0] == 0
