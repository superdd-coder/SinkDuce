"""CV-02: inline prompts moved to prompts.py / agent_prompts.py."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

SOURCES = [
    SRC / "chatbox" / "agent.py",
    SRC / "api" / "routes" / "recall.py",
    SRC / "rag" / "decomposer.py",
    SRC / "rag" / "aggregator.py",
    SRC / "rag" / "sparse_encoder.py",
    SRC / "rag" / "agentic_query.py",
]

MOVED_OPENERS = [
    "You are a knowledge base assistant for ingested documents.",
    'You are a quick Q&A assistant for the document collection',
    "You are building a search evaluation dataset.",
    "You are evaluating retrieval quality for a RAG system.",
    "You are a search query optimizer for a knowledge base system.",
    "You are a collection router. Given a search query",
    "You are a research assistant synthesizing information from multiple searches.",
    "You are a keyword extraction engine for a BM25",
    "You are a helpful research assistant. Answer the user's question",
]


def test_registry_exports_moved_prompts():
    from src.prompts import (
        CHAT_FORCE_ANSWER_SYSTEM,
        DEFAULT_SYSTEM_PROMPT,
        QUICK_CHAT_SYSTEM_PROMPT,
        RECALL_EVAL_CASE_PROMPT,
        RECALL_EVAL_JUDGE_PROMPT,
    )
    from src.rag.agent_prompts import (
        AGGREGATE_GROUP_SYSTEM,
        DECOMPOSE_SYSTEM,
        GENERATE_ANSWER_SYSTEM,
        PREPROCESS_SPARSE_QUERY_SYSTEM,
        ROUTE_SYSTEM,
    )

    assert DEFAULT_SYSTEM_PROMPT.startswith("You are a knowledge base assistant")
    assert "%(collection_name)s" in QUICK_CHAT_SYSTEM_PROMPT
    assert CHAT_FORCE_ANSWER_SYSTEM == "You are a helpful assistant."
    assert "{n_chunks}" in RECALL_EVAL_CASE_PROMPT
    assert "{query_text}" in RECALL_EVAL_JUDGE_PROMPT
    assert DECOMPOSE_SYSTEM.startswith("You are a search query optimizer")
    assert ROUTE_SYSTEM.startswith("You are a collection router")
    assert AGGREGATE_GROUP_SYSTEM.startswith("You are a research assistant")
    assert "BM25" in PREPROCESS_SPARSE_QUERY_SYSTEM
    assert GENERATE_ANSWER_SYSTEM.startswith("You are a helpful research assistant")


def test_source_files_no_longer_define_moved_prompt_bodies():
    for path in SOURCES:
        text = path.read_text(encoding="utf-8")
        for opener in MOVED_OPENERS:
            assert opener not in text, f"{path.name} still contains {opener!r}"


def test_callers_import_from_registry():
    agent = (SRC / "chatbox" / "agent.py").read_text(encoding="utf-8")
    assert "from src.prompts import" in agent
    assert "DEFAULT_SYSTEM_PROMPT" in agent
    recall = (SRC / "api" / "routes" / "recall.py").read_text(encoding="utf-8")
    assert "RECALL_EVAL_CASE_PROMPT" in recall
    assert "RECALL_EVAL_JUDGE_PROMPT" in recall
    dec = (SRC / "rag" / "decomposer.py").read_text(encoding="utf-8")
    assert "from src.rag.agent_prompts import" in dec
    agg = (SRC / "rag" / "aggregator.py").read_text(encoding="utf-8")
    assert "AGGREGATE_GROUP_SYSTEM" in agg
    sparse = (SRC / "rag" / "sparse_encoder.py").read_text(encoding="utf-8")
    assert "PREPROCESS_SPARSE_QUERY_SYSTEM" in sparse


def test_recall_templates_format():
    from src.prompts import RECALL_EVAL_CASE_PROMPT, RECALL_EVAL_JUDGE_PROMPT

    case = RECALL_EVAL_CASE_PROMPT.format(
        n_chunks=2,
        n_files=1,
        file_names="a.pdf",
        summary_hint="",
        chunk_list="[1] hi",
    )
    assert "Below are 2 chunks sampled from 1 document(s)." in case
    assert '[{"query": "...", "target_chunk_index": 1}' in case
    judge = RECALL_EVAL_JUDGE_PROMPT.format(
        query_text="what is X",
        k=3,
        chunks_text="chunk",
    )
    assert '"what is X"' in judge
    assert "... 3 entries ..." in judge
