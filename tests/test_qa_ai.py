"""Provider-independent AI validation and draft persistence using fictional data."""

import json

import pytest

from tests.qa_agentic_helper import install_agentic
from tests.test_qa import create, mutate, qa_fixture  # noqa: F401
from tests.test_qa_excel import upload


def test_ai_mapping_uses_samples_and_preserves_empty_reply_columns(qa, monkeypatch):
    from src.services import services

    seen = []

    class LLM:
        def generate(self, prompt, **kwargs):
            seen.append((prompt, kwargs["system"]))
            return json.dumps({"header_row": 1, "question": 1, "number": 0, "replies": [2, 3]})

    monkeypatch.setattr(services, "llm", LLM())
    d = create(qa)
    p = upload(
        qa,
        d,
        [
            ["Ref", "Enquiry text", "园方说明", "Operator follow up"],
            ["8", "Explain the orchard planting schedule.", "Spring", None],
        ],
    )
    assert p["mapping"]["question"] == 1
    assert p["mapping"]["replies"] == [2, 3]
    assert seen and "Explain the orchard planting schedule." in seen[0][0]


def test_ai_rewritten_hidden_question_proposal_is_reviewable_and_scoped(qa, monkeypatch):
    from src.services import services

    monkeypatch.setattr(services, "llm", None)
    d = create(qa)
    d = mutate(qa, d, "/questions", {"text": "Describe the seed archive access rules."})
    qid = d["questions"][0]["id"]
    d = mutate(qa, d, f"/questions/{qid}/membership", {"included": False})
    p = upload(qa, d, [["Question"], ["Please clarify who may access the seed archive."]])

    class LLM:
        def generate(self, prompt, **kwargs):
            assert qid in prompt
            return json.dumps(
                {
                    "rows": [
                        {
                            "key": p["rows"][0]["key"],
                            "target": qid,
                            "certainty": "review",
                            "reason": "Same archive, access wording changed.",
                            "summary": "Seed archive access",
                            "reply_summary": "",
                            "type": "Clarify",
                        }
                    ],
                    "columns": [],
                }
            )

    monkeypatch.setattr(services, "llm", LLM())
    client, base = qa
    r = client.post(
        f"{base}/lists/{d['id']}/imports/{p['id']}/analyze",
        json={"plan_revision": p["plan_revision"]},
    )
    assert r.status_code == 200, r.text
    result = r.json()
    key = p["rows"][0]["key"]
    assert result["suggestions"][key]["target"] == qid
    assert result["matches"][key] is None
    assert client.get(f"{base}/lists/{d['id']}").json()["revision"] == d["revision"]


def test_invalid_ai_identity_falls_back_to_manual_review(qa, monkeypatch):
    from src.services import services

    monkeypatch.setattr(services, "llm", None)
    d = create(qa)
    d = mutate(qa, d, "/questions", {"text": "Explain the greenhouse inspection interval."})
    p = upload(qa, d, [["Question"], ["Describe the greenhouse review frequency."]])

    class LLM:
        def generate(self, *args, **kwargs):
            return json.dumps(
                {
                    "rows": [
                        {
                            "key": p["rows"][0]["key"],
                            "target": "foreign-identity",
                            "certainty": "high",
                            "summary": "Inspection",
                            "reply_summary": "",
                            "type": "Clarify",
                        }
                    ],
                    "columns": [],
                }
            )

    monkeypatch.setattr(services, "llm", LLM())
    client, base = qa
    r = client.post(
        f"{base}/lists/{d['id']}/imports/{p['id']}/analyze",
        json={"plan_revision": p["plan_revision"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["matches"][p["rows"][0]["key"]] is None


def test_draft_is_persistent_private_and_does_not_change_formal_reply(qa):
    d = create(qa)
    d = mutate(qa, d, "/questions", {"text": "Confirm the nursery maintenance schedule."})
    q, c = d["questions"][0], d["columns"][0]
    client, base = qa
    path = f"{base}/lists/{d['id']}/questions/{q['id']}/drafts/{c['id']}"
    payload = {
        "version_id": d["version"]["id"],
        "body": "Draft maintenance notes.",
        "draft_revision": 0,
        "base_cell_revision_id": q["replies"][0]["text_revision_id"],
    }
    r = client.put(path, json=payload)
    assert r.status_code == 200, r.text
    assert (
        client.get(path, params={"version_id": d["version"]["id"]}).json()["body"]
        == payload["body"]
    )
    assert client.get(f"{base}/lists/{d['id']}").json()["questions"][0]["replies"][0]["body"] == ""
    assert client.put(path, json=payload).status_code == 409
    from src.identity import Actor, reset_actor, set_actor

    token = set_actor(Actor(id="another-user", kind="local", role_id="owner"))
    try:
        assert client.get(path, params={"version_id": d["version"]["id"]}).json() is None
    finally:
        reset_actor(token)


def test_stale_draft_cannot_replace_a_newer_saved_reply(qa):
    d = create(qa)
    d = mutate(qa, d, "/questions", {"text": "Provide the orchard map."})
    q, c = d["questions"][0], d["columns"][0]
    old = q["replies"][0]["text_revision_id"]
    d = mutate(
        qa, d, f"/questions/{q['id']}/replies/{c['id']}", {"body": "Current map notes."}, "put"
    )
    client, base = qa
    r = client.put(
        f"{base}/lists/{d['id']}/questions/{q['id']}/replies/{c['id']}",
        json={
            "expected_revision": d["revision"],
            "body": "Stale draft",
            "base_cell_revision_id": old,
        },
    )
    assert r.status_code == 409


@pytest.mark.parametrize(
    "instructions", ["", "Expand these museum visiting notes. " * 180], ids=["empty", "full-reply"]
)
def test_ai_reply_is_a_proposal_and_source_ids_cannot_be_invented(qa, monkeypatch, instructions):
    from src.rag.retriever import RetrievedChunk
    from src.services import services

    d = create(qa)
    d = mutate(qa, d, "/questions", {"text": "Confirm the arboretum visiting schedule."})
    q, c = d["questions"][0], d["columns"][0]

    class Retrieval:
        def retrieve(self, *args, **kwargs):
            return [
                RetrievedChunk(
                    text="The fictional arboretum accepts visits on weekends.",
                    score=0.8,
                    metadata={
                        "source": "Visitor guide",
                        "file_id": "example_file",
                        "version_id": "example_version",
                        "id": "example_chunk",
                    },
                )
            ]

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = json.loads(prompt.split("\n", 1)[1])
            if packet.get("stage") == "reply_review":
                return json.dumps({"issues": []})
            assert instructions in prompt
            if packet.get("stage") == "reply_intent":
                return json.dumps({"kind": "guidance"})
            return json.dumps(
                {
                    "body": "Visits are available on weekends. [1]",
                    "sources": [1, 999],
                    "insufficient_evidence": False,
                }
            )

    monkeypatch.setattr(services, "llm", LLM())
    install_agentic(monkeypatch, services, Retrieval())
    monkeypatch.setattr(services, "reranker", None)
    monkeypatch.setattr(services, "db", None)
    client, base = qa
    path = f"{base}/lists/{d['id']}/questions/{q['id']}/drafts/{c['id']}/generate"
    r = client.post(
        path,
        json={
            "expected_revision": d["revision"],
            "version_id": d["version"]["id"],
            "instructions": instructions,
        },
    )
    assert r.status_code == 200, r.text
    proposal = r.json()
    assert len(proposal["sources"]) == 1
    assert proposal["sources"][0]["version_id"] == "example_version"
    detail = client.get(f"{base}/lists/{d['id']}").json()
    assert detail["questions"][0]["replies"][0]["body"] == ""
    assert detail["revision"] == d["revision"]
    target = f"{base}/lists/{d['id']}/questions/{q['id']}/drafts/{c['id']}"
    r = client.put(
        target,
        json={
            "version_id": d["version"]["id"],
            "body": proposal["body"],
            "draft_revision": 0,
            "base_cell_revision_id": q["replies"][0]["text_revision_id"],
            "proposal_id": proposal["id"],
        },
    )
    assert r.status_code == 200, r.text
    d = mutate(
        qa,
        d,
        f"/questions/{q['id']}/replies/{c['id']}",
        {
            "body": proposal["body"],
            "base_cell_revision_id": q["replies"][0]["text_revision_id"],
            "draft_revision": r.json()["draft_revision"],
        },
        "put",
    )
    assert d["questions"][0]["replies"][0]["sources"][0]["version_id"] == "example_version"


def test_question_annotation_refreshes_the_summary(qa, monkeypatch):
    from src.services import services

    d = create(qa)
    d = mutate(
        qa, d, "/questions", {"text": "Please explain the fictional orchard inspection procedure."}
    )
    qid = d["questions"][0]["id"]

    class LLM:
        def generate(self, *args, **kwargs):
            return json.dumps(
                {
                    "rows": [
                        {
                            "key": qid,
                            "target": "new",
                            "certainty": "high",
                            "summary": "Orchard inspection procedure",
                            "reply_summary": "",
                            "type": "Clarify",
                        }
                    ],
                    "columns": [],
                }
            )

    monkeypatch.setattr(services, "llm", LLM())
    client, base = qa
    r = client.post(
        f"{base}/lists/{d['id']}/questions/{qid}/annotate",
        json={"expected_revision": d["revision"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["questions"][0]["summary"] == "Orchard inspection procedure"
    assert "type" not in r.json()["questions"][0]
