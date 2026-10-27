"""Exercise preliminary retrieval, quoted-source reuse and gap search end to end."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.rag.agentic_query import AgenticQueryService
from src.rag.aggregator import Aggregator
from src.rag.decomposer import Decomposer
from src.rag.direct_query import DirectQueryResult
from src.rag.retriever import RetrievedChunk
from src.rag.variant_fetcher import VariantFetcherResult


BODY = "Entity A annual output was 120 units in 2025. This value refers to the full year."


def passage(body=BODY, chunk_id="chunk-a", collection="col-a", **metadata):
    return RetrievedChunk(body, 0.9, {
        "id": chunk_id, "collection": collection, "file_id": "file-a",
        "version_id": "version-a", "is_current": True, "source": "__file__:file-a",
        **metadata,
    })


def covered(quote=BODY, chunk_id="chunk-a", collection="col-a", need="annual output"):
    return {"need": need, "evidence": [{"collection": collection, "id": chunk_id, "quote": quote}]}


def plan(covered_needs=None, queries=None, **fields):
    return [{"task": "entity A", "task_query": "Entity A facts",
             "covered": covered_needs or [], "queries": queries or [], **fields}]


class PlanningLLM:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def generate(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return json.dumps(self.value)


class PreliminaryDirect:
    def __init__(self, chunks, errors=None):
        self.chunks = chunks
        self.errors = errors or []
        self.calls = []

    def retrieve(self, query, collections, **kwargs):
        self.calls.append((query, collections, kwargs))
        return DirectQueryResult(chunks=deepcopy(self.chunks), errors=self.errors)


class GapFetcher:
    def __init__(self):
        self.calls = []

    def run(self, query, collections, **kwargs):
        self.calls.append((query, collections, kwargs))
        chunk = passage("The operating permit is approved.", "chunk-permit", "col-b")
        return VariantFetcherResult(chunks=[chunk], retrieved_chunks=[chunk], retained_info=chunk.text)


def service(value, rows=None, errors=None):
    llm = PlanningLLM(value)
    direct = PreliminaryDirect([passage()] if rows is None else rows, errors)
    fetcher = GapFetcher()
    catalog = SimpleNamespace(get_catalog=lambda *args, **kwargs: [
        SimpleNamespace(id=cid, name=cid, definition="", coverage="", tags=[])
        for cid in ("col-a", "col-b")
    ])
    result = AgenticQueryService(direct, fetcher, catalog, Decomposer(llm), Aggregator(llm), llm)
    return result, llm, direct, fetcher


def test_preliminary_retrieval_precedes_single_planning_call_and_preserves_settings():
    svc, llm, direct, fetcher = service(plan([covered()]))
    result = svc.run("annual output", collections=["col-a"], top_k=17,
                     rerank_top_k=4, rerank_enabled=True, search_mode="hybrid", min_score=0.2)
    assert len(direct.calls) == 1
    query, collections, settings = direct.calls[0]
    assert query == "annual output"
    assert collections == ["col-a"]
    assert settings["top_k"] == 17 and settings["rerank_top_k"] == 4
    assert settings["search_mode"] == "hybrid" and settings["min_score"] == 0.2
    assert settings["generate_answer"] is False and settings["sparse_llm_tokenize"] is False
    assert len(llm.calls) == 1
    assert BODY in llm.calls[0][0]
    assert "annual output" in llm.calls[0][0]
    assert fetcher.calls == []
    assert BODY in result.context


def test_planning_progress_is_emitted_before_waiting_for_planning_model():
    svc, llm, direct, fetcher = service(plan([covered()]))
    events = []
    generate = llm.generate

    def check_progress(prompt, **kwargs):
        assert events[-1]["step"] == "decompose"
        assert "planning" in events[-1]["content"].lower()
        return generate(prompt, **kwargs)

    llm.generate = check_progress
    result = svc.run("annual output", collections=["col-a"], on_step=events.append)
    assert not result.errors
    assert fetcher.calls == []


def test_generated_image_description_does_not_certify_a_need_as_already_covered():
    invented = "An unnamed investor owns 4,350,000 shares."
    body = ("Register attached.\n:::image\nimage_id: " + "a" * 32 +
            "\nfile_id: file-a\nocr_text: 4,300,000\ndescription: " + invented + "\n:::\n")
    svc, llm, direct, fetcher = service(plan([covered(quote=invented)]), [passage(body)])
    svc.run("annual output", collections=["col-a"])
    assert len(fetcher.calls) == 1


def test_fully_covered_need_reuses_complete_original_body_without_child_search():
    svc, llm, direct, fetcher = service(plan([covered(quote="annual output was 120 units in 2025.")]))
    result = svc.run("annual output", collections=["col-a"])
    assert fetcher.calls == []
    assert [c.text for c in result.retained_chunks] == [BODY]
    assert BODY in result.context and BODY in result.answer
    assert any(e.get("kind") == "covered_need_source_reuse" for e in result.trace)
    assert direct.chunks[0].text == BODY


def test_only_missing_need_uses_native_fetcher_and_can_search_other_files_collections():
    svc, llm, direct, fetcher = service(plan(
        [covered()], [{"query": "operating permit status", "target_collections": []}],
    ))
    result = svc.run("annual output and operating permit", top_k=10, search_mode="hybrid")
    assert [c[0] for c in fetcher.calls] == ["operating permit status"]
    assert fetcher.calls[0][1] == ["col-a", "col-b"]
    assert fetcher.calls[0][2]["top_k"] == 10
    assert {c.metadata["id"] for c in result.retained_chunks} == {"chunk-a", "chunk-permit"}


@pytest.mark.parametrize("support", [
    covered(quote="The annual output was 900 units, which the source never says."),
    covered(chunk_id="made-up-id"),
    covered(collection="other-collection"),
    {"need": "annual output", "evidence": []},
])
def test_unverifiable_covered_need_becomes_a_real_search(support):
    svc, llm, direct, fetcher = service(plan([support]))
    svc.run("annual output", collections=["col-a"])
    assert [c[0] for c in fetcher.calls] == ["annual output"]


def test_exact_duplicate_missing_search_is_not_run_again_for_covered_need():
    svc, llm, direct, fetcher = service(plan(
        [covered()], [{"query": " annual   output ", "target_collections": []}],
    ))
    result = svc.run("annual output", collections=["col-a"])
    assert fetcher.calls == []
    assert len(result.retained_chunks) == 1


def test_conflicting_same_id_bodies_are_excluded_from_preliminary_evidence():
    svc, llm, direct, fetcher = service(
        plan([covered()], [{"query": "annual output", "target_collections": []}]),
        [passage(), passage("Entity A annual output was 900 units in 2025.")],
    )
    svc.run("annual output", collections=["col-a"])
    assert "<untrusted_primary_source_references>" not in llm.calls[0][0]
    assert [c[0] for c in fetcher.calls] == ["annual output"]


@pytest.mark.parametrize("row", [passage(is_current=False), passage(version_id=None)])
def test_stale_or_unidentified_source_is_not_used_to_mark_need_covered(row):
    svc, llm, direct, fetcher = service(
        plan([covered()], [{"query": "annual output", "target_collections": []}]), [row],
    )
    svc.run("annual output", collections=["col-a"])
    assert "<untrusted_primary_source_references>" not in llm.calls[0][0]
    assert len(fetcher.calls) == 1


def test_seed_body_limit_falls_back_without_truncating_original_source():
    long_body = "source text " * 2000
    svc, llm, direct, fetcher = service(
        plan([], [{"query": "annual output", "target_collections": []}]), [passage(long_body)],
    )
    svc.run("annual output", collections=["col-a"])
    assert long_body not in llm.calls[0][0]
    assert len(fetcher.calls) == 1
    assert direct.chunks[0].text == long_body


def test_source_visibility_is_checked_again_before_reuse(monkeypatch):
    svc, llm, direct, fetcher = service(plan([covered()]))
    visible = {"col-a", "col-b"}
    monkeypatch.setattr("src.identity.visibility.is_collection_visible", lambda actor, cid: cid in visible)
    generate = llm.generate

    def revoke_after_planning(prompt, **kwargs):
        result = generate(prompt, **kwargs)
        visible.remove("col-a")
        return result

    llm.generate = revoke_after_planning
    result = svc.run("annual output", collections=["col-a"])
    assert result.retained_chunks == []
    assert BODY not in result.context
    assert result.errors
    assert fetcher.calls == []


def test_preliminary_error_is_visible_and_does_not_start_paid_fanout():
    svc, llm, direct, fetcher = service(
        plan([covered()]), errors=[{"stage": "retrieve", "error": "ProviderError"}],
    )
    result = svc.run("annual output", collections=["col-a"])
    assert result.errors
    assert llm.calls == [] and fetcher.calls == []


def test_concurrent_runs_do_not_share_covered_sources_or_mutate_retrieved_rows():
    rows = {cid: passage(chunk_id=cid) for cid in ("first-id", "second-id")}
    before = deepcopy(rows)
    svc, llm, direct, fetcher = service([])
    direct.retrieve = lambda query, collections, **kwargs: DirectQueryResult(chunks=[rows[query]])
    llm.generate = lambda prompt, **kwargs: json.dumps(plan([
        covered(chunk_id="first-id" if "first-id" in prompt else "second-id"),
    ]))

    def run(chunk_id):
        result = svc.run(chunk_id, collections=["col-a"])
        return [c.metadata["id"] for c in result.retained_chunks]

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, ["first-id", "second-id"]))
    assert results == [["first-id"], ["second-id"]]
    assert rows == before


def test_routing_without_decomposition_does_not_add_preliminary_retrieval():
    svc, llm, direct, fetcher = service([0])
    svc.run("annual output", collections=["col-a"], decompose=False)
    assert direct.calls == []
    assert len(fetcher.calls) == 1


def test_planning_retains_all_missing_needs_instead_of_research_two_search_cap():
    queries = [{"query": f"missing fact {i}", "target_collections": []} for i in range(3)]
    svc, llm, direct, fetcher = service(plan([covered()], queries))
    result = svc.run("annual output plus three other facts", collections=["col-a"])
    assert len(fetcher.calls) == 3
    assert BODY in result.context


def test_original_question_and_outer_search_need_are_both_shown_to_new_planner():
    svc, llm, direct, fetcher = service(plan([covered()]))
    assert "original_user_question" in __import__("inspect").signature(svc.run).parameters
    svc.run("annual output", collections=["col-a"], original_user_question="Compare production and permits")
    assert "Compare production and permits" in llm.calls[0][0]
    assert "annual output" in llm.calls[0][0]
    assert direct.calls[0][0] == "annual output"


def test_repeated_tool_calls_can_disable_preliminary_retrieval():
    svc, llm, direct, fetcher = service(plan([], [{"query": "annual output", "target_collections": []}]))
    assert "preliminary_retrieval" in __import__("inspect").signature(svc.run).parameters
    svc.run("annual output", collections=["col-a"], preliminary_retrieval=False)
    assert direct.calls == []
    assert len(fetcher.calls) == 1


def test_covered_need_can_reuse_multiple_complete_primary_sources():
    second = passage("The report confirms the output measurement period is the full year.", "chunk-b")
    support = covered()
    support["evidence"].append({"collection": "col-a", "id": "chunk-b", "quote": second.text})
    svc, llm, direct, fetcher = service(plan([support]), [passage(), second])
    result = svc.run("annual output and measurement period", collections=["col-a"])
    assert fetcher.calls == []
    assert {c.metadata["id"] for c in result.retained_chunks} == {"chunk-a", "chunk-b"}
    assert BODY in result.context and second.text in result.context


def test_initial_hidden_sources_never_reach_preliminary_planner(monkeypatch):
    secret = passage("Hidden original text must not reach the planner.", "secret", "col-b")
    svc, llm, direct, fetcher = service(plan([covered()]), [passage(), secret])
    monkeypatch.setattr("src.identity.visibility.is_collection_visible", lambda actor, cid: cid == "col-a")
    svc.run("annual output")
    assert direct.calls[0][1] == ["col-a"]
    assert secret.text not in llm.calls[0][0]


def test_bad_plan_is_reported_without_extra_fanout():
    svc, llm, direct, fetcher = service({"not": "a task list"})
    result = svc.run("annual output", collections=["col-a"])
    assert result.errors[0]["stage"] == "evidence_plan"
    assert fetcher.calls == []
    assert len(llm.calls) == 1


def test_empty_plan_falls_back_to_a_broad_search():
    svc, llm, direct, fetcher = service([])
    svc.run("annual output", collections=["col-a"])
    assert [c[0] for c in fetcher.calls] == ["annual output"]


def test_exact_duplicate_gap_queries_are_fetched_once():
    svc, llm, direct, fetcher = service(plan([], [
        {"query": "permit status", "target_collections": [1]},
        {"query": " permit  status ", "target_collections": [1]},
    ]))
    svc.run("permit status")
    assert len(fetcher.calls) == 1
    assert fetcher.calls[0][1] == ["col-b"]
