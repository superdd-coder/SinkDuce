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

STEP 1 — Split the query into independent tasks:
  - Separate tasks = truly independent topics that would need DIFFERENT documents to answer
  - Single task = the same documents/chunks would contain all the information needed
  - Do NOT split closely related sub-questions that share the same topic and document scope.
    Example: "why is X reliable and low-cost" is ONE task, NOT two tasks
    for "reliable" and "low-cost" separately (same topic, same documents).
  - Example: "A vs B costs, and A's 2024 AR" → 2 tasks (cost comparison across companies
    vs. a specific company's annual report — different document scope)
  - Each task MUST have: "task" (short label with key terms) and "task_query" (complete sentence)
  - If the query needs NO knowledge base search, return [].

STEP 2 — Within each task, produce 1-2 search queries as complete questions (not keyword fragments).
  Do NOT create a separate query for each adjective or minor variation of the same question.
  Route to collections by matching their description/type/tags. Use the index numbers in [brackets]
  for target_collections, e.g. [0, 2]. Omit the field if unsure (the system will search all).

Respond with ONLY a JSON array:
[{"task": "...", "task_query": "...", "queries": [{"query": "...", "target_collections": [...]}]}]"""

_DECOMPOSE_USER = """User query: {raw_query}

Available collections (use [index] for routing):
{catalog_text}

First identify the tasks, then produce search queries within each task. Route each query to the most relevant collections by index number.
Return a JSON array."""


class Decomposer:
    """One-pass query decomposition with catalog-aware collection routing."""

    def __init__(self, llm):
        self.llm = llm

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

        # Log tasks
        tasks = {}
        for aq in aqs:
            t = aq.task or "(single)"
            tasks.setdefault(t, 0)
            tasks[t] += 1
        logger.info("[Decompose] done — %d AQs across %d tasks: %s", len(aqs), len(tasks), tasks)
        # Log task context
        seen_tasks = {}
        for aq in aqs:
            t = aq.task or "(single)"
            if t not in seen_tasks and aq.task_query:
                logger.info("[Decompose]   task %r: %s", t, aq.task_query)
                seen_tasks[t] = True
        # Log each AQ
        for aq in aqs:
            cols = aq.target_collections if aq.target_collections else ["ALL"]
            label = aq.task or "-"
            logger.info("[Decompose]     ↳ [%s] %r → %s", label, aq.query[:120], cols)

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
            if cov:
                line += f" — {cov}"
            if tags:
                line += f" | tags: {', '.join(tags)}"
            if defn:
                line += f"\n    {defn}"
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
