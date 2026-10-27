"""Explicit global retrieval profiles shared by Chat and Recall."""


def search_settings(mode: str, *, config=None) -> dict:
    if mode not in ("direct", "agentic"):
        raise ValueError("mode must be direct or agentic")
    if config is None:
        from src.config import get_config
        config = get_config()
    profile = config.rag if mode == "agentic" else config.direct_rag
    return {"top_k": profile.top_k, "search_mode": profile.default_search_mode,
            "rerank_top_k": profile.rerank_top_k, "min_score": profile.min_score,
            "rerank_enabled": True if mode == "agentic" else profile.use_reranker}
