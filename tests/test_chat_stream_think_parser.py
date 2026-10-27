"""Exercise real stream assembly: answer tokens and saved text stay lossless."""

import asyncio
from unittest.mock import patch

import pytest

from src.chatbox.agent import ChatboxAgent
from src.config import AppConfig
from tests.test_chatbox_agent import store, mock_llm, mock_agentic, _fake_stream


SVG_ANSWER = '  ```svg\n<svg xmlns="http://www.w3.org/2000/svg"><rect width="10"/><text>图 &lt; A</text><path d="M0 0"/></svg>\n```\n '


@pytest.fixture
def run_stream(store, mock_llm, mock_agentic):
    def run(parts, *, thinking=True, native_reasoning=None):
        agent = ChatboxAgent(store, mock_llm, mock_agentic)
        session = store.create_session(title="Lossless stream")
        chunks = list(_fake_stream([(None, {"content": part}) for part in parts] + [("stop", {})]))
        if native_reasoning is not None:
            chunks[0].choices[0].delta.reasoning_content = native_reasoning
        mock_llm._client.chat.completions.create.return_value = iter(chunks)
        with patch("src.config.get_config", return_value=AppConfig()), \
             patch.object(agent, "_has_meetings", return_value=False), \
             patch.object(agent, "_build_catalog_text", return_value=""):
            async def collect():
                return [event async for event in agent.chat_stream(session.id, "Draw the diagram.", thinking=thinking)]
            events = asyncio.run(collect())
        saved = store.get_messages(session.id)[-1]
        answer = "".join(event["content"] for event in events if event["type"] == "token")
        thoughts = "".join(event["content"] for event in events if event["type"] == "thinking")
        assert events[-1]["type"] == "done"
        assert saved.role == "assistant"
        assert saved.content == answer, "SSE tokens and persisted answer must agree exactly"
        return answer, thoughts, saved
    return run


@pytest.mark.parametrize("thinking", [False, True])
@pytest.mark.parametrize("chunk_size", [1, 2, 7, len(SVG_ANSWER)])
def test_svg_xml_and_whitespace_survive_arbitrary_content_chunks(run_stream, thinking, chunk_size):
    answer, thoughts, _ = run_stream(
        [SVG_ANSWER[i:i + chunk_size] for i in range(0, len(SVG_ANSWER), chunk_size)],
        thinking=thinking,
    )
    assert answer == SVG_ANSWER
    assert thoughts == ""


@pytest.mark.parametrize("suffix", ["<", "<t", "<th", "<thin", "<think"])
def test_unfinished_open_tag_prefix_is_ordinary_answer_at_eof(run_stream, suffix):
    answer, thoughts, _ = run_stream(["Answer ", *suffix])
    assert answer == "Answer " + suffix
    assert thoughts == ""


@pytest.mark.parametrize("thinking", [False, True])
def test_split_think_delimiters_emit_reasoning_once_and_preserve_surrounding_answer(run_stream, thinking):
    raw = " Before <think>Reasoning Ω</think> After "
    # Every two-chunk boundary, including boundaries within either delimiter.
    for split in range(len(raw) + 1):
        answer, thoughts, saved = run_stream([raw[:split], raw[split:]], thinking=thinking)
        assert answer == " Before  After ", split
        assert thoughts == ("Reasoning Ω" if thinking else ""), split
        assert saved.metadata["reasoning_content"] == "Reasoning Ω", split


@pytest.mark.parametrize("thinking", [False, True])
def test_character_chunks_handle_multiple_reasoning_blocks_and_svg(run_stream, thinking):
    raw = "<think>First</think>" + SVG_ANSWER + "<think>Second</think>Tail<text>literal</text>"
    answer, thoughts, saved = run_stream(list(raw), thinking=thinking)
    assert answer == SVG_ANSWER + "Tail<text>literal</text>"
    assert thoughts == ("FirstSecond" if thinking else "")
    assert saved.metadata["reasoning_content"] == "FirstSecond"


@pytest.mark.parametrize("thinking", [False, True])
@pytest.mark.parametrize("raw,reasoning", [
    ("<think>Private reasoning</think>", "Private reasoning"),
    ("<think>Private reasoning</th", "Private reasoning</th"),
])
def test_reasoning_only_response_never_becomes_answer(run_stream, thinking, raw, reasoning):
    answer, thoughts, saved = run_stream(list(raw), thinking=thinking)
    assert answer == ""
    assert thoughts == (reasoning if thinking else "")
    assert saved.metadata["reasoning_content"] == reasoning


@pytest.mark.parametrize("thinking", [False, True])
def test_independent_reasoning_channel_keeps_existing_visibility(run_stream, thinking):
    answer, thoughts, saved = run_stream(["Answer"], thinking=thinking, native_reasoning="Native reasoning")
    assert answer == "Answer"
    assert thoughts == ("Native reasoning" if thinking else "")
    assert saved.metadata["reasoning_content"] == "Native reasoning"


@pytest.mark.parametrize("thinking", [False, True])
def test_independent_reasoning_only_channel_retains_existing_answer_fallback(run_stream, thinking):
    answer, thoughts, saved = run_stream([""], thinking=thinking, native_reasoning="  Native answer  ")
    assert answer == "Native answer"
    assert thoughts == ("  Native answer  " if thinking else "")
    assert saved.metadata["reasoning_content"] == "  Native answer  "
