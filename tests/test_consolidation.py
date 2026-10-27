"""Consolidation Handler Tests -- TDD red phase.

Tests for format_doc_summaries_for_prompt, parse_consolidation_response,
and consolidate_handler.

Run: rtk pytest tests/test_consolidation.py -x -v
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from src.tasks.task_manager import Task, TaskStatus


# ── Helpers ──────────────────────────────────────────────────


def _run_async(coro):
    """_run_async() closes the main-thread loop and poisons later sync tests
    (asyncio.get_event_loop raises). Manage our own loop instead."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


def _make_mock_db(vector_size: int = 128):
    """Create a mock QdrantManager with common defaults."""
    mock_db = MagicMock()
    mock_db.collection_exists.return_value = False
    mock_db.scroll_points.return_value = ([], None)
    mock_db.get_collection_config.return_value = {"enriching_llm_provider": None}
    return mock_db


def _make_summary_manager(db=None, vector_size: int = 128):
    """Create a SummaryManager with a mock db injected."""
    from src.rag.summary_manager import SummaryManager

    if db is None:
        db = _make_mock_db(vector_size)
    sm = SummaryManager(db=db, vector_size=vector_size)
    return sm, db


def _make_task(collection: str = "test-col") -> Task:
    """Create a minimal Task for testing."""
    return Task(
        id="test-task-id",
        filename="test.pdf",
        collection=collection,
        status=TaskStatus.PROCESSING,
    )


# ── format_doc_summaries_for_prompt ─────────────────────────


class TestFormatDocSummariesForPrompt:
    """Tests for format_doc_summaries_for_prompt()."""

    def test_single_summary(self):
        from src.tasks.handlers import format_doc_summaries_for_prompt

        summaries = [
            {
                "source": "doc1.pdf",
                "data": ["Revenue was $4.2M"],
                "facts": ["Company was founded in 2019"],
                "insights": ["Likely to expand into Asia"],
            }
        ]
        result = format_doc_summaries_for_prompt(summaries)

        assert "doc1.pdf" in result
        assert "Revenue was $4.2M" in result
        assert "Company was founded in 2019" in result
        assert "Likely to expand into Asia" in result

    def test_multiple_summaries(self):
        from src.tasks.handlers import format_doc_summaries_for_prompt

        summaries = [
            {
                "source": "a.pdf",
                "data": ["data A"],
                "facts": ["fact A"],
                "insights": [],
            },
            {
                "source": "b.pdf",
                "data": ["data B"],
                "facts": [],
                "insights": ["insight B"],
            },
        ]
        result = format_doc_summaries_for_prompt(summaries)

        assert "a.pdf" in result
        assert "b.pdf" in result
        assert "data A" in result
        assert "data B" in result

    def test_empty_data_fields(self):
        from src.tasks.handlers import format_doc_summaries_for_prompt

        summaries = [
            {
                "source": "empty.pdf",
                "data": [],
                "facts": [],
                "insights": [],
            }
        ]
        result = format_doc_summaries_for_prompt(summaries)

        assert "empty.pdf" in result
        # Should still be a valid string, not crash
        assert len(result) > 0

    def test_empty_list(self):
        from src.tasks.handlers import format_doc_summaries_for_prompt

        result = format_doc_summaries_for_prompt([])
        assert result == ""

    def test_includes_source_filename(self):
        from src.tasks.handlers import format_doc_summaries_for_prompt

        summaries = [
            {
                "source": "financial_report_2024.xlsx",
                "data": ["Q1 revenue: $1M"],
                "facts": [],
                "insights": [],
            }
        ]
        result = format_doc_summaries_for_prompt(summaries)

        # Source should appear clearly identifiable
        assert "financial_report_2024.xlsx" in result


# ── parse_consolidation_response ────────────────────────────


class TestParseConsolidationResponse:
    """Tests for parse_consolidation_response()."""

    def test_summary_and_conflicts(self):
        from src.tasks.handlers import parse_consolidation_response

        raw = (
            "===SUMMARY===\n"
            "This project is a web application built with React and Python.\n"
            "It handles user authentication and data processing.\n"
            "\n"
            "===CONFLICTS===\n"
            "- Revenue is $4.2M | financial_report.pdf | Revenue is $3.8M | quarterly_update.pdf\n"
            "- Team size is 50 people | overview.pdf | Team size is 45 people | hr_report.pdf\n"
        )

        summary, conflicts = parse_consolidation_response(raw)

        assert "web application" in summary
        assert "React" in summary
        assert len(conflicts) == 2
        assert conflicts[0]["content1"] == "Revenue is $4.2M"
        assert conflicts[0]["source1"] == "financial_report.pdf"
        assert conflicts[0]["content2"] == "Revenue is $3.8M"
        assert conflicts[0]["source2"] == "quarterly_update.pdf"
        assert conflicts[1]["content1"] == "Team size is 50 people"
        assert conflicts[1]["source1"] == "overview.pdf"

    def test_no_conflicts(self):
        from src.tasks.handlers import parse_consolidation_response

        raw = (
            "===SUMMARY===\n"
            "A unified project overview.\n"
            "\n"
            "===CONFLICTS===\n"
            "None identified"
        )

        summary, conflicts = parse_consolidation_response(raw)

        assert "unified project overview" in summary
        assert conflicts == []

    def test_empty_input(self):
        from src.tasks.handlers import parse_consolidation_response

        summary, conflicts = parse_consolidation_response("")

        assert summary == ""
        assert conflicts == []

    def test_whitespace_only_input(self):
        from src.tasks.handlers import parse_consolidation_response

        summary, conflicts = parse_consolidation_response("   \n\n  ")

        assert summary == ""
        assert conflicts == []

    def test_missing_conflicts_section(self):
        from src.tasks.handlers import parse_consolidation_response

        raw = "===SUMMARY===\nSome summary text\n"

        summary, conflicts = parse_consolidation_response(raw)

        assert summary == "Some summary text"
        assert conflicts == []

    def test_missing_summary_section(self):
        from src.tasks.handlers import parse_consolidation_response

        raw = (
            "===CONFLICTS===\n"
            "- Point A | doc1.pdf | Point B | doc2.pdf\n"
        )

        summary, conflicts = parse_consolidation_response(raw)

        assert summary == ""
        assert len(conflicts) == 1

    def test_single_conflict(self):
        from src.tasks.handlers import parse_consolidation_response

        raw = (
            "===SUMMARY===\n"
            "Overview text.\n"
            "\n"
            "===CONFLICTS===\n"
            "- Price is $100 | sales.pdf | Price is $120 | pricing.pdf\n"
        )

        summary, conflicts = parse_consolidation_response(raw)

        assert summary == "Overview text."
        assert len(conflicts) == 1
        assert conflicts[0]["content1"] == "Price is $100"
        assert conflicts[0]["source1"] == "sales.pdf"
        assert conflicts[0]["content2"] == "Price is $120"
        assert conflicts[0]["source2"] == "pricing.pdf"

    def test_none_identified_case_insensitive(self):
        from src.tasks.handlers import parse_consolidation_response

        raw = (
            "===SUMMARY===\n"
            "Summary.\n"
            "\n"
            "===CONFLICTS===\n"
            "none identified"
        )

        summary, conflicts = parse_consolidation_response(raw)

        assert summary == "Summary."
        assert conflicts == []


# ── consolidate_handler ─────────────────────────────────────


class TestConsolidateHandler:
    """Tests for consolidate_handler() with mocked dependencies."""

    @staticmethod
    def _patch_definitive_filter(doc_summaries):
        """Patch pick_current + source_is_definitive so handler uses given rows."""
        return (
            patch(
                "src.api.routes.info.pick_current_doc_summaries",
                side_effect=lambda _col, summaries: list(summaries),
            ),
            patch(
                "src.api.routes.info.source_is_definitive",
                return_value=True,
            ),
            patch("src.api.routes.info.clear_debounce"),
        )

    @pytest.mark.asyncio
    async def test_no_doc_summaries_returns_early(self):
        """If no definitive docs remain, clear previous consolidate results."""
        from src.tasks.handlers import consolidate_handler

        task = _make_task()
        p_pick, p_def, p_clear = self._patch_definitive_filter([])

        with patch("src.tasks.handlers.SummaryManager") as MockSM, \
             patch("src.tasks.handlers.services") as mock_services, \
             p_pick, p_def, p_clear:
            mock_sm_instance = MockSM.return_value
            mock_sm_instance.get_doc_summaries.return_value = []
            mock_services.db.update_collection_config = MagicMock()

            result = await consolidate_handler(task, collection="test-col")

            assert "No documents" in result["message"]
            mock_sm_instance.delete_collection_summary.assert_called_once_with("test-col")
            mock_sm_instance.delete_project_description.assert_called_once_with("test-col")
            mock_sm_instance.delete_conflicts.assert_called_once_with("test-col")
            mock_services.db.update_collection_config.assert_called_once_with(
                "test-col", {"summary_change_counter": 0}
            )

    @pytest.mark.asyncio
    async def test_full_flow_with_mocks(self):
        """Full consolidation flow with all dependencies mocked."""
        from src.tasks.handlers import consolidate_handler

        task = _make_task()
        doc_summaries = [
            {
                "source": "a.pdf",
                "data": ["data A"],
                "facts": ["fact A"],
                "insights": ["insight A"],
            },
            {
                "source": "b.pdf",
                "data": ["data B"],
                "facts": ["fact B"],
                "insights": [],
            },
        ]
        llm_response = (
            "===SUMMARY===\n"
            "Unified project summary combining A and B.\n"
            "\n"
            "===CONFLICTS===\n"
            "None identified"
        )
        p_pick, p_def, p_clear = self._patch_definitive_filter(doc_summaries)

        with patch("src.tasks.handlers.SummaryManager") as MockSM, \
             patch("src.tasks.handlers.services") as mock_services, \
             patch("src.tasks.handlers._get_enriching_llm") as mock_get_llm, \
             patch("src.tasks.handlers.get_collection_embedding") as mock_get_emb, \
             p_pick, p_def, p_clear:

            mock_sm_instance = MockSM.return_value
            mock_sm_instance.get_doc_summaries.return_value = doc_summaries
            mock_sm_instance.store_collection_summary = MagicMock()
            mock_sm_instance.store_conflicts = MagicMock()

            mock_services.db.get_collection_config.return_value = {"enriching_llm_provider": None}
            mock_services.db.update_collection_config = MagicMock()
            mock_services.embedding = MagicMock()
            mock_services.embedding.dimensions = 128

            mock_llm = MagicMock()
            mock_llm.generate.return_value = llm_response
            mock_get_llm.return_value = mock_llm

            mock_embedding = MagicMock()
            mock_embedding.embed_texts.return_value = [[0.1] * 128]
            mock_get_emb.return_value = mock_embedding

            result = await consolidate_handler(task, collection="test-col")

            assert result["message"] == "Consolidation done"
            assert result["conflicts_count"] == 0

            # Should delete old data first
            mock_sm_instance.delete_collection_summary.assert_called_once_with("test-col")
            mock_sm_instance.delete_conflicts.assert_called_once_with("test-col")

            # Should read doc summaries
            mock_sm_instance.get_doc_summaries.assert_called_once_with(
                "test-col", included_only=False
            )

            # Should call LLM (at least once — may also generate project description)
            assert mock_llm.generate.call_count >= 1
            call_arg = mock_llm.generate.call_args_list[0][0][0]
            assert "data A" in call_arg or "fact A" in call_arg

            # Should store new summary and conflicts
            mock_sm_instance.store_collection_summary.assert_called_once()
            mock_sm_instance.store_conflicts.assert_called_once()

            # Should reset counter
            mock_services.db.update_collection_config.assert_called_once_with(
                "test-col", {"summary_change_counter": 0}
            )

    @pytest.mark.asyncio
    async def test_full_flow_with_conflicts(self):
        """Consolidation that produces conflicts."""
        from src.tasks.handlers import consolidate_handler

        task = _make_task()
        doc_summaries = [
            {"source": "a.pdf", "data": ["$4.2M"], "facts": [], "insights": []},
            {"source": "b.pdf", "data": ["$3.8M"], "facts": [], "insights": []},
        ]
        llm_response = (
            "===SUMMARY===\n"
            "Project with conflicting revenue figures.\n"
            "\n"
            "===CONFLICTS===\n"
            "- Revenue $4.2M | a.pdf | Revenue $3.8M | b.pdf\n"
        )
        p_pick, p_def, p_clear = self._patch_definitive_filter(doc_summaries)

        with patch("src.tasks.handlers.SummaryManager") as MockSM, \
             patch("src.tasks.handlers.services") as mock_services, \
             patch("src.tasks.handlers._get_enriching_llm") as mock_get_llm, \
             patch("src.tasks.handlers.get_collection_embedding") as mock_get_emb, \
             p_pick, p_def, p_clear:

            mock_sm_instance = MockSM.return_value
            mock_sm_instance.get_doc_summaries.return_value = doc_summaries

            mock_services.db.get_collection_config.return_value = {}
            mock_services.db.update_collection_config = MagicMock()
            mock_services.embedding = MagicMock()
            mock_services.embedding.dimensions = 128

            mock_llm = MagicMock()
            mock_llm.generate.return_value = llm_response
            mock_get_llm.return_value = mock_llm

            mock_embedding = MagicMock()
            mock_embedding.embed_texts.return_value = [[0.1] * 128]
            mock_get_emb.return_value = mock_embedding

            result = await consolidate_handler(task, collection="test-col")

            assert result["conflicts_count"] == 1

            # Check store_conflicts was called with correct data
            call_args = mock_sm_instance.store_conflicts.call_args
            conflicts_arg = call_args[0][1]
            assert len(conflicts_arg) == 1
            assert conflicts_arg[0]["content1"] == "Revenue $4.2M"
            assert conflicts_arg[0]["source1"] == "a.pdf"

    @pytest.mark.asyncio
    async def test_llm_failure_raises(self):
        """LLM exception propagates as an error."""
        from src.tasks.handlers import consolidate_handler

        task = _make_task()
        docs = [{"source": "a.pdf", "data": ["x"], "facts": [], "insights": []}]
        p_pick, p_def, p_clear = self._patch_definitive_filter(docs)

        with patch("src.tasks.handlers.SummaryManager") as MockSM, \
             patch("src.tasks.handlers.services") as mock_services, \
             patch("src.tasks.handlers._get_enriching_llm") as mock_get_llm, \
             patch("src.tasks.handlers.get_collection_embedding") as mock_get_emb, \
             p_pick, p_def, p_clear:

            mock_sm_instance = MockSM.return_value
            mock_sm_instance.get_doc_summaries.return_value = docs

            mock_services.db.get_collection_config.return_value = {}
            mock_services.embedding = MagicMock()

            mock_llm = MagicMock()
            mock_llm.generate.side_effect = RuntimeError("LLM timeout")
            mock_get_llm.return_value = mock_llm

            with pytest.raises(RuntimeError, match="LLM timeout"):
                await consolidate_handler(task, collection="test-col")

    @pytest.mark.asyncio
    async def test_uses_enriching_llm_config(self):
        """Verifies _get_enriching_llm is called with collection config."""
        from src.tasks.handlers import consolidate_handler

        task = _make_task()
        col_config = {"enriching_llm_provider": "deepseek"}
        docs = [{"source": "a.pdf", "data": ["x"], "facts": [], "insights": []}]
        p_pick, p_def, p_clear = self._patch_definitive_filter(docs)

        with patch("src.tasks.handlers.SummaryManager") as MockSM, \
             patch("src.tasks.handlers.services") as mock_services, \
             patch("src.tasks.handlers._get_enriching_llm") as mock_get_llm, \
             patch("src.tasks.handlers.get_collection_embedding") as mock_get_emb, \
             p_pick, p_def, p_clear:

            mock_sm_instance = MockSM.return_value
            mock_sm_instance.get_doc_summaries.return_value = docs

            mock_services.db.get_collection_config.return_value = col_config
            mock_services.db.update_collection_config = MagicMock()
            mock_services.embedding = MagicMock()
            mock_services.embedding.dimensions = 128

            mock_llm = MagicMock()
            mock_llm.generate.return_value = "===SUMMARY===\nTest\n===CONFLICTS===\nNone identified"
            mock_get_llm.return_value = mock_llm

            mock_embedding = MagicMock()
            mock_embedding.embed_texts.return_value = [[0.1] * 128]
            mock_get_emb.return_value = mock_embedding

            await consolidate_handler(task, collection="test-col")

            mock_get_llm.assert_called_once_with(col_config)

    @pytest.mark.asyncio
    async def test_embeds_collection_summary(self):
        """Verifies the collection summary text is stored (embedding optional)."""
        from src.tasks.handlers import consolidate_handler

        task = _make_task()
        docs = [{"source": "a.pdf", "data": ["x"], "facts": [], "insights": []}]
        p_pick, p_def, p_clear = self._patch_definitive_filter(docs)

        with patch("src.tasks.handlers.SummaryManager") as MockSM, \
             patch("src.tasks.handlers.services") as mock_services, \
             patch("src.tasks.handlers._get_enriching_llm") as mock_get_llm, \
             patch("src.tasks.handlers.get_collection_embedding") as mock_get_emb, \
             p_pick, p_def, p_clear:

            mock_sm_instance = MockSM.return_value
            mock_sm_instance.get_doc_summaries.return_value = docs

            mock_services.db.get_collection_config.return_value = {}
            mock_services.db.update_collection_config = MagicMock()
            mock_services.embedding = MagicMock()

            summary_text = "This is the unified project summary."
            llm_response = f"===SUMMARY===\n{summary_text}\n\n===CONFLICTS===\nNone identified"
            mock_llm = MagicMock()
            mock_llm.generate.return_value = llm_response
            mock_get_llm.return_value = mock_llm

            mock_embedding = MagicMock()
            mock_embedding.embed_texts.return_value = [[0.5] * 128]
            mock_get_emb.return_value = mock_embedding

            await consolidate_handler(task, collection="test-col")

            # store_collection_summary should receive the summary text
            store_call = mock_sm_instance.store_collection_summary.call_args
            assert store_call[0][1] == summary_text  # content


# ── consolidate determinism ─────────────────────────────────


class TestConsolidateDeterminism:
    """Consolidate output should be reproducible across runs.

    Two stabilizers:
    - every LLM call runs at temperature 0 (no sampling jitter)
    - doc summaries are sorted by source before prompt formatting, so the
      prompt text — and alias assignment (FILE_A, FILE_B, …) — does not
      shuffle with Qdrant scroll order between runs
    """

    def test_doc_summaries_sorted_by_source_before_prompt(self):
        """Handler feeds the formatter a source-sorted summary list."""
        import src.tasks.handlers as handlers_mod
        from src.tasks.handlers import consolidate_handler

        task = _make_task()
        doc_summaries = [
            {"source": "__file__:m", "data": ["m data"], "facts": [], "insights": []},
            {"source": "__file__:a", "data": ["a data"], "facts": [], "insights": []},
            {"source": "__file__:z", "data": ["z data"], "facts": [], "insights": []},
        ]
        p_pick, p_def, p_clear = TestConsolidateHandler._patch_definitive_filter(
            doc_summaries
        )

        with patch("src.tasks.handlers.SummaryManager") as MockSM, \
             patch("src.tasks.handlers.services") as mock_services, \
             patch("src.tasks.handlers._get_enriching_llm") as mock_get_llm, \
             patch("src.tasks.handlers.get_collection_embedding") as mock_get_emb, \
             patch.object(
                 handlers_mod,
                 "format_doc_summaries_for_prompt",
                 return_value="FORMATTED",
             ) as mock_fmt, \
             p_pick, p_def, p_clear:

            mock_sm_instance = MockSM.return_value
            mock_sm_instance.get_doc_summaries.return_value = doc_summaries
            mock_services.db.get_collection_config.return_value = {}
            mock_services.db.update_collection_config = MagicMock()
            mock_services.embedding = MagicMock()
            mock_services.embedding.dimensions = 128

            mock_llm = MagicMock()
            mock_llm.generate.return_value = (
                "===SUMMARY===\nS\n===CONFLICTS===\nNone identified"
            )
            mock_get_llm.return_value = mock_llm

            mock_embedding = MagicMock()
            mock_embedding.embed_texts.return_value = [[0.1] * 128]
            mock_get_emb.return_value = mock_embedding

            _run_async(consolidate_handler(task, collection="test-col"))

            assert mock_fmt.call_count >= 1
            received = mock_fmt.call_args[0][0]
            assert [s["source"] for s in received] == ["__file__:a", "__file__:m", "__file__:z"]

            # Alias assignment is stable: sorted sources map to FILE_A/B/C in order.
            alias_arg = mock_fmt.call_args[1]["alias_map"]
            assert list(alias_arg.keys()) == ["__file__:a", "__file__:m", "__file__:z"]
            assert list(alias_arg.values()) == ["FILE_A", "FILE_B", "FILE_C"]

    def test_llm_calls_run_at_temperature_zero(self):
        """Both the consolidation and project-description calls pin temperature 0."""
        from src.tasks.handlers import consolidate_handler

        task = _make_task()
        doc_summaries = [
            {"source": "a.pdf", "data": ["x"], "facts": [], "insights": []},
        ]
        p_pick, p_def, p_clear = TestConsolidateHandler._patch_definitive_filter(
            doc_summaries
        )

        with patch("src.tasks.handlers.SummaryManager") as MockSM, \
             patch("src.tasks.handlers.services") as mock_services, \
             patch("src.tasks.handlers._get_enriching_llm") as mock_get_llm, \
             patch("src.tasks.handlers.get_collection_embedding") as mock_get_emb, \
             p_pick, p_def, p_clear:

            mock_sm_instance = MockSM.return_value
            mock_sm_instance.get_doc_summaries.return_value = doc_summaries
            mock_services.db.get_collection_config.return_value = {}
            mock_services.db.update_collection_config = MagicMock()
            mock_services.embedding = MagicMock()
            mock_services.embedding.dimensions = 128

            mock_llm = MagicMock()
            mock_llm.generate.return_value = (
                "===SUMMARY===\nS\n===CONFLICTS===\nNone identified"
            )
            mock_get_llm.return_value = mock_llm

            mock_embedding = MagicMock()
            mock_embedding.embed_texts.return_value = [[0.1] * 128]
            mock_get_emb.return_value = mock_embedding

            _run_async(consolidate_handler(task, collection="test-col"))

            # Consolidation + project description — every call must pin temp 0.
            assert mock_llm.generate.call_count >= 2
            for call in mock_llm.generate.call_args_list:
                assert call.kwargs.get("temperature") == 0.0


# ── parse: claim-based conflict schema ──────────────────────


class TestParseClaimSchema:
    """New consolidated conflict schema: claim cards + rubric + quote checks.

    The LLM returns each conflict as two decomposed claim cards plus a
    dimension-by-dimension comparison. The parser must:
      - resolve claim sources through the alias map into real sources
      - keep top-level content1/source1/content2/source2 for storage/UI compat
      - drop pairs whose comparison rubric fails (any dimension false)
      - keep legacy pairs without a comparison block (rubric skipped)
      - verify each quote against the aliased source's summary text:
        re-attribute on unique match elsewhere, drop the pair when the
        quote matches nothing (fabricated pairing)
    """

    ALIAS_MAP = {
        "FILE_A": "__file__:aaa",
        "FILE_B": "__file__:bbb",
        "FILE_C": "__file__:ccc",
    }

    SUMMARIES = [
        {
            "source": "__file__:aaa",
            "data": [],
            "facts": ["Company X reported 2021E revenue of RMB 420m"],
            "insights": [],
        },
        {
            "source": "__file__:bbb",
            "data": [],
            "facts": ["Registered capital is RMB 90m per the articles"],
            "insights": [],
        },
        {
            "source": "__file__:ccc",
            "data": [],
            "facts": ["Registered capital is RMB 90m per the amended articles of association"],
            "insights": [],
        },
    ]

    def _claim(self, source, quote, aspect="registered capital"):
        return {
            "source": source,
            "quote": quote,
            "subject": "Company X",
            "aspect": aspect,
            "value": {"normalized": 90, "unit": "RMB million", "text": "RMB 90m"},
            "modality": "actual",
            "time_basis": "FY2021",
        }

    def _all_true(self):
        return {
            "same_subject": True,
            "same_aspect": True,
            "same_time_basis": True,
            "scope_comparable": True,
            "modality_comparable": True,
            "mutually_exclusive": True,
            "reason": "Registered capital cannot be both RMB 120m and RMB 90m.",
        }

    def _raw(self, comparison=None, claim_b_source="FILE_B", claim_b_quote=None):
        claim_a = self._claim("FILE_A", "Company X reported 2021E revenue of RMB 420m")
        claim_b = self._claim(
            claim_b_source,
            claim_b_quote or "Registered capital is RMB 90m per the articles",
        )
        conflict = {"claim_a": claim_a, "claim_b": claim_b}
        if comparison is not None:
            conflict["comparison"] = comparison
        import json as _json

        return _json.dumps(
            {
                "summary": "A project about Company X.",
                "conflicts": [conflict],
            }
        )

    def test_parses_claim_schema_and_resolves_sources(self):
        from src.tasks.handlers import parse_consolidation_response

        summary, conflicts = parse_consolidation_response(
            self._raw(comparison=self._all_true()),
            alias_map=self.ALIAS_MAP,
            summaries=self.SUMMARIES,
        )
        assert summary == "A project about Company X."
        assert len(conflicts) == 1
        c = conflicts[0]
        assert c["content1"] == "Company X reported 2021E revenue of RMB 420m"
        assert c["source1"] == "__file__:aaa"
        assert c["source2"] == "__file__:bbb"
        assert c["claim_a"]["source"] == "__file__:aaa"
        assert c["comparison"]["mutually_exclusive"] is True

    def test_rubric_drops_pair_on_false_dimension(self):
        from src.tasks.handlers import parse_consolidation_response

        comparison = self._all_true()
        comparison["same_aspect"] = False  # market size vs revenue shape
        _, conflicts = parse_consolidation_response(
            self._raw(comparison=comparison),
            alias_map=self.ALIAS_MAP,
            summaries=self.SUMMARIES,
        )
        assert conflicts == []

    def test_rubric_treats_string_booleans(self):
        from src.tasks.handlers import parse_consolidation_response

        comparison = self._all_true()
        comparison["mutually_exclusive"] = "false"  # LLM string booleans
        _, conflicts = parse_consolidation_response(
            self._raw(comparison=comparison),
            alias_map=self.ALIAS_MAP,
            summaries=self.SUMMARIES,
        )
        assert conflicts == []

    def test_legacy_pair_without_comparison_is_kept(self):
        from src.tasks.handlers import parse_consolidation_response

        import json as _json

        raw = _json.dumps(
            {
                "summary": "S",
                "conflicts": [
                    {
                        "content1": "Claim one",
                        "source1": "FILE_A",
                        "content2": "Claim two",
                        "source2": "FILE_B",
                    }
                ],
            }
        )
        _, conflicts = parse_consolidation_response(
            raw, alias_map=self.ALIAS_MAP, summaries=self.SUMMARIES
        )
        assert len(conflicts) == 1
        assert conflicts[0]["source1"] == "__file__:aaa"

    def test_quote_misattributed_is_reassigned_to_right_source(self):
        from src.tasks.handlers import parse_consolidation_response

        # claim_b claims FILE_B but its quote only exists in FILE_C's summary
        _, conflicts = parse_consolidation_response(
            self._raw(
                comparison=self._all_true(),
                claim_b_source="FILE_B",
                claim_b_quote=(
                    "Registered capital is RMB 90m per the amended "
                    "articles of association"
                ),
            ),
            alias_map=self.ALIAS_MAP,
            summaries=self.SUMMARIES,
        )
        assert len(conflicts) == 1
        assert conflicts[0]["source2"] == "__file__:ccc"

    def test_quote_matching_nothing_drops_pair(self):
        from src.tasks.handlers import parse_consolidation_response

        _, conflicts = parse_consolidation_response(
            self._raw(
                comparison=self._all_true(),
                claim_b_source="FILE_B",
                claim_b_quote="A claim that appears in no summary at all",
            ),
            alias_map=self.ALIAS_MAP,
            summaries=self.SUMMARIES,
        )
        assert conflicts == []


# ── conflict judge (second-pass verification) ───────────────


class TestConflictJudge:
    """Second-pass judge: verifies each surviving candidate pair with a
    narrow one-pair-one-call LLM verdict.

    Contract:
    - verdict "conflict" → pair kept with judge verdict + reason attached
    - verdict "not_conflict" → pair dropped
    - invalid JSON / call errors → retried, then fail-open: pair kept with
      judge marked "skipped"
    - hard cap on judged pairs; beyond the cap pairs are kept unverified
    - one llm.generate per pair, temperature pinned to 0, no thinking
    """

    def _candidate(self, idx: int = 1) -> dict:
        return {
            "content1": f"Claim A{idx} text",
            "source1": "__file__:aaa",
            "content2": f"Claim B{idx} text",
            "source2": "__file__:bbb",
            "claim_a": {
                "source": "__file__:aaa",
                "quote": f"Claim A{idx} text",
                "subject": "Company X",
                "aspect": "registered capital",
                "value": {"normalized": 120, "unit": "RMB million", "text": "RMB 120m"},
                "modality": "actual",
                "time_basis": "FY2021",
            },
            "claim_b": {
                "source": "__file__:bbb",
                "quote": f"Claim B{idx} text",
                "subject": "Company X",
                "aspect": "registered capital",
                "value": {"normalized": 90, "unit": "RMB million", "text": "RMB 90m"},
                "modality": "actual",
                "time_basis": "FY2021",
            },
            "comparison": {"mutually_exclusive": True},
        }

    def _run_judge(self, candidates, responses):
        """Run _judge_conflict_pairs with a scripted mock LLM. Returns (kept, mock_llm)."""
        import json as _json
        from src.tasks.handlers import _judge_conflict_pairs

        mock_llm = MagicMock()
        if isinstance(responses, list):
            mock_llm.generate.side_effect = responses
        else:
            mock_llm.generate.return_value = responses

        async def _run():
            return await _judge_conflict_pairs(candidates, mock_llm)

        with patch("asyncio.sleep", new=AsyncMock(return_value=None)):
            kept = _run_async(_run())
        return kept, mock_llm

    def test_verdict_conflict_kept_not_conflict_dropped(self):
        candidates = [self._candidate(1), self._candidate(2)]
        kept, mock_llm = self._run_judge(
            candidates,
            [
                '{"verdict": "conflict", "reason": "same capital, different numbers"}',
                '{"verdict": "not_conflict", "reason": "different aspects"}',
            ],
        )
        assert len(kept) == 1
        assert kept[0]["content1"] == "Claim A1 text"
        assert kept[0]["judge"]["verdict"] == "conflict"
        assert kept[0]["judge"]["reason"] == "same capital, different numbers"

    def test_fail_open_after_exhausted_retries(self):
        candidates = [self._candidate(1)]
        kept, mock_llm = self._run_judge(candidates, "this is not json at all")
        assert len(kept) == 1
        assert kept[0]["judge"]["verdict"] == "skipped"
        assert mock_llm.generate.call_count == 3  # 1 + 2 retries

    def test_cap_limits_calls_and_keeps_extras_unverified(self):
        candidates = [self._candidate(i) for i in range(35)]
        kept, mock_llm = self._run_judge(
            candidates, '{"verdict": "conflict", "reason": "ok"}'
        )
        assert mock_llm.generate.call_count == 30
        assert len(kept) == 35
        assert all(c["judge"]["verdict"] == "conflict" for c in kept[:30])
        assert all(c["judge"]["verdict"] == "skipped" for c in kept[30:])

    def test_generate_kwargs_pin_temperature_and_no_thinking(self):
        candidates = [self._candidate(1)]
        kept, mock_llm = self._run_judge(
            candidates, '{"verdict": "conflict", "reason": "ok"}'
        )
        kwargs = mock_llm.generate.call_args.kwargs
        assert kwargs.get("temperature") == 0.0
        assert kwargs.get("thinking") is False

    def test_prompt_contains_both_claim_cards(self):
        candidates = [self._candidate(1)]
        kept, mock_llm = self._run_judge(
            candidates, '{"verdict": "conflict", "reason": "ok"}'
        )
        prompt = mock_llm.generate.call_args.args[0]
        assert "registered capital" in prompt
        assert "RMB 120m" in prompt
        assert "RMB 90m" in prompt
        assert "Claim A1 text" in prompt
        assert "Claim B1 text" in prompt

    def test_legacy_pair_without_cards_still_gets_judged(self):
        legacy = {
            "content1": "Revenue was RMB 240m",
            "source1": "__file__:aaa",
            "content2": "Revenue was RMB 420m",
            "source2": "__file__:bbb",
        }
        kept, mock_llm = self._run_judge(
            [legacy], '{"verdict": "conflict", "reason": "same year revenue"}'
        )
        assert len(kept) == 1
        prompt = mock_llm.generate.call_args.args[0]
        assert "RMB 240m" in prompt and "RMB 420m" in prompt

    def test_full_flow_judge_filters_before_store(self):
        """consolidate_handler: judge drops one of two candidates pre-store."""
        from src.tasks.handlers import consolidate_handler

        task = _make_task()
        doc_summaries = [
            {
                "source": "__file__:aaa",
                "data": [],
                "facts": ["fact a one", "fact a two"],
                "insights": [],
            },
            {
                "source": "__file__:bbb",
                "data": [],
                "facts": ["fact b one", "fact b two"],
                "insights": [],
            },
        ]

        def _claim(idx, source, quote):
            return {
                "source": source,
                "quote": quote,
                "subject": "Company X",
                "aspect": "registered capital",
                "value": {"normalized": 90, "unit": "RMB million", "text": "RMB 90m"},
                "modality": "actual",
                "time_basis": "FY2021",
            }

        all_true = {
            "same_subject": True,
            "same_aspect": True,
            "same_time_basis": True,
            "scope_comparable": True,
            "modality_comparable": True,
            "mutually_exclusive": True,
            "reason": "cannot both hold",
        }
        consolidation_json = json.dumps(
            {
                "summary": "Project summary.",
                "conflicts": [
                    {
                        "claim_a": _claim(1, "FILE_A", "fact a one"),
                        "claim_b": _claim(2, "FILE_B", "fact b one"),
                        "comparison": all_true,
                    },
                    {
                        "claim_a": _claim(1, "FILE_A", "fact a two"),
                        "claim_b": _claim(2, "FILE_B", "fact b two"),
                        "comparison": all_true,
                    },
                ],
            }
        )
        p_pick, p_def, p_clear = TestConsolidateHandler._patch_definitive_filter(
            doc_summaries
        )

        with patch("src.tasks.handlers.SummaryManager") as MockSM,              patch("src.tasks.handlers.services") as mock_services,              patch("src.tasks.handlers._get_enriching_llm") as mock_get_llm,              patch("src.tasks.handlers.get_collection_embedding") as mock_get_emb,              patch("asyncio.sleep", new=AsyncMock(return_value=None)),              p_pick, p_def, p_clear:

            mock_sm_instance = MockSM.return_value
            mock_sm_instance.get_doc_summaries.return_value = doc_summaries
            mock_services.db.get_collection_config.return_value = {}
            mock_services.db.update_collection_config = MagicMock()
            mock_services.embedding = MagicMock()
            mock_services.embedding.dimensions = 128

            mock_llm = MagicMock()
            mock_llm.generate.side_effect = [
                consolidation_json,                      # consolidation call
                "A one-line project description.",        # project description call
                '{"verdict": "conflict", "reason": "keep"}',
                '{"verdict": "not_conflict", "reason": "drop"}',
            ]
            mock_get_llm.return_value = mock_llm

            mock_embedding = MagicMock()
            mock_embedding.embed_texts.return_value = [[0.1] * 128]
            mock_get_emb.return_value = mock_embedding

            result = _run_async(consolidate_handler(task, collection="test-col"))

            assert result["conflicts_count"] == 1
            stored = mock_sm_instance.store_conflicts.call_args[0][1]
            assert len(stored) == 1
            assert stored[0]["judge"]["verdict"] == "conflict"


# ── CONSOLIDATION_PROMPT ────────────────────────────────────


class TestConsolidationPrompt:
    """Tests for the CONSOLIDATION_PROMPT template."""

    def test_has_summaries_placeholder(self):
        from src.tasks.handlers import CONSOLIDATION_PROMPT
        assert "{summaries}" in CONSOLIDATION_PROMPT

    def test_has_summary_section(self):
        from src.tasks.handlers import CONSOLIDATION_PROMPT
        assert "CONCISE PROJECT SUMMARY" in CONSOLIDATION_PROMPT

    def test_has_conflicts_section(self):
        from src.tasks.handlers import CONSOLIDATION_PROMPT
        assert "CONFLICTS" in CONSOLIDATION_PROMPT

    def test_has_conflict_format(self):
        from src.tasks.handlers import CONSOLIDATION_PROMPT
        assert '"claim_a"' in CONSOLIDATION_PROMPT
        '"claim_b"' in CONSOLIDATION_PROMPT
        assert '"comparison"' in CONSOLIDATION_PROMPT
        for dim in (
            "same_subject",
            "same_aspect",
            "same_time_basis",
            "scope_comparable",
            "modality_comparable",
            "mutually_exclusive",
        ):
            assert dim in CONSOLIDATION_PROMPT

    def test_has_quote_verbatim_instruction(self):
        from src.tasks.handlers import CONSOLIDATION_PROMPT
        assert "verbatim" in CONSOLIDATION_PROMPT

    def test_mentions_none_identified(self):
        from src.tasks.handlers import CONSOLIDATION_PROMPT
        assert '"conflicts": []' in CONSOLIDATION_PROMPT


# ── Generated-text language policy ──────────────────────────


class TestGeneratedTextLanguagePolicy:
    """File-level and collection-level generated text follows the content's
    dominant language — the instruction must stay in the prompts or the
    output language drifts back to the model default."""

    def test_ingest_system_prompt_pins_language(self):
        from src.prompts import INGEST_SYSTEM_PROMPT
        assert "dominant language" in INGEST_SYSTEM_PROMPT

    def test_standalone_summary_prompt_pins_language(self):
        from src.prompts import SUMMARY_PROMPT
        assert "dominant language" in SUMMARY_PROMPT

    def test_structured_summary_prompt_pins_language(self):
        from src.prompts import STRUCTURED_SUMMARY_PROMPT
        assert "dominant language" in STRUCTURED_SUMMARY_PROMPT

    def test_consolidation_prompt_pins_language(self):
        from src.tasks.handlers import CONSOLIDATION_PROMPT
        assert "dominant language" in CONSOLIDATION_PROMPT
