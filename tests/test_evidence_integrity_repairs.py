"""Do not promote derived image descriptions or unsupported grade arithmetic."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

from src.rag.agent_nodes import node_combined_grade
from src.rag.agent_state import AgentState
from src.rag.retriever import RetrievedChunk


def grade(body, response):
    chunk = RetrievedChunk(body, 0.9, {"id": "p1", "source": "proposal.pdf"})
    state = AgentState(original_query="Explain the investment terms")
    llm = SimpleNamespace(generate=Mock(return_value=json.dumps(response)))
    node_combined_grade(state, [chunk], llm=llm)
    return state, llm


def test_grade_does_not_promote_unsupported_complement_of_equity_percentage():
    body = "Investor may acquire up to 35% of the vehicle's equity. Loan allocation is unspecified."
    state, llm = grade(body, {"relevant_indices": [0],
                            "retained_info": "At least 65% of the cash is a loan.", "gap_analysis": ""})
    assert "65%" not in state.retained_info
    assert "35%" in state.retained_info
    assert state.retained_chunks
    assert llm.generate.call_count == 1


def test_grade_accepts_only_exact_quotes_from_selected_primary_passages():
    body = "The investor acquires equity in Vehicle A."
    state, _ = grade(body, {"relevant_indices": [0], "retained_info": "Some invented interpretation",
                           "evidence": [{"index": 0, "quote": body},
                                        {"index": 0, "quote": "Investment is in Company B."}],
                           "gap_analysis": ""})
    assert body in state.retained_info
    assert "Company B" not in state.retained_info
    assert "invented" not in state.retained_info


def test_generated_image_description_cannot_be_certified_as_primary_quote():
    body = ("Register attached.\n:::image\nimage_id: " + "a" * 32 +
            "\nfile_id: f1\nocr_text: 699,999 4,300,000\n"
            "description: An unnamed holder owns 4,350,000 shares.\n:::\n")
    state, _ = grade(body, {"relevant_indices": [0],
                           "retained_info": "An unnamed holder owns 4,350,000 shares.",
                           "evidence": [{"index": 0, "quote": "An unnamed holder owns 4,350,000 shares."}],
                           "gap_analysis": ""})
    assert "4,350,000" not in state.retained_info


def test_grade_cannot_create_findings_when_no_sources_are_selected():
    state, _ = grade("Unrelated text", {"relevant_indices": [],
                                       "retained_info": "A fabricated fact.", "gap_analysis": "missing"})
    assert state.retained_info == ""


def test_visual_requests_prioritize_original_pixels_over_generated_interpretation():
    from src.rag.agentic_query import _build_multimodal_context
    iid = "a" * 32
    body = (f":::image\nimage_id: {iid}\nfile_id: f1\n"
            "ocr_text: 699,999\ndescription: An unnamed holder owns 635,999 shares.\n:::\n")
    parts = _build_multimodal_context(body, {iid: {"base64": "AAAA", "mime": "image/png"}})
    assert any(p["type"] == "image_url" for p in parts)
    assert "635,999" not in json.dumps(parts)


def test_conflicting_figure_numbers_are_not_sent_as_factual_text():
    from src.rag.context_builder import build_context
    from src.rag.index_text import build_evidence_text
    body = (":::image\nimage_id: " + "a" * 32 + "\nfile_id: f1\n"
            "ocr_text: 699,999 4,300,000\n"
            "description: An unnamed holder owns 4,350,000 shares.\n:::\n")
    chunk = RetrievedChunk(body, 0.9, {"id": "p1", "source": "register.pdf"})
    for output in [build_context([chunk]), build_evidence_text(chunk)]:
        assert "4,350,000" not in output
        assert "4,300,000" in output
        assert "disagreement" in output.lower()
    assert "4,350,000" in chunk.text, "Preserve raw stored readings for diagnosis"


def test_fractional_or_boolean_source_indices_do_not_select_a_different_source():
    state, _ = grade("Primary source", {"relevant_indices": [0.8, True],
                                        "retained_info": "Primary source", "gap_analysis": ""})
    assert not state.retained_chunks
    assert not state.retained_info
