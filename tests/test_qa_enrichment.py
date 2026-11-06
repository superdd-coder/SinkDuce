"""Derived labels survive normal edits and never replace explicit types."""

import json

from tests.test_qa import create, mutate, qa_fixture  # noqa: F401


def test_compact_classification_response_updates_types_and_truncates_summary(qa, monkeypatch):
    from src.qa.pipeline import summarize
    from src.services import services

    d = mutate(
        qa, create(qa), "/questions", {"text": "Explain the fictional garden irrigation cycle."}
    )
    qid = d["questions"][0]["id"]

    class LLM:
        def generate(self, *args, **kwargs):
            return json.dumps(
                {
                    "rows": [
                        {
                            "key": qid,
                            "summary": "Garden " * 50,
                            "reply_summary": "",
                            "type": "Clarify",
                        }
                    ]
                }
            )

    monkeypatch.setattr(services, "llm", LLM())
    summarize("col_qa", d["id"], d["revision"])
    result = qa[0].get(f"{qa[1]}/lists/{d['id']}").json()
    assert result["questions"][0]["type"] == "Clarify"
    assert len(result["questions"][0]["summary"]) <= 240
    assert result["annotation_pending"] == 0


def test_rename_during_classification_does_not_discard_results(qa, monkeypatch):
    from src.qa.pipeline import summarize
    from src.services import services

    d = mutate(qa, create(qa), "/questions", {"text": "Provide an invented seed catalogue."})

    class LLM:
        def generate(self, *args, **kwargs):
            mutate(qa, d, "", {"title": "Updated garden checklist"}, "patch")
            return json.dumps(
                {
                    "rows": [
                        {
                            "key": d["questions"][0]["id"],
                            "summary": "Seed catalogue",
                            "reply_summary": "",
                            "type": "Provide",
                        }
                    ]
                }
            )

    monkeypatch.setattr(services, "llm", LLM())
    summarize("col_qa", d["id"], d["revision"])
    result = qa[0].get(f"{qa[1]}/lists/{d['id']}").json()
    assert result["title"] == "Updated garden checklist"
    assert result["questions"][0]["type"] == "Provide"


def test_unavailable_classification_is_retryable_without_overwriting_manual_type(qa, monkeypatch):
    from src.services import services

    d = mutate(
        qa, create(qa), "/questions", {"text": "Confirm the invented greenhouse opening hours."}
    )
    qid = d["questions"][0]["id"]
    d = mutate(qa, d, f"/questions/{qid}", {"type": "Confirm"}, "patch")
    path = f"{qa[1]}/lists/{d['id']}/annotations"
    monkeypatch.setattr(services, "llm", None)
    failed = qa[0].post(
        path, json={"expected_revision": d["revision"], "version_id": d["version"]["id"]}
    )
    assert failed.status_code == 200
    assert failed.json()["annotation_state"] == "partial"

    class LLM:
        def generate(self, *args, **kwargs):
            return json.dumps(
                {
                    "rows": [
                        {
                            "key": qid,
                            "summary": "Opening hours",
                            "reply_summary": "",
                            "type": "Clarify",
                        }
                    ]
                }
            )

    monkeypatch.setattr(services, "llm", LLM())
    ready = (
        qa[0]
        .post(path, json={"expected_revision": d["revision"], "version_id": d["version"]["id"]})
        .json()
    )
    assert ready["annotation_state"] == "ready"
    assert ready["questions"][0]["type"] == "Confirm"
    assert ready["revision"] == d["revision"]


def test_unknown_type_does_not_become_an_ai_confirmed_other(qa, monkeypatch):
    from src.qa.pipeline import summarize
    from src.services import services

    d = mutate(qa, create(qa), "/questions", {"text": "Explain the fictional storage process."})

    class LLM:
        def generate(self, *args, **kwargs):
            return json.dumps(
                {
                    "rows": [
                        {
                            "key": d["questions"][0]["id"],
                            "summary": "Storage process",
                            "reply_summary": "",
                            "type": "Unknown option",
                        }
                    ]
                }
            )

    monkeypatch.setattr(services, "llm", LLM())
    summarize("col_qa", d["id"], d["revision"])
    result = qa[0].get(f"{qa[1]}/lists/{d['id']}").json()
    assert result["questions"][0]["type_origin"] == "default"
    assert result["annotation_state"] == "partial"


def test_editing_original_text_during_classification_discards_outdated_annotation(qa, monkeypatch):
    from src.qa.pipeline import summarize
    from src.services import services

    d = mutate(qa, create(qa), "/questions", {"text": "Explain the invented nursery schedule."})
    qid = d["questions"][0]["id"]

    class LLM:
        def generate(self, *args, **kwargs):
            mutate(
                qa, d, f"/questions/{qid}", {"text": "Provide a fictional tool inventory."}, "patch"
            )
            return json.dumps(
                {"rows": [{"key": qid, "summary": "Nursery schedule", "type": "Clarify"}]}
            )

    monkeypatch.setattr(services, "llm", LLM())
    summarize("col_qa", d["id"], d["revision"])
    result = qa[0].get(f"{qa[1]}/lists/{d['id']}").json()
    assert result["questions"][0]["summary"] == ""
    assert result["questions"][0]["type_origin"] == "default"
    assert result["annotation_pending"] == 1


def test_classification_never_writes_to_a_historical_version(qa, monkeypatch):
    from src.services import services

    d = mutate(qa, create(qa), "/questions", {"text": "Confirm the imaginary seed count."})
    mutate(qa, d, "/versions", {})

    class LLM:
        def generate(self, *args, **kwargs):
            raise AssertionError("Historical requests must be rejected before generation")

    monkeypatch.setattr(services, "llm", LLM())
    response = qa[0].post(
        f"{qa[1]}/lists/{d['id']}/annotations",
        json={
            "expected_revision": d["revision"],
            "version_id": d["version"]["id"],
        },
    )
    assert response.status_code == 403
