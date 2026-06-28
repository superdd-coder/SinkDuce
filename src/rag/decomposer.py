"""Decomposer — one-pass query decomposition with collection routing.

A single LLM call breaks the user's raw query into AtomicQueries,
each assigned to target collections from the Catalog and grouped
for parallel retrieval + grouped aggregation.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AtomicQuery:
    query: str
    target_collections: list[str] = field(default_factory=list)
    task: str = ""        # short task label
    task_query: str = ""  # complete sentence: what this task is asking


# ── Prompt ────────────────────────────────────────────────────────────────

_DECOMPOSE_SYSTEM = """You are a search query optimizer for a knowledge base system.

Your input is a concrete set of information needs — NOT a user question. Someone upstream
has already decided WHAT to search for. Your job is HOW to search it optimally.

GUIDING PRINCIPLE — The available collections define the searchable universe:
  - Each collection has an "aspects" field — a compact inventory of concrete topics
    its documents cover.
  - When the information needs clearly match specific aspects, split into focused
    AtomicQueries targeting those collections.
  - When the information needs are only loosely related to the available aspects
    (or the aspects are too vague to split on), produce a single broad AtomicQuery
    with no target_collections — the system will search all available collections
    and let the relevance grader filter results downstream.
  - When the information needs are clearly about a completely different domain
    than ALL collections' aspects, return [] — there is nothing to find here.
  - When in doubt between returning [] and a broad query, prefer the broad query.
    The downstream grader is better at filtering irrelevance than you are at
    predicting it from compact aspect labels.

STEP 1 — Match and group:
  - Scan each collection's aspects. Where an information need aligns with a
    specific listed aspect, create an AtomicQuery targeting that collection.
  - Group AtomicQueries by the ENTITY or PROJECT they are about, not by the
    aspect. One task = one entity/project. Assign a short
    "task" label (the entity name) and a "task_query" describing what this
    overall task is asking about that entity.
  - Within each task, each matched aspect produces 1 search query as a
    complete question.
  - Route to collections using the index numbers in [brackets], e.g. [0, 2].
    Omit target_collections to search all.

STEP 2 — When aspects are too vague to split:
  - If the aspects are generic labels (e.g. "Technical specifications") rather
    than concrete topic inventories, treat them as "no specific match" and
    produce a single broad AQ.

Respond with ONLY a JSON array:
[{"task": "...", "task_query": "...", "queries": [{"query": "...", "target_collections": [...]}]}]"""

_DECOMPOSE_USER = """Information needs: {raw_query}

Available collections (use [index] for routing):
{catalog_text}

These collections are the data sources. If the information needs are clearly
about a different domain than these, return []. Otherwise, match and split.

Return a JSON array."""


class Decomposer:
    """One-pass query decomposition with catalog-aware collection routing."""

    def __init__(self, llm):
        self.llm = llm

    # ── collection routing (lightweight, no task decomposition) ──────

    _ROUTE_SYSTEM = """You are a collection router. Given a search query and a list of
available collections, return the numeric indices of the most relevant collections.

Rules:
- Return only indices of collections that are genuinely relevant
- If no collection clearly matches, return [] to search all
- Do NOT invent collections — only use indices from the provided list

Respond with ONLY a JSON array of integers, e.g. [0] or [0, 2] or []."""

    _ROUTE_USER = """Query: {raw_query}

Available collections:
{catalog_text}

Which collections (by index) are relevant? Return a JSON array."""

    def route_collections(self, raw_query: str, catalog: list) -> list[str]:
        """Return the collection IDs relevant to *raw_query*, or [] for all."""
        if not raw_query or not raw_query.strip():
            return []
        if not catalog:
            return []

        catalog_text, index_map = self._build_catalog_text(catalog)
        if not catalog_text:
            return []

        prompt = self._ROUTE_USER.format(raw_query=raw_query[:200], catalog_text=catalog_text)

        try:
            result = self._llm_json(prompt, self._ROUTE_SYSTEM)
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

    def decompose(self, raw_query: str, catalog: list) -> list[AtomicQuery]:
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

        prompt = _DECOMPOSE_USER.format(raw_query=raw_query, catalog_text=catalog_text)

        try:
            result = self._llm_json(prompt, _DECOMPOSE_SYSTEM)
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

    def _llm_json(self, prompt: str, system: str) -> dict | list:
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
