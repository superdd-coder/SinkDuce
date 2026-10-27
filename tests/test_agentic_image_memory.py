"""Real chat entry points must preserve full Agentic evidence across turns."""

import asyncio
import json
from unittest.mock import patch

import pytest

from src.chatbox.agent import ChatboxAgent
from src.config import AppConfig
from src.rag.agentic_query import AgenticQueryResult
from tests.test_chatbox_agent import (
    store, mock_llm, mock_agentic, _fake_llm_response, _fake_stream, _tc_delta,
)


@pytest.mark.parametrize("streaming", [False, True])
def test_agentic_multimodal_result_saves_full_text_and_byte_stable_image(
    store, mock_llm, mock_agentic, tmp_path, streaming,
):
    from src.chatbox.image_memory import load_session_image
    iid = "a" * 32
    context = "Primary passage " * 100 + f"\n:::image\nimage_id: {iid}\nfile_id: f1\n:::\n"
    payload = "data:image/png;base64,AAAA"
    mock_agentic.run.return_value = AgenticQueryResult(
        answer=[{"type": "text", "text": context},
                {"type": "image_url", "image_url": {"url": payload}}],
        context=context, images={iid: {"base64": "AAAA", "mime": "image/png"}},
    )
    name = "search_knowledge_base"
    args = json.dumps({"raw_query": "Find primary evidence", "include_images": True})
    create = mock_llm._client.chat.completions.create
    create.side_effect = ([
        _fake_stream([("tool_calls", {"tool_calls": [_tc_delta(0, "search", name, args)]})]),
        _fake_stream([("stop", {"content": "Answer"})]),
    ] if streaming else [
        _fake_llm_response(tool_calls=[{"id": "search", "type": "function",
                                      "function": {"name": name, "arguments": args}}]),
        _fake_llm_response(content="Answer"),
    ])
    agent = ChatboxAgent(store, mock_llm, mock_agentic)
    agent._image_store_dir = tmp_path / "images"
    session = store.create_session(title="Evidence memory")
    with patch("src.config.get_config", return_value=AppConfig()):
        if streaming:
            async def collect():
                return [e async for e in agent.chat_stream(session.id, "Question", mode="agentic")]
            asyncio.run(collect())
        else:
            agent.chat(session.id, "Question", mode="agentic")
    tools = [m for m in store.get_messages(session.id) if m.role == "tool"]
    assert tools[0].content == context
    assert load_session_image(session.id, iid, base_dir=agent._image_store_dir) == payload
