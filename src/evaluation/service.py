from __future__ import annotations

import hashlib
import math
import time
from collections import Counter
from pathlib import Path

from src.evaluation.metrics import evidence_coverage, retrieval_metrics
from src.evaluation.models import Dataset, QueryCase, StageResult


class PipelineRunner:
    """Adapt the real Direct/Agentic service without replacing its retrieval logic.

    Gold IDs refer to stable documents: evaluation_id or canonical source. Point
    UUIDs are deliberately excluded. The caller supplies initialized services
    and explicit parameters, retaining the caller's actor/visibility context.
    """

    def __init__(self, pipeline, *, mode: str, collections: list[str], parameters: dict,
                 run_metadata: dict | None = None):
        if mode not in ("direct", "agentic"):
            raise ValueError("mode must be direct or agentic")
        self.pipeline, self.mode = pipeline, mode
        self.collections, self.parameters = collections, dict(parameters)
        self.run_metadata = dict(run_metadata or {})

    def __call__(self, query: QueryCase) -> StageResult:
        kwargs = dict(self.parameters, collections=self.collections, generate_answer=False)
        if self.mode == "agentic":
            result = self.pipeline.run(query.text, **kwargs)
            context_chunks = result.retained_chunks
        else:
            result = self.pipeline.retrieve(query.text, **kwargs)
            context_chunks = result.chunks

        def ids(chunks):
            output = []
            for chunk in chunks:
                id_ = chunk.metadata.get("evaluation_id") or chunk.metadata.get("source")
                if not isinstance(id_, str) or not id_:
                    raise ValueError("Retrieved chunk has no stable evaluation document ID")
                output.append(id_)
            return list(dict.fromkeys(output))

        return StageResult(candidates=ids(result.candidate_chunks), context_ids=ids(context_chunks),
                           context=result.context,
                           parameters=dict(self.run_metadata, **self.parameters, mode=self.mode,
                                           collections=self.collections),
                           errors=list(getattr(result, "errors", [])),
                           trace=list(getattr(result, "trace", [])))


def evaluate(dataset: Dataset, runner, *, ks: list[int] | None = None,
             measure_latency: bool = True) -> dict:
    """Evaluate every query; provider failures remain in the denominator."""
    ks = sorted(set(ks or [5, 10, 20]))
    if any(k <= 0 for k in ks):
        raise ValueError("Cutoffs must be positive")
    cases = []
    for query in dataset.queries.values():
        started = time.perf_counter()
        try:
            result = runner(query)
        except Exception as exc:
            result = StageResult(errors=[{"stage": "execution", "error": type(exc).__name__}])
        elapsed_ms = result.latency_ms
        if elapsed_ms is None and measure_latency:
            elapsed_ms = (time.perf_counter() - started) * 1000
        qrels = dataset.qrels[query.id]
        positive = {id_ for id_, grade in qrels.items() if grade > 0}
        candidate_coverage = evidence_coverage(result.candidates, query.evidence_groups)
        coverage = evidence_coverage(result.context_ids, query.evidence_groups)
        if result.errors:
            loss = "error"
        elif not query.answerable:
            loss = "unanswerable"
        elif (candidate_coverage is not None and candidate_coverage < 1) or (
            candidate_coverage is None and not positive.issubset(result.candidates)
        ):
            loss = "retrieve"
        elif (coverage is not None and coverage < 1) or (
            coverage is None and not positive.issubset(result.context_ids)
        ):
            loss = "context"
        else:
            loss = "none"
        cases.append({
            "query_id": query.id, "query": query.text, "category": query.category,
            "answerable": query.answerable, "candidates": result.candidates,
            "context_ids": result.context_ids, "actual_context": result.context,
            "candidate": {str(k): retrieval_metrics(result.candidates, qrels, k) for k in ks},
            "context": {str(k): retrieval_metrics(result.context_ids, qrels, k) for k in ks},
            "candidate_evidence_coverage": candidate_coverage, "evidence_coverage": coverage,
            "all_evidence_hit": coverage == 1 if coverage is not None else None,
            "first_loss": loss, "errors": result.errors, "parameters": result.parameters,
            "trace": result.trace,
            "unjudged_candidates": len(set(result.candidates) - set(qrels)),
            "elapsed_ms": elapsed_ms,
        })

    def mean(values):
        numbers = [value for value in values if value is not None]
        return sum(numbers) / len(numbers) if numbers else None

    summary = {"queries": len(cases), "answerable_queries": sum(c["answerable"] for c in cases),
               "errors": sum(bool(c["errors"]) for c in cases),
               "fallbacks": sum(any(e.get("kind") == "retrieval_fallback" for e in c["trace"]) for c in cases),
               "first_loss": dict(Counter(c["first_loss"] for c in cases)),
               "evidence_coverage": mean(c["evidence_coverage"] for c in cases),
               "all_evidence_hit": mean(c["all_evidence_hit"] for c in cases)}
    for stage in ("candidate", "context"):
        summary[stage] = {str(k): {metric: mean(c[stage][str(k)][metric] for c in cases)
                                 for metric in ("recall", "precision", "mrr", "ndcg", "hit")}
                          for k in ks}
    times = sorted(c["elapsed_ms"] for c in cases if c["elapsed_ms"] is not None)
    summary["latency_samples"] = len(times)
    summary["latency_ms"] = {f"p{p}": times[math.ceil(len(times) * p / 100) - 1] if times else None
                             for p in (50, 95)}
    code_digest = hashlib.sha256()
    source = Path(__file__).resolve().parents[1]
    for path in sorted(source.rglob("*.py")):
        code_digest.update(str(path.relative_to(source)).encode())
        code_digest.update(path.read_bytes())
    return {"schema_version": 1, "dataset": dataset.manifest,
            "dataset_fingerprint": dataset.fingerprint, "code_fingerprint": code_digest.hexdigest(),
            "protocol": {"ks": ks, "ndcg_gain": "linear", "relevance": ">0",
                         "deduplication": "stable_document_id", "unjudged": "nonrelevant",
                         "latency": "executor_or_supplied" if measure_latency else "supplied_only",
                         "generation_evaluated": False, "scope": "retrieval_and_context"},
            "summary": summary, "cases": cases}
