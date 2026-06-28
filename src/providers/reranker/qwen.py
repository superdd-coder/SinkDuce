from __future__ import annotations

import logging

import httpx

from src.config import RerankProviderConfig
from src.providers.base import RerankerProvider
from src.providers.registry import reranker_registry

logger = logging.getLogger(__name__)


@reranker_registry.register("qwen", display_name="Qwen (DashScope)")
class QwenReranker(RerankerProvider):
    def __init__(self, config: RerankProviderConfig):
        self._api_key = config.api_key
        self._model = config.model or "qwen3-vl-rerank"
        self._base_url = (config.base_url or "https://dashscope.aliyuncs.com/api/v1").rstrip("/")

    def rerank(self, query: str, documents: list[str], top_k: int = 5) -> list[tuple[int, float]]:
        logger.debug("Qwen rerank: %d docs, top_k=%d, model=%s", len(documents), top_k, self._model)
        url = f"{self._base_url}/services/rerank/text-rerank/text-rerank"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "input": {
                "query": query,
                "documents": documents,
            },
            "parameters": {
                "top_n": top_k,
            },
        }
        resp = httpx.post(url, json=payload, headers=headers, timeout=30)
        if not resp.is_success:
            logger.error("Qwen rerank HTTP %d: %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
        data = resp.json()

        results = data.get("output", {}).get("results", [])
        if not results:
            logger.warning("Qwen rerank returned 0 results, raw response: %s",
                          json.dumps(data, ensure_ascii=False)[:500])
        return [(r["index"], r["relevance_score"]) for r in results]
