"""A chat turn performs at most one preliminary search, including streaming."""
import asyncio
import json
from unittest.mock import patch

from src.chatbox.agent import ChatboxAgent
from src.config import AppConfig
from tests.test_chatbox_agent import (
    store, mock_llm, mock_agentic, _fake_llm_response, _fake_stream, _tc_delta,
)


def test_sync_turn_passes_original_question_and_seeds_only_first_decomposed_search(store, mock_llm, mock_agentic):
    tool = lambda cid, query: {"id": cid, "type": "function", "function": {
        "name": "search_knowledge_base", "arguments": json.dumps({"raw_query": query}),
    }}
    mock_llm._client.chat.completions.create.side_effect = [
        _fake_llm_response(tool_calls=[tool("first", "annual output")]),
        _fake_llm_response(tool_calls=[tool("second", "permit status")]),
        _fake_llm_response(content="answer"),
    ]
    agent = ChatboxAgent(store, mock_llm, mock_agentic)
    session = store.create_session(title="preliminary search")
    with patch("src.config.get_config", return_value=AppConfig()):
        agent.chat(session.id, "Compare output and permits", mode="agentic")
    calls = mock_agentic.run.call_args_list
    assert len(calls) == 2
    assert [c.kwargs.get("preliminary_retrieval") for c in calls] == [True, False]
    assert all(c.kwargs.get("original_user_question") == "Compare output and permits" for c in calls)
    assert [c.args[0] for c in calls] == ["annual output", "permit status"]


def test_stream_turn_passes_original_question_and_seeds_only_first_decomposed_search(store, mock_llm, mock_agentic):
    def tool_stream(cid, query):
        return _fake_stream([
            ("tool_calls", {"tool_calls": [
                _tc_delta(0, cid, "search_knowledge_base", json.dumps({"raw_query": query})),
            ]}),
        ])
    mock_llm._client.chat.completions.create.side_effect = [
        tool_stream("first", "annual output"), tool_stream("second", "permit status"),
        _fake_stream([("stop", {"content": "answer"})]),
    ]
    agent = ChatboxAgent(store, mock_llm, mock_agentic)
    session = store.create_session(title="stream preliminary search")

    async def collect():
        with patch("src.config.get_config", return_value=AppConfig()):
            return [e async for e in agent.chat_stream(session.id, "Compare output and permits", mode="agentic")]

    asyncio.run(collect())
    calls = mock_agentic.run.call_args_list
    assert len(calls) == 2
    assert [c.kwargs.get("preliminary_retrieval") for c in calls] == [True, False]
    assert all(c.kwargs.get("original_user_question") == "Compare output and permits" for c in calls)
