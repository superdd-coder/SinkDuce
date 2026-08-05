"""Consolidation Handler Tests -- TDD red phase.

Tests for format_doc_summaries_for_prompt, parse_consolidation_response,
and consolidate_handler.

Run: rtk pytest tests/test_consolidation.py -x -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from src.tasks.task_manager import Task, TaskStatus


# ── Helpers ──────────────────────────────────────────────────


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
        assert '"content1"' in CONSOLIDATION_PROMPT
        assert '"source1"' in CONSOLIDATION_PROMPT

    def test_mentions_none_identified(self):
        from src.tasks.handlers import CONSOLIDATION_PROMPT
        assert '"conflicts": []' in CONSOLIDATION_PROMPT
