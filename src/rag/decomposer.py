"""Decomposer — one-pass query decomposition with collection routing.

A single LLM call breaks the user's raw query into AtomicQueries,
each assigned to target collections from the Catalog and grouped
for parallel retrieval + grouped aggregation.
"""

from __future__ import annotations

import json
import logging
import uuid
import html
from dataclasses import dataclass, field

from src.rag.agent_prompts import (
    DECOMPOSE_SYSTEM,
    DECOMPOSE_USER,
    ROUTE_SYSTEM,
    ROUTE_USER,
    EVIDENCE_PLAN_SYSTEM,
    EVIDENCE_PLAN_USER,
    SCOPED_DECOMPOSE_SYSTEM,
    SCOPED_DECOMPOSE_USER,
)

logger = logging.getLogger(__name__)


@dataclass
class AtomicQuery:
    query: str
    target_collections: list[str] = field(default_factory=list)
    task: str = ""        # short task label
    task_query: str = ""  # complete sentence: what this task is asking
    covered_chunks: list = field(default_factory=list)  # actual preliminary source bodies
    covered_quotes: list[str] = field(default_factory=list)


class Decomposer:
    """One-pass query decomposition with catalog-aware collection routing."""

    def __init__(self, llm):
        self.llm = llm

    # ── collection routing (lightweight, no task decomposition) ──────

    def route_collections(self, raw_query: str, catalog: list) -> list[str]:
        """Return the collection IDs relevant to *raw_query*, or [] for all."""
        if not raw_query or not raw_query.strip():
            return []
        if not catalog:
            return []

        catalog_text, index_map = self._build_catalog_text(catalog)
        if not catalog_text:
            return []

        prompt = ROUTE_USER.format(raw_query=raw_query[:200], catalog_text=catalog_text)

        try:
            result = self._llm_json(prompt, ROUTE_SYSTEM)
        except Exception as e:
            logger.warning("[Route] LLM failed: %s, fallback to all", e)
            return []

        if not isinstance(result, list):
            return []

        target_ids = []
        for t in result:
            try:
                idx = int(t)
            except (ValueError, TypeError):
                continue
            if idx in index_map:
                target_ids.append(index_map[idx])

        if target_ids:
            cols = target_ids
        else:
            cols = []
        logger.info("[Route] q=%r → %s", raw_query[:120], cols if cols else "ALL")
        return cols

    # ── full decomposition ───────────────────────────────────────────

    def decompose(self, raw_query: str, catalog: list, *, original_user_question: str = "") -> list[AtomicQuery]:
        """Break *raw_query* into AtomicQueries with collection routing.

        *catalog* should be a list of ``CatalogEntry`` objects (or dicts with
        ``name``, ``id``, ``definition``, ``coverage``, ``tags`` fields).

        Returns [] if the entire query is non-retrieval (greeting, draft email, etc.)
        or if decomposition fails.
        """
        logger.info("[Decompose] q=%r catalog=%d", raw_query[:200], len(catalog))

        if not raw_query or not raw_query.strip():
            return []

        # Build catalog text with numeric index mapping (ephemeral — GC'd on return)
        catalog_text, index_map = self._build_catalog_text(catalog)
        if not catalog_text:
            catalog_text = "(no collections available — target_collections must be [])"

        if original_user_question:
            prompt = SCOPED_DECOMPOSE_USER.format(
                original_user_question=original_user_question,
                raw_query=raw_query, catalog_text=catalog_text,
            )
            system = SCOPED_DECOMPOSE_SYSTEM
        else:
            prompt = DECOMPOSE_USER.format(raw_query=raw_query, catalog_text=catalog_text)
            system = DECOMPOSE_SYSTEM

        try:
            result = self._llm_json(prompt, system)
        except Exception as e:
            logger.warning("[Decompose] LLM failed: %s, fallback to single AQ", e)
            return [AtomicQuery(query=raw_query)]

        if not isinstance(result, list):
            logger.warning("[Decompose] expected list, got %s, fallback", type(result))
            return [AtomicQuery(query=raw_query)]

        # Parse: [{"task": "...", "queries": [...]}] → flatten to AtomicQuery list
        # Also accept flat format [{"query": "...", "task": "...", ...}] for backward compat.
        aqs = []
        for item in result:
            if not isinstance(item, dict):
                continue
            task = str(item.get("task", "")).strip()
            task_query = str(item.get("task_query", "")).strip()
            queries = item.get("queries")
            if isinstance(queries, list):
                pass  # nested format
            elif isinstance(item.get("query"), str):
                queries = [item]  # flat format — wrap as single-task group
            else:
                continue
            for q in queries:
                if not isinstance(q, dict):
                    continue
                query = str(q.get("query", "")).strip()
                if not query:
                    continue
                # Resolve numeric index → collection ID
                raw_targets = q.get("target_collections", [])
                if not isinstance(raw_targets, list):
                    raw_targets = []
                target_ids = []
                for t in raw_targets:
                    try:
                        idx = int(t)
                    except (ValueError, TypeError):
                        # Backward compat: accept string IDs directly
                        idx = None
                    if idx is not None and idx in index_map:
                        target_ids.append(index_map[idx])
                    elif isinstance(t, str) and t in self._catalog_ids(catalog):
                        target_ids.append(t)
                aqs.append(AtomicQuery(query=query, target_collections=target_ids,
                                       task=task, task_query=task_query))

        if not aqs:
            logger.info("[Decompose] done — 0 retrieval AQs (all non-retrieval)")
            return []

        # ── Print task plan ──────────────────────────────────────────
        # Group AQs by task for a readable table
        task_aqs: dict[str, list[AtomicQuery]] = {}
        for aq in aqs:
            t = aq.task or "(no task)"
            task_aqs.setdefault(t, []).append(aq)

        logger.info("[Decompose] %d AQs in %d tasks:", len(aqs), len(task_aqs))
        for task_label, items in task_aqs.items():
            tq = items[0].task_query or task_label
            cols = items[0].target_collections or ["ALL"]
            logger.info("[Decompose]   [%s] %s → %s", task_label, tq[:100], ", ".join(cols[:3]))
            for aq in items:
                logger.info("[Decompose]     ↳ %s", aq.query[:120])

        return aqs

    def plan_with_evidence(
        self, raw_query: str, catalog: list, seed_chunks: list,
        *, original_user_question: str = "",
    ) -> list[AtomicQuery]:
        """One model call classifies existing evidence and plans only missing needs."""
        from src.rag.context_builder import build_context
        from src.rag.evidence_planner import covered_evidence, normalized_text

        catalog_text, index_map = self._build_catalog_text(catalog)
        native_request = DECOMPOSE_USER.format(raw_query=raw_query, catalog_text=catalog_text)
        identities = [{k: c.metadata[k] for k in ("collection", "id", "file_id", "version_id")}
                      for c in seed_chunks]
        prompt = EVIDENCE_PLAN_USER.format(
            original_user_question=html.escape(original_user_question or raw_query, quote=True),
            native_request=html.escape(native_request, quote=True),
            seed_context=html.escape(build_context(seed_chunks), quote=True),
            source_identity_catalog=html.escape(json.dumps(identities), quote=True),
        )
        from src.rag.image_evidence import evidence_for_model
        result = self._llm_json(evidence_for_model(prompt, seed_chunks, self.llm), EVIDENCE_PLAN_SYSTEM)
        if not isinstance(result, list):
            raise ValueError("Evidence plan must be a task list")
        aqs = []
        allowed_ids = self._catalog_ids(catalog)
        for item in result:
            if not isinstance(item, dict) or not isinstance(item.get("covered"), list) or not isinstance(item.get("queries"), list):
                raise ValueError("Invalid evidence plan task")
            task, task_query = item.get("task", ""), item.get("task_query", "")
            if not isinstance(task, str) or not isinstance(task_query, str):
                raise ValueError("Invalid evidence plan task label")
            task, task_query = task.strip(), task_query.strip()
            known_needs = set()
            fallback_queries = []
            for supported in item["covered"]:
                if not isinstance(supported, dict) or not isinstance(supported.get("need"), str) or not supported["need"].strip():
                    raise ValueError("Invalid covered need")
                need = supported["need"].strip()
                proof = covered_evidence(supported, seed_chunks)
                if proof is None:
                    fallback_queries.append({"query": need, "target_collections": []})
                    continue
                key = normalized_text(need).casefold()
                if key in known_needs:
                    continue
                known_needs.add(key)
                chunks, quotes = proof
                aqs.append(AtomicQuery(
                    query=need, task=task, task_query=task_query,
                    target_collections=list(dict.fromkeys(c.metadata["collection"] for c in chunks)),
                    covered_chunks=chunks, covered_quotes=quotes,
                ))
            seen = set()
            for q in item["queries"] + fallback_queries:
                if not isinstance(q, dict) or not isinstance(q.get("query"), str) or not q["query"].strip():
                    raise ValueError("Invalid missing query")
                query = q["query"].strip()
                raw_targets = q.get("target_collections", [])
                if not isinstance(raw_targets, list):
                    raise ValueError("Invalid missing-query routing")
                targets = []
                for target in raw_targets:
                    if isinstance(target, bool) or not isinstance(target, (str, int)):
                        raise ValueError("Invalid collection target")
                    try:
                        idx = int(target)
                    except ValueError:
                        idx = None
                    if idx in index_map:
                        targets.append(index_map[idx])
                    elif target in allowed_ids:
                        targets.append(target)
                key = (normalized_text(query).casefold(), tuple(sorted(set(targets))))
                if key[0] in known_needs or key in seen:
                    continue
                seen.add(key)
                aqs.append(AtomicQuery(query=query, target_collections=targets, task=task, task_query=task_query))
        if not aqs:
            return [AtomicQuery(query=raw_query)]
        return aqs

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _build_catalog_text(catalog: list) -> tuple[str, dict[int, str]]:
        """Build catalog text with numeric indices for LLM routing.

        Returns (catalog_text, index_map) where index_map is {0: collection_id, 1: ...}.
        The index_map is a local ephemeral dict — garbage-collected when decompose() returns.
        """
        lines = []
        index_map: dict[int, str] = {}
        for i, entry in enumerate(catalog):
            if hasattr(entry, "name"):
                name, eid, defn, cov, tags = (
                    entry.name, entry.id, entry.definition,
                    entry.coverage, entry.tags,
                )
            elif isinstance(entry, dict):
                name = entry.get("name", "")
                eid = entry.get("id", "")
                defn = entry.get("definition", "")
                cov = entry.get("coverage", "")
                tags = entry.get("tags", [])
            else:
                continue
            index_map[i] = eid
            line = f"[{i}] {name}"
            if defn:
                line += f"\n    description: {defn}"
            if cov:
                line += f"\n    aspects: {cov}"
            if tags:
                line += f"\n    tags: {', '.join(tags)}"
            lines.append(line)
        return "\n".join(lines), index_map

    @staticmethod
    def _catalog_ids(catalog: list) -> set[str]:
        ids = set()
        for entry in catalog:
            if hasattr(entry, "id"):
                ids.add(entry.id)
            elif isinstance(entry, dict):
                ids.add(entry.get("id", ""))
        return ids

    @staticmethod
    def _new_id() -> str:
        return str(uuid.uuid4())[:8]

    def _llm_json(self, prompt: str | list, system: str) -> dict | list:
        """Call LLM and parse JSON from response."""
        raw = self.llm.generate(prompt, system=system, max_tokens=4096, thinking=False).strip()
        try:
            return self._parse_json(raw)
        except Exception:
            logger.warning("[Decompose] LLM returned invalid JSON. Raw output (first 500 chars): %r", raw[:500])
            raise

    @staticmethod
    def _parse_json(text: str) -> dict | list:
        """Parse JSON from LLM output, handling markdown fences."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines)
        return json.loads(text)
