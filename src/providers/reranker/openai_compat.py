from __future__ import annotations

import logging

import httpx
from openai import OpenAI

from src.config import RerankProviderConfig
from src.providers.base import RerankerProvider
from src.providers.registry import reranker_registry

logger = logging.getLogger(__name__)


@reranker_registry.register("openai_compatible", "remote", display_name="OpenAI-Compatible")
class OpenAICompatReranker(RerankerProvider):
    """Calls a Jina/Cohere-compatible rerank API, falling back to chat completions.

    Primary: POST {base_url}/rerank (Jina/Cohere format)
    Fallback: POST {base_url}/chat/completions with yes/no prompt + logprobs
              (for Qwen3-Reranker deployed via oMLX / vLLM / Ollama)
    """

    def __init__(self, config: RerankProviderConfig):
        self._client = OpenAI(
            base_url=config.base_url.strip(),
            api_key=config.api_key.strip(),
            timeout=httpx.Timeout(1800, connect=30),
        )
        self._model = config.model.strip()
        self._top_k = config.top_k
        self._use_chat_fallback = False  # Set after first failed /rerank attempt

    def rerank(self, query: str, documents: list[str], top_k: int = 5) -> list[tuple[int, float]]:
        top_n = top_k or self._top_k or 5

        # Only use Qwen-style chat fallback for Qwen reranker models
        _is_qwen = "qwen" in self._model.lower()
        if not self._use_chat_fallback:
            try:
                result = self._rerank_via_api(query, documents, top_n)
                logger.debug("Rerank via API: %d results, top score=%.4f", len(result), result[0][1] if result else 0)
                return result
            except Exception as e:
                if _is_qwen:
                    logger.warning(
                        "Rerank API failed (%s), falling back to chat completions for %s",
                        e, self._model,
                    )
                    self._use_chat_fallback = True
                else:
                    raise

        if self._use_chat_fallback and _is_qwen:
            result = self._rerank_via_chat(query, documents, top_n)
            logger.debug("Rerank via chat fallback: %d results, top score=%.4f", len(result), result[0][1] if result else 0)
            return result

        raise RuntimeError(f"Rerank failed for {self._model} and no chat fallback available")

    def _rerank_via_api(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        body: dict = {
            "model": self._model,
            "query": query,
            "documents": documents,
            "top_n": top_n,
        }
        base = str(self._client.base_url).rstrip("/")
        headers = {"Authorization": f"Bearer {self._client.api_key}"}
        resp = httpx.post(f"{base}/rerank", json=body, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        logger.debug("Rerank API raw results: %s", [
            {"index": r.get("index"), "score": r.get("relevance_score")}
            for r in results[:5]
        ])
        return [(r["index"], r["relevance_score"]) for r in results[:top_n]]

    def _rerank_via_chat(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        """Rerank using chat completions with yes/no token logprobs.

        Compatible with Qwen3-Reranker deployed via oMLX, vLLM, Ollama, etc.
        Uses the official Qwen3-Reranker prompt format with yes/no logprobs.
        """
        instruction = "Given a web search query, retrieve relevant passages that answer the query"

        scores: list[float] = []
        for doc in documents:
            prompt = (
                "<|im_start|>system\n"
                "Judge whether the Document meets the requirements based on the Query and the Instruct provided. "
                "Note that the answer can only be \"yes\" or \"no\".<|im_end|>\n"
                "<|im_start|>user\n"
                f"<Instruct>: {instruction}\n"
                f"<Query>: {query}\n"
                f"<Document>: {doc}<|im_end|>\n"
                "<|im_start|>assistant\n"
            )

            try:
                resp = self._client.chat.completions.create(
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=1,
                    logprobs=True,
                    top_logprobs=20,
                    temperature=0,
                )
                logprobs = resp.choices[0].logprobs
                if logprobs and logprobs.content:
                    token_logprobs = logprobs.content[0].top_logprobs
                    yes_logprob = next(
                        (tlp.logprob for tlp in token_logprobs if tlp.token.strip().lower() == "yes"),
                        None,
                    )
                    no_logprob = next(
                        (tlp.logprob for tlp in token_logprobs if tlp.token.strip().lower() == "no"),
                        None,
                    )
                    if yes_logprob is not None or no_logprob is not None:
                        yes_lp = yes_logprob if yes_logprob is not None else -100.0
                        no_lp = no_logprob if no_logprob is not None else -100.0
                        scores.append(yes_lp - no_lp)
                        continue
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                logger.warning("logprobs-based rerank failed for doc: %s", e)

            # Fallback: plain text response
            try:
                resp = self._client.chat.completions.create(
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=5,
                    temperature=0,
                )
                text = (resp.choices[0].message.content or "").strip().lower()
                logger.debug("Rerank chat text response: %r", text[:100])
                if text.startswith("yes"):
                    scores.append(1.0)
                elif text.startswith("no"):
                    scores.append(0.0)
                else:
                    scores.append(0.0)
            except (ValueError, KeyError, TypeError, AttributeError):
                scores.append(0.0)

        if not scores:
            return []
        if all(s == 0.0 for s in scores):
            raise RuntimeError(
                "Rerank chat fallback failed: all scores are zero. "
                "The model may be offline or not a reranker-capable model."
            )

        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        logger.debug("Rerank chat scores: %s", [(idx, round(s, 4)) for idx, s in ranked[:5]])
        return ranked[:top_n]
