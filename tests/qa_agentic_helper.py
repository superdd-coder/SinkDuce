"""Explicit shared-retrieval adapter for Q&A tests with deterministic evidence."""

from src.rag.agentic_query import AgenticQueryResult


def install_agentic(monkeypatch, services, retriever, *, retain=None):
    class Agentic:
        def __init__(self):
            self.calls = []

        def run(self, query, *, collections, **kwargs):
            self.calls.append((query, collections, kwargs))
            candidates = list(retriever.retrieve(query, collection=collections[0]))
            retained = candidates if retain is None else list(retain(candidates))
            return AgenticQueryResult(
                answer=None,
                all_chunks=candidates,
                candidate_chunks=candidates,
                retained_chunks=retained,
            )

    agentic = Agentic()
    monkeypatch.setattr(services, "agentic_query", agentic)
    return agentic
