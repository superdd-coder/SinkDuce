"""Aggregator — merge sub-query results by group, then merge groups into final answer.

Supports group-aware aggregation: AQs with the same ``group`` label are aggregated
together first, then groups are merged into a final Markdown answer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SubQueryResult:
    query: str
    retained_chunks: list          # list[RetrievedChunk]
    retained_info: str
    gap_analysis: str = ""         # what's still missing (empty = complete)
    answer: str | None = None      # None when generate_answer=False
    task: str = ""                  # decompose task label
    task_query: str = ""            # complete task description


# ── Prompts ────────────────────────────────────────────────────────────────

_AGGREGATE_GROUP_SYSTEM = """You are a research assistant synthesizing information from multiple searches.

Given a task description, multiple sub-queries, their findings (retained_info), and relevant
context chunks, produce a comprehensive answer to the task.

Rules:
1. Answer the TASK, not each sub-query individually — synthesize across all sub-queries.
2. Preserve ALL specific data points (numbers, dates, names) from the context and retained info.
3. If a note indicates some sub-queries returned incomplete data, clearly mark which parts of the answer are uncertain.
4. Use clear Markdown formatting with headers and bullet points where helpful.
5. Do NOT fabricate information — only use what is provided.
6. If all sub-queries returned no useful information, state that clearly."""

_AGGREGATE_GROUP_USER = """The user asked: {original_query}
This was broken down into one or more tasks. The task you need to answer is: {task_query}
Below are the sub-queries run against the knowledge base to gather information for this task, and what they found.

{sub_results}

Your goal is to answer this task: {task_query}

Using the sub-query findings and context above, write a complete, well-structured answer.
- Synthesize across all sub-queries into one coherent response.
- Include specific data points (numbers, names, dates) where relevant.
- Use Markdown formatting with headers, bullet points, and tables where helpful.
- If any sub-query returned incomplete data, clearly mark those parts as uncertain.
- Do NOT fabricate information not present in the context or retained info."""

class Aggregator:
    """Group-aware aggregation: within-group first, then cross-group merge."""

    def __init__(self, llm):
        self.llm = llm

    def aggregate(
        self,
        results: list[SubQueryResult],
        *,
        original_query: str = "",
    ) -> str:
        """Merge all *results* into a single Markdown answer.

        Results with the same ``group`` are aggregated within-group first.
        Ungrouped results (group="") are treated as one flat group.
        Multiple groups are then merged via a final LLM pass.
        """
        logger.info("[Aggregate] %d results, groups=%s",
                    len(results), {r.task or "(single)" for r in results})

        if not results:
            return ""

        # Partition results by group
        by_group: dict[str, list[SubQueryResult]] = {}
        for r in results:
            g = r.task or ""  # "" is the default ungrouped bucket
            by_group.setdefault(g, []).append(r)

        # Aggregate within each group
        group_answers: dict[str, str] = {}
        for g_label, grp_results in by_group.items():
            if not any(r.retained_info or r.retained_chunks for r in grp_results):
                ans = "No relevant information found."
            else:
                ans = self._aggregate_group(g_label, grp_results, original_query=original_query)
            group_answers[g_label] = ans

        # If only one group, return directly
        if len(group_answers) == 1:
            return list(group_answers.values())[0]

        # Merge multiple groups
        return self._merge_groups(group_answers, original_query=original_query)

    # ── private ──────────────────────────────────────────────────────────

    def build_prompt(self, results: list[SubQueryResult], *,
                     original_query: str = "") -> str:
        """Build the aggregate prompt from AQ results without calling LLM."""
        return self._build_aggregate_prompt(results, original_query=original_query)

    def _build_aggregate_prompt(self, results: list[SubQueryResult],
                                original_query: str = "") -> str:
        """Assemble the full aggregate user prompt.

        Deduplicates chunks across all AQs before building a single context,
        then shows each AQ with just a short summary (query + retained_info).
        """
        from src.rag.context_builder import build_context

        task_query = results[0].task_query if results else ""
        task_label = task_query or "the user's query"

        # ── Dedup chunks across all AQs, build single context ──────
        seen: set[str] = set()
        all_chunks: list = []
        for r in results:
            for c in (r.retained_chunks or []):
                cid = c.metadata.get("id", "") if hasattr(c, "metadata") else ""
                if cid and cid not in seen:
                    seen.add(cid)
                    all_chunks.append(c)
        all_chunks.sort(key=lambda c: c.score, reverse=True)

        context_text = build_context(all_chunks) if all_chunks else "(no relevant chunks found)"

        # ── Sub-query summaries (short — just query + key findings) ─
        has_incomplete = False
        sub_parts = []
        for i, r in enumerate(results):
            if r.gap_analysis:
                has_incomplete = True
            info_text = r.retained_info or "(no information found)"
            gap_xml = f'\n  <gaps>{r.gap_analysis}</gaps>' if r.gap_analysis else ""
            sub_parts.append(
                f'  <sub_query index="{i + 1}">\n'
                f"    <query>{r.query}</query>\n"
                f"    <findings>{info_text}</findings>{gap_xml}\n"
                f"  </sub_query>"
            )

        gap_note = (
            "\n<!-- Some sub-queries have gaps. Clearly mark uncertain parts in your answer. -->"
            if has_incomplete else ""
        )

        # ── Assemble as XML ────────────────────────────────────────
        return (
            f'<task description="{task_label}">\n'
            f"{chr(10).join(sub_parts)}\n"
            f"</task>\n"
            f"{gap_note}\n"
            f"<context>\n{context_text}\n</context>"
        )

    def _aggregate_group(self, label: str, results: list[SubQueryResult],
                         original_query: str = "") -> str:
        """LLM-aggregate AQ results within a single task."""
        prompt = self._build_aggregate_prompt(results, original_query=original_query)

        try:
            answer = self.llm.generate(prompt, system=_AGGREGATE_GROUP_SYSTEM, max_tokens=16384, thinking=True).strip()
            logger.info("[Aggregate] task %r: %d AQs → %d chars",
                        label, len(results), len(answer))
            return answer
        except Exception as e:
            logger.warning("[Aggregate] task %r LLM failed: %s, fallback", label, e)
            return self._fallback_group(results)

    def _merge_groups(self, group_answers: dict[str, str], original_query: str = "") -> str:
        """Concatenate multiple task answers with section headers. No LLM call."""
        labels = list(group_answers.keys())
        if len(labels) == 1:
            return group_answers[labels[0]]

        parts = []
        for label, answer in group_answers.items():
            header = label if label else "Results"
            parts.append(f"## {header}\n\n{answer}")

        merged = "\n\n---\n\n".join(parts)
        logger.info("[Aggregate] merge %d groups → %d chars (concat, no LLM)", len(group_answers), len(merged))
        return merged

    # ── fallbacks ────────────────────────────────────────────────────────

    @staticmethod
    def _fallback_group(results: list[SubQueryResult]) -> str:
        parts = []
        for i, r in enumerate(results):
            answer = r.answer or "(no answer)"
            status = "✓" if not r.gap_analysis else "⚠"
            parts.append(f"**Query {i + 1}** [{status}]: {r.query}\n{answer}")
        return "\n\n".join(parts) if parts else "No information found"

