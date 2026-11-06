"""Conversation attribution and retrieval decisions use invented museum correspondence."""

import json

import pytest

from tests.qa_agentic_helper import install_agentic
from tests.test_qa import create, mutate, qa_fixture  # noqa: F401


@pytest.mark.parametrize("text", ["", "Ask for clarification of the booking deadline."])
def test_draft_receives_explicit_turn_attribution_even_when_input_is_empty(qa, monkeypatch, text):
    from src.services import services

    d = mutate(qa, create(qa), "/questions", {"text": "Confirm the museum booking deadline."})
    qid = d["questions"][0]["id"]
    first, second = [c["id"] for c in d["columns"]]
    d = mutate(qa, d, f"/columns/{first}", {"label": "Venue response"}, "patch")
    d = mutate(qa, d, f"/columns/{second}", {"label": "Coordinator clarification"}, "patch")
    d = mutate(
        qa,
        d,
        f"/questions/{qid}/replies/{first}",
        {"body": "We are confirming the deadline."},
        "put",
    )
    d = mutate(
        qa,
        d,
        f"/questions/{qid}/replies/{second}",
        {"body": "We need the deadline for planning."},
        "put",
    )
    d = mutate(qa, d, "/columns", {"labels": ["Coordinator follow-up"]})
    current = d["columns"][-1]["id"]
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
                        "question_author_relation": "same_party",
                        "turn_relations": [
                            {"position": 1, "author_relation": "counterparty"},
                            {"position": 2, "author_relation": "same_party"},
                        ],
                    }
                )
            return json.dumps({"body": "Could you now confirm the booking deadline?"})

    class Retriever:
        def retrieve(self, *args, **kwargs):
            raise AssertionError("This clarification uses the existing exchange alone")

    monkeypatch.setattr(services, "llm", LLM())
    agentic = install_agentic(monkeypatch, services, Retriever())
    client, base = qa
    response = client.post(
        f"{base}/lists/{d['id']}/questions/{qid}/drafts/{current}/generate",
        json={"expected_revision": d["revision"], "instructions": text},
    )
    assert response.status_code == 200, response.text
    assert packets[0].get("stage") == "reply_intent"
    packet = packets[-1]
    assert packet["reply_column"] == "Coordinator follow-up"
    assert packet["question_author_relation"] == "same_party"
    assert [r["author_relation"] for r in packet["previous_replies"]] == [
        "counterparty",
        "counterparty",
    ]
    assert packet["retrieval_status"] == "skipped"
    assert agentic.calls == []


def test_older_conflicting_turn_authorship_stays_unknown_and_latest_is_counterparty(qa, monkeypatch):
    from src.services import services

    d = mutate(qa, create(qa), "/questions", {"text": "Describe the sculpture loan schedule."})
    qid = d["questions"][0]["id"]
    first, second = [c["id"] for c in d["columns"]]
    d = mutate(
        qa, d, f"/questions/{qid}/replies/{first}", {"body": "The schedule is provisional."}, "put"
    )
    d = mutate(
        qa,
        d,
        f"/questions/{qid}/replies/{second}",
        {"body": "We will confirm the loan dates next week."},
        "put",
    )
    d = mutate(qa, d, "/columns", {"labels": ["Curator follow-up"]})
    current = d["columns"][-1]["id"]
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
                        "turn_relations": [
                            {"position": 1, "author_relation": "counterparty"},
                            {"position": 1, "author_relation": "same_party"},
                            {"position": 50, "author_relation": "same_party"},
                        ],
                    }
                )
            return json.dumps({"body": "Please confirm when the schedule will be finalized."})

    monkeypatch.setattr(services, "llm", LLM())
    monkeypatch.setattr(services, "agentic_query", None)
    client, base = qa
    response = client.post(
        f"{base}/lists/{d['id']}/questions/{qid}/drafts/{current}/generate",
        json={"expected_revision": d["revision"]},
    )
    assert response.status_code == 200, response.text
    assert packets[-1]["question_author_relation"] == "unknown"
    assert [r["author_relation"] for r in packets[-1]["previous_replies"]] == [
        "unknown",
        "counterparty",
    ]


def test_empty_input_analysis_failure_keeps_drafting_with_unknown_authors(qa, monkeypatch):
    from src.services import services

    d = mutate(qa, create(qa), "/questions", {"text": "Describe the exhibition access rules."})
    qid, cid = d["questions"][0]["id"], d["columns"][0]["id"]
    packets = []

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = json.loads(prompt.split("\n", 1)[1])
            if packet.get("stage") == "reply_review":
                return json.dumps({"issues": []})
            if packet.get("stage") == "reply_intent":
                raise ValueError("Intent unavailable")
            packets.append(packet)
            return json.dumps({"body": "Please provide the current access rules."})

    monkeypatch.setattr(services, "llm", LLM())
    monkeypatch.setattr(services, "agentic_query", None)
    client, base = qa
    response = client.post(
        f"{base}/lists/{d['id']}/questions/{qid}/drafts/{cid}/generate",
        json={"expected_revision": d["revision"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["operation"] == "draft"
    assert packets[-1]["question_author_relation"] == "unknown"
    assert packets[-1]["retrieval_status"] == "unavailable"
    assert packets[-1]["evidence"] == []
    assert response.json()["insufficient_evidence"] is True


@pytest.mark.parametrize("when", ["during_generation", "before_adoption"])
@pytest.mark.parametrize("renamed_column", [0, 1])
def test_column_rename_invalidates_ai_party_context(qa, monkeypatch, when, renamed_column):
    from src.qa import store
    from src.services import services

    d = mutate(qa, create(qa), "/questions", {"text": "Confirm the gallery reservation cutoff."})
    question = d["questions"][0]
    qid, cid = question["id"], d["columns"][1]["id"]

    def rename():
        mutate(
            qa,
            d,
            f"/columns/{d['columns'][renamed_column]['id']}",
            {"label": "A different author"},
            "patch",
        )

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = json.loads(prompt.split("\n", 1)[1])
            if packet.get("stage") == "reply_review":
                return json.dumps({"issues": []})
            if packet.get("stage") == "reply_intent":
                return json.dumps({"kind": "guidance", "needs_retrieval": False})
            if when == "during_generation":
                rename()
            return json.dumps({"body": "Please confirm the reservation cutoff."})

    monkeypatch.setattr(services, "llm", LLM())
    monkeypatch.setattr(services, "agentic_query", None)
    client, base = qa
    path = f"{base}/lists/{d['id']}/questions/{qid}/drafts/{cid}"
    response = client.post(path + "/generate", json={"expected_revision": d["revision"]})
    if when == "during_generation":
        assert response.status_code == 409, response.text
        assert "stale_draft" in response.text
        with store.connection("col_qa") as conn:
            assert conn.execute("SELECT count(*) FROM qa_draft_proposals").fetchone()[0] == 0
    else:
        assert response.status_code == 200, response.text
        proposal = response.json()
        rename()
        response = client.put(
            path,
            json={
                "version_id": d["version"]["id"],
                "body": proposal["body"],
                "draft_revision": 0,
                "base_cell_revision_id": question["replies"][1]["text_revision_id"],
                "proposal_id": proposal["id"],
            },
        )
        assert response.status_code == 409, response.text
        assert "stale_draft" in response.text
