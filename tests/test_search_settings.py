from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize("mode", ["direct", "agentic"])
def test_resolved_search_settings_use_the_correct_global_profile(mode):
    from src.config import AppConfig
    from src.rag.search_settings import search_settings

    config = AppConfig()
    config.direct_rag.top_k = 31
    config.direct_rag.default_search_mode = "hybrid"
    config.direct_rag.use_reranker = False
    config.rag.top_k = 27
    config.rag.default_search_mode = "dense"
    params = search_settings(mode, config=config)
    assert params["top_k"] == (31 if mode == "direct" else 27)
    assert params["search_mode"] == ("hybrid" if mode == "direct" else "dense")
    assert params["rerank_enabled"] is (mode == "agentic")


def test_agent_passes_sparse_preprocessing_model_when_enabled():
    from src.rag.agentic_query import AgenticQueryService
    from src.rag.aggregator import Aggregator
    from src.rag.variant_fetcher import VariantFetcherResult

    vf = MagicMock()
    vf.run.return_value = VariantFetcherResult()
    llm = MagicMock()
    service = AgenticQueryService(MagicMock(), vf, MagicMock(), MagicMock(), Aggregator(llm), llm)
    service.run("fact", collections=["col"], decompose=False, generate_answer=False,
                sparse_llm_tokenize=True)
    assert vf.run.call_args.kwargs.get("llm_for_sparse") is llm


def test_recall_agent_uses_agent_profile_and_honors_disabled_reranking(monkeypatch):
    from src.api.routes import recall
    from src.api.schemas import RecallSearchRequest
    from src.config import AppConfig
    from src.rag.agentic_query import AgenticQueryResult

    services = MagicMock()
    services.config = AppConfig()
    services.config.direct_rag.top_k = 31
    services.config.rag.top_k = 27
    services.db.get_collection_config.return_value = {}
    services.agentic_query.run.return_value = AgenticQueryResult(answer=None)
    monkeypatch.setattr(recall, "services", services)
    monkeypatch.setattr(recall.collections_store, "get_collection_meta", lambda cid: None)
    monkeypatch.setattr(recall, "get_embedding_overrides", lambda cols: {})
    monkeypatch.setattr("src.collections.file_index.load_for_read", lambda col: {})
    result = recall.recall_search(RecallSearchRequest(query="fact", collections=["col"],
                                                    use_agent=True, use_reranker=False))
    assert services.agentic_query.run.call_args.kwargs["top_k"] == 27
    assert services.agentic_query.run.call_args.kwargs["rerank_enabled"] is False
    assert result.search_params["use_reranker"] is False


def test_recall_omitted_threshold_uses_the_shared_profile(monkeypatch):
    from src.api.routes import recall
    from src.api.schemas import RecallSearchRequest
    from src.config import AppConfig

    services = MagicMock(config=AppConfig())
    services.config.direct_rag.min_score = .25
    monkeypatch.setattr(recall, "services", services)
    assert recall._resolve_recall_params(RecallSearchRequest(query="fact"), {})["min_score"] == .25


def test_recall_labels_displayed_fallback_as_excluded_from_context(monkeypatch):
    from src.api.routes import recall
    from src.api.schemas import RecallSearchRequest
    from src.config import AppConfig
    from src.rag.agentic_query import AgenticQueryResult
    from src.rag.retriever import RetrievedChunk

    services = MagicMock(config=AppConfig())
    services.db.get_collection_config.return_value = {}
    chunk = RetrievedChunk("gold", .9, {"id": "gold", "collection": "col", "source": "doc.txt"})
    services.agentic_query.run.return_value = AgenticQueryResult(answer=None, all_chunks=[chunk],
                                                               retained_chunks=[])
    monkeypatch.setattr(recall, "services", services)
    monkeypatch.setattr(recall.collections_store, "get_collection_meta", lambda cid: None)
    monkeypatch.setattr(recall, "get_embedding_overrides", lambda cols: {})
    monkeypatch.setattr("src.collections.file_index.load_for_read", lambda col: {})
    result = recall.recall_search(RecallSearchRequest(query="fact", collections=["col"], use_agent=True))
    assert result.results[0].used_in_context is False


def test_recall_provider_override_is_request_local(monkeypatch):
    from src.api.routes import recall
    from src.api.schemas import RecallSearchRequest
    from src.config import AppConfig
    from src.rag.direct_query import DirectQueryModule
    from src.rag.retriever import RetrievedChunk

    services = MagicMock(config=AppConfig())
    services.db.get_collection_config.return_value = {}
    chunks = [RetrievedChunk("first", .9, {"id": "a", "source": "a.txt"}),
              RetrievedChunk("second", .8, {"id": "b", "source": "b.txt"})]
    retriever = MagicMock()
    retriever.retrieve.return_value = chunks
    default = MagicMock()
    default.rerank.side_effect = lambda query, chunks, **kwargs: chunks[:1]
    override = MagicMock()
    override.rerank.side_effect = lambda query, chunks, **kwargs: chunks[-1:]
    services.direct_query = DirectQueryModule(retriever, services.db, default)
    monkeypatch.setattr(recall, "services", services)
    monkeypatch.setattr(recall, "_resolve_reranker", lambda id_: override)
    monkeypatch.setattr(recall.collections_store, "get_collection_meta", lambda cid: None)
    monkeypatch.setattr(recall, "get_embedding_overrides", lambda cols: {})
    monkeypatch.setattr("src.collections.file_index.load_for_read", lambda col: {})
    result = recall.recall_search(RecallSearchRequest(query="fact", collections=["col"],
                                                    use_reranker=True, rerank_provider_id="alternate"))
    assert result.results[0].id == "b"
    assert services.direct_query.reranker is default
