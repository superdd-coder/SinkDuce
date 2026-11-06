"""Q&A uses Chat's retrieval and never restores evidence discarded by grading."""

import json
from types import SimpleNamespace

import pytest

from src.rag.agentic_query import AgenticQueryResult, AgenticQueryService
from src.rag.aggregator import Aggregator
from src.rag.decomposer import AtomicQuery
from src.rag.retriever import RetrievedChunk
from src.rag.variant_fetcher import VariantFetcher
from tests.test_qa import qa_fixture  # noqa: F401
from tests.test_qa_reply_direction import exchange, generate


def passage(key, text):
    return RetrievedChunk(
        text,
        0.9,
        {
            "id": key,
            "file_id": key + "_file",
            "version_id": key + "_version",
            "collection": "col_qa",
            "source": "__file__:" + key + "_file",
            "source_label": "Gallery reference.xlsx",
            "is_current": True,
        },
    )


@pytest.fixture
def profile(monkeypatch):
    from src.config import AppConfig
    from src.services import services

    config = AppConfig()
    config.rag.top_k = 19
    config.rag.rerank_top_k = 6
    config.rag.default_search_mode = "dense"
    config.rag.min_score = 0.13
    monkeypatch.setattr("src.config.get_config", lambda: config)
    monkeypatch.setattr(services, "config", config)
    return config


class DraftModel:
    def __init__(self, kind="guidance"):
        self.packets = []
        self.kind = kind

    def generate(self, prompt, **kwargs):
        packet = json.loads(prompt.split("\n", 1)[1])
        self.packets.append(packet)
        if packet.get("stage") == "reply_intent":
            return json.dumps({"kind": self.kind, "needs_retrieval": True})
        return json.dumps(
            {
                "body": "Could you please clarify the applicable capacity assumption?",
                "sources": [1],
                "insufficient_evidence": False,
            }
        )


def test_qa_uses_the_same_agentic_service_and_settings_without_file_preselection(
    qa, monkeypatch, profile
):
    from src.file_mgmt import service as files
    from src.rag.search_settings import search_settings
    from src.rag.source_scope import source_filter
    from src.services import services

    d, qid, cid = exchange(qa)
    draft = DraftModel()
    kept = passage("kept", "The calculation uses a provisional gallery capacity allowance.")
    dropped = passage("dropped", "The exhibition installation takes seven weeks.")
    calls, directories = [], []

    def directory(*args, **kwargs):
        directories.append(True)
        return {"files": []}

    def forbidden(*args, **kwargs):
        raise AssertionError("Q&A must use the shared AgenticRAG entry point")

    class Agentic:
        def run(self, query, **kwargs):
            calls.append((query, kwargs, source_filter().model_dump()))
            kwargs["on_step"]({"step": "grading", "content": "private provider details"})
            return AgenticQueryResult(
                answer=None, all_chunks=[dropped, kept], retained_chunks=[kept]
            )

    monkeypatch.setattr(files, "list_files_lean", directory)
    monkeypatch.setattr(services, "llm", draft)
    monkeypatch.setattr(services, "retriever", SimpleNamespace(retrieve=forbidden))
    monkeypatch.setattr(services, "agentic_query", Agentic())
    response = generate(qa, d, qid, cid, "Check the underlying capacity calculation.")
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    query, kwargs, scope = calls[0]
    assert "Check the underlying capacity calculation." in query
    assert kwargs["original_user_question"] == query
    assert kwargs["collections"] == ["col_qa"]
    assert kwargs["generate_answer"] is False and kwargs["include_images"] is False
    assert kwargs["decompose"] is True and kwargs["preliminary_retrieval"] is True
    assert kwargs["include_archived"] is False
    assert all(kwargs[key] == value for key, value in search_settings("agentic").items())
    assert '"any": []' in json.dumps(scope)
    assert directories == []
    assert "available_sources" not in draft.packets[0]
    assert "requested_sources" not in draft.packets[-1]
    assert [e["chunk_id"] for e in draft.packets[-1]["evidence"]] == ["kept"]
    assert dropped.text not in json.dumps(draft.packets)
    assert response.json()["sources"][0]["chunk_id"] == "kept"


@pytest.mark.parametrize("decision", ["drop_all", "keep_one", "keep_with_gap", "grade_failure"])
def test_real_shared_grading_controls_qa_evidence_and_missing_facts(
    qa, monkeypatch, profile, decision
):
    from src.rag.agent_prompts import SCOPED_GRADE_SYSTEM, VARIANT_GENERATION_SYSTEM
    from src.services import services

    d, qid, cid = exchange(qa)
    batch = [
        passage("timeline", "Installation takes seven weeks and a reception follows."),
        passage("calculation", "Capacity is based on the usable exhibition floor area."),
    ]
    draft = DraftModel()
    grade_queries, outputs = [], []

    class Grader:
        def generate(self, prompt, **kwargs):
            if kwargs["system"] == VARIANT_GENERATION_SYSTEM:
                return "[]"
            assert kwargs["system"] == SCOPED_GRADE_SYSTEM
            grade_queries.append(prompt)
            if decision == "grade_failure":
                raise RuntimeError("Synthetic provider failure")
            return json.dumps(
                {
                    "relevant_indices": [] if decision == "drop_all" else [1],
                    "retained_info": "",
                    "gap_analysis": "The underlying allowance remains unverified."
                    if decision in {"drop_all", "keep_with_gap"}
                    else "",
                }
            )

    class Direct:
        def retrieve(self, *args, **kwargs):
            return SimpleNamespace(chunks=list(batch), trace=[], errors=[])

    model, direct = Grader(), Direct()
    router = SimpleNamespace(
        decompose=lambda *args, **kwargs: [
            AtomicQuery(
                query="Verify the capacity calculation assumption", target_collections=["col_qa"]
            ),
        ]
    )
    catalog = SimpleNamespace(get_catalog=lambda *args, **kwargs: [SimpleNamespace(id="col_qa")])
    real_agent = AgenticQueryService(
        direct, VariantFetcher(direct, model), catalog, router, Aggregator(model), model
    )

    class CaptureAgent:
        def run(self, *args, **kwargs):
            result = real_agent.run(*args, **kwargs)
            outputs.append(result)
            return result

    monkeypatch.setattr(services, "llm", draft)
    monkeypatch.setattr(services, "agentic_query", CaptureAgent())
    response = generate(
        qa, d, qid, cid, "Verify the capacity calculation before drafting a follow-up."
    )
    assert response.status_code == 200, response.text
    assert len(grade_queries) == 1
    assert outputs[0].all_chunks  # Search/Recall may still display rejected hits.
    packet = draft.packets[-1]
    if decision in {"drop_all", "grade_failure"}:
        assert packet["evidence"] == []
        assert batch[0].text not in json.dumps(packet)
        assert batch[1].text not in json.dumps(packet)
        assert response.json()["sources"] == []
        assert response.json()["insufficient_evidence"] is True
        assert packet["retrieval_status"] == (
            "unavailable" if decision == "grade_failure" else "empty"
        )
    else:
        assert [e["chunk_id"] for e in packet["evidence"]] == ["calculation"]
        assert batch[0].text not in json.dumps(packet)
        assert response.json()["sources"][0]["chunk_id"] == "calculation"
        assert response.json()["insufficient_evidence"] is (decision == "keep_with_gap")
    if decision in {"drop_all", "keep_with_gap"}:
        assert packet["retrieval_gaps"]


def test_polishing_does_not_start_agentic_retrieval_or_a_directory_lookup(qa, monkeypatch, profile):
    from src.file_mgmt import service as files
    from src.services import services

    d, qid, cid = exchange(qa)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Polishing uses the user's existing prose only")

    monkeypatch.setattr(files, "list_files_lean", forbidden)
    monkeypatch.setattr(services, "llm", DraftModel("message"))
    monkeypatch.setattr(services, "agentic_query", SimpleNamespace(run=forbidden))
    response = generate(
        qa, d, qid, cid, "Could you please clarify the applicable capacity assumption?"
    )
    assert response.status_code == 200, response.text
    assert response.json()["operation"] == "polish"
    assert calls == []
