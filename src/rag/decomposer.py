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

STEP 1 — Split the query into independent tasks. Use these criteria:
  - If you could write the answer as one section vs. needing separate sections with
    their own headers, they are separate tasks.
  - If each part asks about a different metric, entity, or time period that would
    require its own data lookup, it is a separate task.
  - If the parts share the same answer structure and can be answered in one coherent
    paragraph, they belong to the same task.
  Examples:
    "risks and mitigation for plant X" → 1 task (same entity, one coherent answer)
    "A vs B costs, and A's 2024 AR"    → 2 tasks (different metrics: cost comparison
                                          vs. accounts receivable lookup)
    "Compare A/B finances and check car sales data" → 2 tasks (different entities)
  - Give EVERY task two fields:
    "task": a short label that includes the KEY terms distinguishing this task.
    "task_query": a complete sentence describing what this task is asking.
  - If the ENTIRE query needs NO knowledge base search, return [].

STEP 2 — Within each task, produce natural, well-formed search queries.
  - Write each as a complete, grammatical question or sentence, not keyword fragments.
  - Same language as the user's input.
  - Route to collections by matching coverage/definition/tags. Use the collection ID (in parentheses) for target_collections. [] if unsure.

Respond with ONLY a JSON array:
[{"task": "...", "task_query": "...", "queries": [{"query": "...", "target_collections": [...]}]}]"""

_DECOMPOSE_USER = """User query: {raw_query}

Available collections:
{catalog_text}

First identify the tasks, then produce search queries within each task.
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

        # Build catalog text
        catalog_text = self._build_catalog_text(catalog)
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
        catalog_ids = self._catalog_ids(catalog)
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
                target = q.get("target_collections", [])
                if not isinstance(target, list):
                    target = []
                target = [str(c) for c in target]
                if catalog_ids:
                    target = [c for c in target if c in catalog_ids]
                aqs.append(AtomicQuery(query=query, target_collections=target,
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
    def _build_catalog_text(catalog: list) -> str:
        lines = []
        for entry in catalog:
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
            line = f"- Collection: {name} (id: {eid})\n  Definition: {defn}"
            if cov:
                line += f"\n  Coverage: {cov}"
            if tags:
                line += f"\n  Tags: {', '.join(tags)}"
            lines.append(line)
        return "\n".join(lines)

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
