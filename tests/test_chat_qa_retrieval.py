"""Mixed Q&A evidence must go through the native relevance pipeline."""

import json
from importlib.util import find_spec
from types import SimpleNamespace

import pytest

from src.rag.agentic_query import AgenticQueryService
from src.rag.aggregator import Aggregator
from src.rag.decomposer import AtomicQuery
from src.rag.direct_query import DirectQueryModule, DirectQueryResult
from src.rag.reranker import Reranker
from src.rag.retriever import Retriever
from src.rag.variant_fetcher import VariantFetcher
from tests.test_qa import qa_fixture  # noqa: F401
from tests.test_qa_search_index import PROJECT, QUESTION, ANSWER, saved_exchange, vector_index  # noqa: F401

DOCUMENT = "The stage access checklist is kept next to the main door."
DOC_ID = "f2b1a8f8-dc21-42a7-a3de-00245aef770a"


class Ranker:
    def __init__(self):
        self.batches = []

    def rerank(self, query, texts, top_k):
        self.batches.append(texts)
        return [(i, 0.9) for i in range(min(len(texts), top_k))]


class Grader:
    def __init__(self, decision):
        self.decision = decision
        self.batches = []

    def generate(self, prompt, **kwargs):
        if "variant" in kwargs["system"].casefold():
            return "[]"
        self.batches.append(prompt)
        if self.decision == "error":
            raise RuntimeError("Example grading interrupted")
        import re

        selected = [
            int(index) for index, body in re.findall(
                r"^\[(\d+)\] \(database:[^\n]+\) ([\s\S]*?)(?=\n---\n|\Z)", prompt, re.M
            )
            if (ANSWER in body and self.decision == "keep") or DOCUMENT in body
        ]
        return json.dumps({"relevant_indices": selected, "retained_info": "", "gap_analysis": ""})


@pytest.mark.parametrize("decision", ["keep", "drop", "error"])
def test_agentic_reranks_mixed_candidates_and_exports_only_graded_qa_hits(qa, vector_index, decision):
    from src.qa import search_index
    from src.rag import source_scope

    saved_exchange(qa)
    search_index.sync_project(PROJECT)
    db, encoder = vector_index
    db.upsert_points(
        PROJECT, [DOC_ID], [[1.0, 0.0, 0.0]],
        [{"text": DOCUMENT, "source": "example.txt", "is_current": True}],
    )
    ranker, grader = Ranker(), Grader(decision)
    direct = DirectQueryModule(Retriever(db, encoder), db, Reranker(ranker), grader)
    catalog = SimpleNamespace(get_catalog=lambda *_args, **_kwargs: [SimpleNamespace(id=PROJECT)])
    decomposer = SimpleNamespace(
        decompose=lambda *_args, **_kwargs: [AtomicQuery(query="gate", target_collections=[PROJECT])]
    )
    service = AgenticQueryService(direct, VariantFetcher(direct, grader), catalog, decomposer, Aggregator(grader), grader)
    assert hasattr(source_scope, "qa_chat_scope"), "Mixed retrieval needs an explicit Chat-only scope"
    with source_scope.qa_chat_scope((PROJECT,)):
        result = service.run("gate", collections=[PROJECT], preliminary_retrieval=False, generate_answer=False)
    assert any(DOCUMENT in batch and any(ANSWER in part for part in batch) for batch in ranker.batches)
    assert any(DOCUMENT in batch and ANSWER in batch for batch in grader.batches)
    assert [hit.text for hit in result.qa_hits] == ([ANSWER] if decision == "keep" else [])
    assert any(error["stage"] == "qa_grade" for error in result.errors) == (decision == "error")
    assert ANSWER not in result.context
    assert all(chunk.metadata.get("source_kind") != "qa_reply" for chunk in result.all_chunks)


def test_named_project_excludes_other_projects_before_the_chat_tool_search(qa, monkeypatch):
    from src.collections import store as collections

    saved_exchange(qa)
    other = "col_other"
    outside = "col_outside"
    collections.create_collection_meta(other, "Sample Annex")
    collections.create_collection_meta(outside, "Example Workshop")
    assert find_spec("src.chatbox.qa_search"), "Chat needs to lock the user's named project"
    from src.chatbox.qa_search import QaTurn

    catalog = SimpleNamespace(get_catalog=lambda *_args, **_kwargs: [])
    agentic = SimpleNamespace(catalog=catalog, decomposer=None)
    calls = []
    def retrieve(_query, collections, **_kwargs):
        calls.append(collections)
        return DirectQueryResult()

    direct = SimpleNamespace(retrieve=retrieve)
    turn = QaTurn("What is the gate schedule in Example Library?", [PROJECT, other], agentic)
    turn.direct(direct, "gate schedule", collections=[PROJECT, other])
    assert calls == [[PROJECT]]

    turn = QaTurn("What is the gate schedule in Example Workshop?", [PROJECT, other], agentic)
    result = turn.direct(direct, "gate schedule", collections=[PROJECT, other])
    assert calls == [[PROJECT]]
    assert "select" in result.context.casefold()


def test_all_projects_resolves_a_short_project_name_before_mixed_search(qa):
    from src.chatbox.qa_search import QaTurn
    from src.collections import store as collections
    from src.rag.agentic_query import AgenticQueryResult
    from src.rag.source_scope import qa_chat_projects

    saved_exchange(qa)
    collections.update_collection_meta(PROJECT, name="Project Juniper")
    collections.create_collection_meta("col_annex", "Project Cedar")
    calls = []

    def run(query, *, collections, **_kwargs):
        calls.append((collections, qa_chat_projects.get()))
        return AgenticQueryResult(answer=None)

    service = SimpleNamespace(run=run)
    QaTurn("Juniper 项目的访客登记时间？", [], service).agentic(service, "gate schedule", collections=None)
    assert calls == [([PROJECT], (PROJECT,))]


def test_explicit_focused_lookup_uses_one_rank_and_grade(qa, vector_index):
    from src.qa import search_index
    from src.rag.source_scope import qa_chat_scope

    saved_exchange(qa)
    search_index.sync_project(PROJECT)
    db, encoder = vector_index
    db.upsert_points(PROJECT, [DOC_ID], [[1.0, 0.0, 0.0]], [{"text": DOCUMENT, "is_current": True}])
    ranker, grader = Ranker(), Grader("keep")
    direct = DirectQueryModule(Retriever(db, encoder), db, Reranker(ranker), grader)
    catalog_calls, plans = [], []

    def get_catalog(collections, **_kwargs):
        catalog_calls.append(collections)
        return [SimpleNamespace(id=PROJECT)]

    def decompose(*_args, **_kwargs):
        plans.append(True)
        return [AtomicQuery(query="related gate information", target_collections=[PROJECT])]

    service = AgenticQueryService(
        direct, VariantFetcher(direct, grader), SimpleNamespace(get_catalog=get_catalog),
        SimpleNamespace(decompose=decompose), Aggregator(grader), grader,
    )
    with qa_chat_scope((PROJECT,)):
        result = service.run(
            "gate", collections=[PROJECT], original_user_question=QUESTION,
            generate_answer=False, decompose=False,
        )
    assert not catalog_calls and not plans
    assert len(ranker.batches) == len(grader.batches) == 1
    assert [chunk.text for chunk in result.qa_hits] == [ANSWER]
    assert result.coverage_complete


def test_default_scoped_search_runs_the_plan_when_the_first_grade_misses_a_condition(qa):
    from src.rag.retriever import RetrievedChunk
    from src.rag.source_scope import qa_chat_scope
    from src.rag.variant_fetcher import VariantFetcherResult

    reply = RetrievedChunk("The gate is opened at seven.", 0.9, {
        "id": "reply", "collection": PROJECT, "source_kind": "qa_reply",
    })
    exception = RetrievedChunk("During rehearsal the gate remains closed until eight.", 0.8, {
        "id": "exception", "collection": PROJECT, "source": "example.txt",
    })
    searches = []

    def fetch(query, *, collections, **_kwargs):
        searches.append((query, collections))
        evidence = reply if query == "gate opening time" else exception
        return VariantFetcherResult(chunks=[evidence], retrieved_chunks=[evidence], gap_analysis="")

    catalog = SimpleNamespace(get_catalog=lambda *_args, **_kwargs: [SimpleNamespace(id=PROJECT)])
    plan = SimpleNamespace(decompose=lambda *_args, **_kwargs: [
        AtomicQuery(query="rehearsal exception", target_collections=[PROJECT]),
    ])
    service = AgenticQueryService(None, SimpleNamespace(run=fetch), catalog, plan, Aggregator(None), None)
    with qa_chat_scope((PROJECT,)):
        result = service.run(
            "gate opening time", collections=[PROJECT], generate_answer=False,
            original_user_question="When does the gate open, including during rehearsal?",
        )

    assert searches == [("gate opening time", [PROJECT]), ("rehearsal exception", [PROJECT])]
    assert result.qa_hits == [reply]
    assert exception.text in result.context


def test_scoped_grade_checks_the_whole_question_and_reuses_partial_evidence(qa, vector_index):
    from src.qa import search_index
    from src.rag.source_scope import qa_chat_scope

    saved_exchange(qa)
    search_index.sync_project(PROJECT)
    db, encoder = vector_index
    db.upsert_points(PROJECT, [DOC_ID], [[1.0, 0.0, 0.0]], [{"text": DOCUMENT, "is_current": True}])
    question = "When is the gate tested and where is the checklist stored?"
    plans = []

    class PartialGrader(Grader):
        def generate(self, prompt, **kwargs):
            raw = super().generate(prompt, **kwargs)
            if f"(local completion target)\n{question}" in prompt:
                result = json.loads(raw)
                result["gap_analysis"] = "Verify the checklist location independently."
                return json.dumps(result)
            return raw

    ranker, grader = Ranker(), PartialGrader("keep")
    direct = DirectQueryModule(Retriever(db, encoder), db, Reranker(ranker), grader)
    catalog = SimpleNamespace(get_catalog=lambda *_args, **_kwargs: [SimpleNamespace(id=PROJECT)])

    def decompose(*_args, **_kwargs):
        plans.append(True)
        return [AtomicQuery(query="checklist location", target_collections=[PROJECT])]

    service = AgenticQueryService(
        direct, VariantFetcher(direct, grader), catalog, SimpleNamespace(decompose=decompose),
        Aggregator(grader), grader,
    )
    with qa_chat_scope((PROJECT,)):
        result = service.run("gate timing", collections=[PROJECT], original_user_question=question, generate_answer=False)
    assert len(plans) == 1
    assert len(ranker.batches) == len(grader.batches) == 2
    assert any(chunk.text == ANSWER for chunk in result.qa_hits)
    assert DOCUMENT in result.context
    assert not result.coverage_complete


def test_an_explicit_inventory_request_keeps_the_directory_tool():
    from src.chatbox.qa_search import QaTurn

    calls = [
        {"function": {"name": "list_collections", "arguments": "{}"}},
        {"function": {"name": "search_knowledge_base", "arguments": '{"raw_query":"gate schedule"}'}},
    ]
    turn = QaTurn("List the available projects and check the gate schedule.", [], None)
    assert turn.plan_tool_calls(calls, has_catalog=True) == calls


@pytest.mark.parametrize("targets", [("col_other",), (PROJECT, "col_other")])
def test_agentic_scope_bounds_catalog_and_searches_each_target(qa, targets):
    from src.rag.source_scope import qa_chat_scope
    from src.rag.variant_fetcher import VariantFetcherResult

    catalog_calls, searched = [], []

    def get_catalog(collections, **_kwargs):
        catalog_calls.append(collections)
        return [SimpleNamespace(id=project) for project in collections]

    def fetch(query, *, collections, **_kwargs):
        searched.extend(collections)
        return VariantFetcherResult(query_used=query)

    catalog = SimpleNamespace(get_catalog=get_catalog)
    decomposer = SimpleNamespace(decompose=lambda *_args, **_kwargs: [
        AtomicQuery(query="rephrased", target_collections=[PROJECT]),
    ])
    service = AgenticQueryService(None, SimpleNamespace(run=fetch), catalog, decomposer, Aggregator(None), None)
    with qa_chat_scope(targets):
        service.run("gate schedule", collections=[PROJECT, "col_other"],
                    preliminary_retrieval=False, generate_answer=False)
    assert catalog_calls == [list(targets)]
    assert sorted(searched) == sorted(targets)
