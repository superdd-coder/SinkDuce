"""Independent qrels, complete query accounting, and measurable stage loss."""

import json
import math

import pytest


def write_dataset(path):
    path.mkdir(exist_ok=True)
    (path / "manifest.json").write_text(json.dumps({"name": "controlled", "version": "1",
                                                  "kind": "controlled", "granularity": "document"}))
    (path / "corpus.jsonl").write_text('\n'.join(json.dumps({"_id": i, "text": i})
                                               for i in ["a", "b", "c", "noise"]))
    (path / "queries.jsonl").write_text('\n'.join(json.dumps(q) for q in [
        {"_id": "positive", "text": "both facts", "evidence_groups": [["a", "b"], ["c"]]},
        {"_id": "negative", "text": "unanswerable", "answerable": False},
    ]))
    (path / "qrels.tsv").write_text("query-id\tcorpus-id\tscore\npositive\ta\t2\npositive\tb\t1\npositive\tc\t1\nnegative\tnoise\t0\n")
    return path


def test_metrics_use_all_gold_in_denominator_and_trec_linear_gain():
    from src.evaluation.metrics import retrieval_metrics

    metrics = retrieval_metrics(["b", "noise"], {"a": 2, "b": 1, "c": 1}, 10)
    assert metrics["recall"] == pytest.approx(1 / 3)
    assert metrics["precision"] == .1
    assert metrics["mrr"] == 1
    assert metrics["ndcg"] == pytest.approx(1 / (2 + 1 / math.log2(3) + .5))


def test_duplicate_hits_do_not_inflate_recall():
    from src.evaluation.metrics import retrieval_metrics

    result = retrieval_metrics(["a", "a", "b"], {"a": 1, "b": 1, "c": 1}, 2)
    assert result["recall"] == pytest.approx(2 / 3)
    assert result["hit"] == 1


def test_unanswerable_ir_metrics_are_undefined():
    from src.evaluation.metrics import retrieval_metrics

    assert retrieval_metrics(["noise"], {"noise": 0}, 10)["recall"] is None


@pytest.mark.parametrize("ids", [[""], [None], [123], [{"id": "a"}]])
def test_stage_trace_rejects_invalid_document_ids(ids):
    from pydantic import ValidationError
    from src.evaluation.models import StageResult

    with pytest.raises(ValidationError):
        StageResult(candidates=ids)


def test_dataset_loads_beir_style_files_and_fingerprints_contents(tmp_path):
    from src.evaluation.models import load_dataset

    path = write_dataset(tmp_path)
    first = load_dataset(path)
    assert set(first.queries) == {"positive", "negative"}
    assert first.queries["negative"].answerable is False
    (path / "corpus.jsonl").write_text((path / "corpus.jsonl").read_text().replace('"text": "a"', '"text": "changed"'))
    assert load_dataset(path).fingerprint != first.fingerprint


@pytest.mark.parametrize("corruption", ["unknown_doc", "duplicate_query", "missing_gold", "invalid_group"])
def test_dataset_rejects_invalid_gold(tmp_path, corruption):
    from src.evaluation.models import load_dataset

    path = write_dataset(tmp_path)
    if corruption == "unknown_doc":
        (path / "qrels.tsv").write_text("query-id\tcorpus-id\tscore\npositive\tmissing\t1\n")
    elif corruption == "duplicate_query":
        text = (path / "queries.jsonl").read_text()
        (path / "queries.jsonl").write_text(text + '\n' + text.splitlines()[0])
    elif corruption == "missing_gold":
        (path / "qrels.tsv").write_text("query-id\tcorpus-id\tscore\nnegative\tnoise\t0\n")
    else:
        text = (path / "queries.jsonl").read_text().replace('["c"]', '["noise"]')
        (path / "queries.jsonl").write_text(text)
    with pytest.raises(ValueError):
        load_dataset(path)


def test_service_reports_first_evidence_loss_and_errors(tmp_path):
    from src.evaluation.models import StageResult, load_dataset
    from src.evaluation.service import evaluate

    dataset = load_dataset(write_dataset(tmp_path))

    def runner(query):
        if not query.answerable:
            raise RuntimeError("provider unavailable")
        return StageResult(candidates=["a", "c"], context_ids=["a"], context="a",
                           parameters={"top_k": 10, "search_mode": "hybrid"})

    report = evaluate(dataset, runner, ks=[10])
    case = report["cases"][0]
    assert case["first_loss"] == "context"
    assert case["candidate"]["10"]["recall"] == pytest.approx(2 / 3)
    assert case["context"]["10"]["recall"] == pytest.approx(1 / 3)
    assert case["evidence_coverage"] == .5
    assert case["all_evidence_hit"] is False
    assert report["summary"]["errors"] == 1
    assert report["summary"]["queries"] == 2
    assert report["protocol"]["ndcg_gain"] == "linear"


def test_missing_positive_run_is_counted_as_a_miss(tmp_path):
    from src.evaluation.models import StageResult, load_dataset
    from src.evaluation.service import evaluate

    report = evaluate(load_dataset(write_dataset(tmp_path)), lambda _: StageResult(), ks=[10])
    assert report["summary"]["candidate"]["10"]["recall"] == 0
    assert report["summary"]["answerable_queries"] == 1


def test_saved_retrieval_latency_is_preserved_without_measuring_replay(tmp_path):
    from src.evaluation.models import StageResult, load_dataset
    from src.evaluation.service import evaluate

    report = evaluate(load_dataset(write_dataset(tmp_path)),
                      lambda _: StageResult(latency_ms=123.0), measure_latency=False)
    assert report["summary"]["latency_ms"]["p50"] == 123.0


def test_fallbacks_are_reported_separately_from_fatal_errors(tmp_path):
    from src.evaluation.models import StageResult, load_dataset
    from src.evaluation.service import evaluate

    def runner(query):
        return StageResult(trace=[{"kind": "retrieval_fallback", "mode": "dense"}])
    report = evaluate(load_dataset(write_dataset(tmp_path)), runner)
    assert report["summary"]["fallbacks"] == 2
    assert report["summary"]["errors"] == 0
    assert report["cases"][0]["trace"][0]["mode"] == "dense"


def test_pipeline_runner_uses_retained_evidence_not_display_fallback(tmp_path):
    from types import SimpleNamespace
    from src.rag.retriever import RetrievedChunk
    from src.evaluation.models import load_dataset
    from src.evaluation.service import PipelineRunner

    chunk = RetrievedChunk("gold", .9, {"evaluation_id": "a"})
    agent = SimpleNamespace(run=lambda *args, **kwargs: SimpleNamespace(
        all_chunks=[chunk], candidate_chunks=[chunk], retained_chunks=[], context="no evidence"))
    runner = PipelineRunner(agent, mode="agentic", collections=["col"], parameters={"top_k": 10})
    result = runner(load_dataset(write_dataset(tmp_path)).queries["positive"])
    assert result.candidates == ["a"]
    assert result.context_ids == []


def test_cli_scores_saved_runs_and_records_missing_queries(tmp_path, capsys):
    from src.evaluation.__main__ import main

    dataset_path = write_dataset(tmp_path / "dataset")
    run_path = tmp_path / "run.jsonl"
    run_path.write_text(json.dumps({"query_id": "positive", "candidates": ["a", "c"],
                                    "context_ids": ["a"], "context": "a"}) + '\n')
    output = tmp_path / "report.json"
    assert main(["score", "--dataset", str(dataset_path), "--run", str(run_path),
                 "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["summary"]["queries"] == 2
    assert report["summary"]["errors"] == 1
    assert report["cases"][1]["errors"][0]["error"] == "missing_run_query"
    assert report["summary"]["latency_ms"]["p50"] is None


def test_cli_runs_real_pipeline_factory(tmp_path, monkeypatch):
    import sys
    import types
    from src.evaluation.__main__ import main
    from src.evaluation.models import StageResult

    module = types.ModuleType("controlled_factory")
    module.make_runner = lambda dataset: lambda query: StageResult(candidates=["a", "c"], context_ids=["a"])
    monkeypatch.setitem(sys.modules, "controlled_factory", module)
    dataset_path = write_dataset(tmp_path / "dataset")
    output = tmp_path / "report.json"
    assert main(["run", "--dataset", str(dataset_path), "--runner", "controlled_factory:make_runner",
                 "--output", str(output)]) == 0
    assert json.loads(output.read_text())["summary"]["queries"] == 2


def test_controlled_factory_indexes_and_searches_real_pipeline(tmp_path):
    from src.evaluation.models import load_dataset
    from tests.evaluation_controlled import make_runner

    dataset = load_dataset(write_dataset(tmp_path))
    runner = make_runner(dataset)
    result = runner(dataset.queries["positive"])
    assert result.errors == []
    assert set(result.candidates).issubset(dataset.corpus)
    assert result.parameters["embedding_model"] == "controlled-bag-of-words"


def test_bundled_controlled_dataset_has_30_versioned_queries():
    from pathlib import Path
    from src.evaluation.models import load_dataset

    dataset = load_dataset(Path(__file__).parent / "fixtures" / "rag_controlled_v1")
    assert dataset.manifest["kind"] == "controlled"
    assert len(dataset.queries) == 30
    assert sum(not q.answerable for q in dataset.queries.values()) == 5
    assert sum(len(q.evidence_groups) == 2 for q in dataset.queries.values()) == 10


def test_controlled_embedding_is_frozen_independently_of_sparse_tokenizer(monkeypatch):
    from tests import evaluation_controlled as controlled

    # A production tokenizer experiment must not change the fixed dense model.
    monkeypatch.setattr(controlled, "_tokenize", lambda _: ["changed"], raising=False)
    embedding = controlled.ControlledEmbedding({"a": {"text": "alpha beta"}})
    assert embedding.dimensions == 2
    assert embedding.embed_query("alpha") != embedding.embed_query("beta")
