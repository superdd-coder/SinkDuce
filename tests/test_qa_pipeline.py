"""Provider boundaries and resumable analysis with synthetic correspondence."""

import json

from tests.test_qa import create, mutate, qa_fixture  # noqa: F401
from tests.test_qa_excel import upload


def payload(prompt):
    return json.loads(prompt.split("\n", 1)[1])


def test_group_heading_is_not_a_question_parent_and_does_not_block_import(qa, monkeypatch):
    from src.services import services

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = payload(prompt)
            if packet.get("stage") == "structure":
                kinds = {"2": "section", "3": "question", "4": "child"}
                parents = {"3": "2", "4": "3"}
                return json.dumps(
                    {
                        "rows": [
                            {
                                "key": r["key"],
                                "kind": kinds[r["key"]],
                                "parent_key": parents.get(r["key"], ""),
                            }
                            for r in packet["rows"]
                        ]
                    }
                )
            return json.dumps({"header_row": 1, "question": 0, "replies": [1]})

    monkeypatch.setattr(services, "llm", LLM())
    p = upload(
        qa,
        create(qa),
        [
            ["Question", "Reply"],
            ["Nursery storage", ""],
            ["Describe the seed cabinet.", "Three shelves."],
            ["Confirm its lock.", "Key held at reception."],
        ],
    )
    assert p["mapping_valid"] is True
    assert len(p["rows"]) == 2
    assert p["rows"][0]["group_key"] == "2"
    assert p["rows"][0]["parent_key"] == ""
    assert p["rows"][1]["parent_key"] == "3"


def test_structure_receives_nonempty_reply_flags_and_independent_parent_rows(qa, monkeypatch):
    from src.services import services

    seen = []

    class LLM:
        def generate(self, prompt, **kwargs):
            p = payload(prompt)
            seen.append(p)
            if p.get("stage") == "structure":
                assert "Staff-only reply evidence." not in prompt
                return json.dumps(
                    {
                        "rows": [
                            {"key": "2", "kind": "question"},
                            {"key": "3", "kind": "child", "parent_key": "2"},
                        ]
                    }
                )
            return json.dumps({"header_row": 1, "question": 0, "replies": [1]})

    monkeypatch.setattr(services, "llm", LLM())
    p = upload(
        qa,
        create(qa),
        [
            ["Question", "Reply"],
            ["Describe the specimen cabinet.", "Staff-only reply evidence."],
            ["Confirm its locking mechanism.", "Spare keys at reception."],
        ],
    )
    assert any(s.get("stage") == "structure" for s in seen)
    assert p["rows"][1]["parent_key"] == "2"


def test_history_identity_packets_exclude_replies_and_columns(qa, monkeypatch):
    from src.services import services

    monkeypatch.setattr(services, "llm", None)
    d = mutate(qa, create(qa), "/questions", {"text": "Describe the specimen cabinet."})
    qid = d["questions"][0]["id"]
    p = upload(
        qa,
        d,
        [
            ["Question", "Reply"],
            ["Explain the specimen storage cabinet.", "Staff-only reply evidence."],
        ],
    )
    seen = []

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = payload(prompt)
            seen.append(packet)
            if packet.get("stage") == "identity":
                assert "Staff-only reply evidence." not in prompt
                assert "historical_columns" not in packet
                return json.dumps(
                    {
                        "rows": [
                            {
                                "key": "2",
                                "target": qid,
                                "certainty": "review",
                                "reason": "Wording needs confirmation.",
                            }
                        ]
                    }
                )
            if packet.get("stage") == "structure":
                return json.dumps({"rows": [{"key": "2", "kind": "question"}]})
            return json.dumps({"columns": []})

    monkeypatch.setattr(services, "llm", LLM())
    client, base = qa
    url = f"{base}/lists/{d['id']}/imports/{p['id']}/analyze"
    r = client.post(url, json={"plan_revision": p["plan_revision"]})
    assert r.status_code == 200, r.text
    assert any(s.get("stage") == "identity" for s in seen)
    for packet in seen:
        if packet.get("stage") == "identity":
            assert all(
                "number" not in row and "external_number" not in row for row in packet["rows"]
            )
    result = r.json()
    assert result["matches"]["2"] is None
    assert result["suggestions"]["2"]["target"] == qid
    before = len(seen)
    r = client.post(url, json={"plan_revision": result["plan_revision"]})
    assert r.status_code == 200
    assert len(seen) == before, "Successful unchanged analyses should be reused"


def test_first_import_skips_history_matching(qa, monkeypatch):
    from src.services import services

    monkeypatch.setattr(services, "llm", None)
    d = create(qa)
    p = upload(qa, d, [["Question"], ["Describe the greenhouse entry route."]])
    stages = []

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = payload(prompt)
            stages.append(packet.get("stage"))
            return json.dumps({"rows": [{"key": "2", "kind": "question"}]})

    monkeypatch.setattr(services, "llm", LLM())
    client, base = qa
    r = client.post(
        f"{base}/lists/{d['id']}/imports/{p['id']}/analyze",
        json={"plan_revision": p["plan_revision"]},
    )
    assert r.status_code == 200
    assert "identity" not in stages and "columns" not in stages
    assert None not in stages


def test_reply_bearing_question_cannot_silently_become_a_group(qa, monkeypatch):
    from src.services import services

    class LLM:
        def generate(self, prompt, **kwargs):
            if payload(prompt).get("stage") == "structure":
                return json.dumps({"rows": [{"key": "2", "kind": "section"}]})
            return json.dumps({"header_row": 1, "question": 0, "replies": [1]})

    monkeypatch.setattr(services, "llm", LLM())
    p = upload(
        qa, create(qa), [["Question", "Reply"], ["Describe the seed rack.", "It is made of cedar."]]
    )
    assert p["table_rows"][0]["kind"] == "review"
    assert not p["mapping_valid"]


def test_additional_field_rename_is_aligned_separately_from_question_matching(qa, monkeypatch):
    from src.services import services
    from tests.test_qa_excel import commit

    monkeypatch.setattr(services, "llm", None)
    d = create(qa)
    d = commit(
        qa, d, upload(qa, d, [["Question", "Owner"], ["Describe the seed cabinet.", "Garden team"]])
    )
    field = d["questions"][0]["fields"][0]["id"]
    p = upload(
        qa, d, [["Question", "Coordinator"], ["Describe the seed cabinet.", "Garden team, revised"]]
    )
    seen = []

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = payload(prompt)
            seen.append(packet.get("stage"))
            if packet.get("stage") == "structure":
                return json.dumps({"rows": [{"key": "2", "kind": "question"}]})
            if packet.get("stage") == "columns":
                return json.dumps(
                    {
                        "columns": [
                            {
                                "key": "extra:1",
                                "target": field,
                                "certainty": "high",
                                "reason": "Same responsible team field.",
                            }
                        ]
                    }
                )
            return json.dumps({"rows": []})

    monkeypatch.setattr(services, "llm", LLM())
    client, base = qa
    r = client.post(
        f"{base}/lists/{d['id']}/imports/{p['id']}/analyze",
        json={"plan_revision": p["plan_revision"]},
    )
    assert r.status_code == 200
    assert "identity" not in seen and "columns" in seen
    d = commit(qa, d, r.json())
    assert d["questions"][0]["fields"][0]["id"] == field
    assert d["questions"][0]["fields"][0]["label"] == "Coordinator"


def test_manual_row_and_parent_changes_survive_later_edits_and_analysis(qa, monkeypatch):
    from src.services import services

    monkeypatch.setattr(services, "llm", None)
    d = create(qa)
    p = upload(
        qa,
        d,
        [
            ["Question"],
            ["Describe the seed cabinet."],
            ["Confirm its lock."],
            ["Read the room guide."],
        ],
    )
    client, base = qa
    url = f"{base}/lists/{d['id']}/imports/{p['id']}"
    for change in (
        {"row_actions": {"3": "child"}, "parents": {"3": "2"}},
        {"row_actions": {"4": "note"}},
    ):
        r = client.patch(url, json={"plan_revision": p["plan_revision"], **change})
        assert r.status_code == 200, r.text
        p = r.json()
    assert p["row_actions"]["3"] == "child"
    assert p["parents"]["3"] == "2"

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = payload(prompt)
            return json.dumps(
                {"rows": [{"key": r["key"], "kind": "question"} for r in packet["rows"]]}
            )

    monkeypatch.setattr(services, "llm", LLM())
    r = client.post(url + "/analyze", json={"plan_revision": p["plan_revision"]})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["table_rows"][1]["kind"] == "child" and p["table_rows"][1]["parent_key"] == "2"
    assert p["table_rows"][2]["kind"] == "note"


def test_evidence_review_cannot_silently_discard_a_reply_bearing_question(qa, monkeypatch):
    from src.services import services

    class LLM:
        def generate(self, prompt, **kwargs):
            packet = payload(prompt)
            if packet.get("stage", "").startswith("structure"):
                return json.dumps(
                    {
                        "rows": [
                            {
                                "key": "2",
                                "kind": "note",
                                "needs_evidence": packet["stage"] == "structure",
                            }
                        ]
                    }
                )
            return json.dumps({"header_row": 1, "question": 0, "replies": [1]})

    monkeypatch.setattr(services, "llm", LLM())
    p = upload(
        qa, create(qa), [["Question", "Reply"], ["Describe the seed rack.", "It is made of cedar."]]
    )
    assert p["table_rows"][0]["kind"] == "review" and not p["mapping_valid"]
