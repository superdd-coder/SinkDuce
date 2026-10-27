from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.rag.contextual import (
    STRUCTURED_SUMMARY_PROMPT,
    _parse_structured_summary,
    generate_structured_summary,
)


# ---------------------------------------------------------------------------
# _parse_structured_summary tests
# ---------------------------------------------------------------------------

class TestParseStructuredSummary:
    """Tests for _parse_structured_summary()."""

    def test_well_formed_input(self):
        raw = (
            "===DATA===\n"
            "- Revenue was $4.2 million in Q3\n"
            "- 150 employees across 3 offices\n"
            "\n"
            "===FACTS===\n"
            "- The company was founded in 2019\n"
            "- Jane Doe is the CEO\n"
            "\n"
            "===INSIGHTS===\n"
            "- The company is likely to expand into Asia next year\n"
        )
        result = _parse_structured_summary(raw)

        assert result["data"] == [
            "Revenue was $4.2 million in Q3",
            "150 employees across 3 offices",
        ]
        assert result["facts"] == [
            "The company was founded in 2019",
            "Jane Doe is the CEO",
        ]
        assert result["insights"] == [
            "The company is likely to expand into Asia next year",
        ]

    def test_missing_sections_return_empty_lists(self):
        raw = "===DATA===\n- Only data here\n"
        result = _parse_structured_summary(raw)

        assert result["data"] == ["Only data here"]
        assert result["facts"] == []
        assert result["insights"] == []

    def test_none_identified_filtered_out(self):
        raw = (
            "===DATA===\n"
            "- Revenue was $4.2 million\n"
            "\n"
            "===FACTS===\n"
            "- None identified\n"
            "\n"
            "===INSIGHTS===\n"
            "- None identified\n"
        )
        result = _parse_structured_summary(raw)

        assert result["data"] == ["Revenue was $4.2 million"]
        assert result["facts"] == []
        assert result["insights"] == []

    def test_empty_input(self):
        result = _parse_structured_summary("")

        assert result == {"data": [], "facts": [], "insights": []}

    def test_whitespace_only_input(self):
        result = _parse_structured_summary("   \n\n  ")

        assert result == {"data": [], "facts": [], "insights": []}

    def test_no_bullet_items(self):
        raw = (
            "===DATA===\n"
            "\n"
            "===FACTS===\n"
            "Some text without bullets\n"
            "\n"
            "===INSIGHTS===\n"
        )
        result = _parse_structured_summary(raw)

        assert result == {"data": [], "facts": [], "insights": []}

    def test_items_with_extra_whitespace(self):
        raw = (
            "===DATA===\n"
            "  -  Spaced item  \n"
            "- Normal item\n"
        )
        result = _parse_structured_summary(raw)

        assert result["data"] == ["Spaced item", "Normal item"]

    def test_case_insensitive_section_headers(self):
        raw = (
            "===data===\n"
            "- Lower case data\n"
            "\n"
            "===Facts===\n"
            "- Mixed case fact\n"
        )
        result = _parse_structured_summary(raw)

        assert result["data"] == ["Lower case data"]
        assert result["facts"] == ["Mixed case fact"]


# ---------------------------------------------------------------------------
# generate_structured_summary tests
# ---------------------------------------------------------------------------

SAMPLE_LLM_RESPONSE = (
    "===DATA===\n"
    "- Revenue was $4.2 million in Q3 2025\n"
    "- The company employs 150 people\n"
    "\n"
    "===FACTS===\n"
    "- The company was founded in 2019\n"
    "- Jane Doe is the CEO\n"
    "\n"
    "===INSIGHTS===\n"
    "- The company is likely to expand into Asia\n"
)


class TestGenerateStructuredSummary:
    """Tests for generate_structured_summary()."""

    def test_returns_parsed_dict(self):
        llm = MagicMock()
        llm.generate.return_value = SAMPLE_LLM_RESPONSE

        result = generate_structured_summary(llm, "Some document text")

        assert result == {
            "data": [
                "Revenue was $4.2 million in Q3 2025",
                "The company employs 150 people",
            ],
            "facts": [
                "The company was founded in 2019",
                "Jane Doe is the CEO",
            ],
            "insights": [
                "The company is likely to expand into Asia",
            ],
        }

    def test_passes_document_to_prompt(self):
        llm = MagicMock()
        llm.generate.return_value = SAMPLE_LLM_RESPONSE

        doc = "This is the full document content."
        generate_structured_summary(llm, doc)

        llm.generate.assert_called_once()
        call_arg = llm.generate.call_args[0][0]
        assert doc in call_arg
        assert STRUCTURED_SUMMARY_PROMPT.format(document=doc) == call_arg

    def test_handles_llm_exception(self):
        llm = MagicMock()
        llm.generate.side_effect = RuntimeError("LLM unavailable")

        result = generate_structured_summary(llm, "Some document")

        assert result == {"data": [], "facts": [], "insights": []}

    def test_handles_llm_empty_response(self):
        llm = MagicMock()
        llm.generate.return_value = ""

        result = generate_structured_summary(llm, "Some document")

        assert result == {"data": [], "facts": [], "insights": []}

    def test_prompt_template_contains_document_placeholder(self):
        assert "{document}" in STRUCTURED_SUMMARY_PROMPT

    def test_prompt_template_has_all_sections(self):
        assert "===DATA===" in STRUCTURED_SUMMARY_PROMPT
        assert "===FACTS===" in STRUCTURED_SUMMARY_PROMPT
        assert "===INSIGHTS===" in STRUCTURED_SUMMARY_PROMPT
