"""Chat entry points preserve conversation/tool payloads across parameter adaptation."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import httpx
from openai import BadRequestError

from src.chatbox import agent as chat_agent
from src.db.sessions import SessionStore

_TOOL = {
    "type": "function",
    "function": {
        "name": "lookup_collection",
        "description": "Read the visitor guide.",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _temperature_rejected():
    message = (
        "Unsupported value: 'temperature' does not support 0.1 with this model. "
        "Only the default (1) value is supported."
    )
    body = {"message": message, "param": "temperature", "code": "unsupported_value"}
    request = httpx.Request("POST", "https://models.example.test/v1/chat/completions")
    response = httpx.Response(400, request=request, json={"error": body})
    return BadRequestError(message, response=response, body=body)


def _llm(case, *outcomes):
    calls = []
    pending = iter(outcomes)

    def create(**kwargs):
        calls.append(deepcopy(kwargs))
        outcome = next(pending)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    llm = SimpleNamespace(
        _model="fixture-model",
        _base_url=f"https://{case}.example.test/v1",
        _default_max_tokens=73,
        _client=client,
    )
    return llm, calls


def _reply(content):
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _chunk(content):
    delta = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason="stop")])


def _stream_events(tmp_path, monkeypatch, llm):
    store = SessionStore(str(tmp_path / "chat.db"))
    agent = chat_agent.ChatboxAgent(store, llm, None)
    session = store.create_session(title="Visitor guide")
    monkeypatch.setattr(chat_agent, "_build_surface_context", lambda *_: None)
    monkeypatch.setattr(agent, "_has_meetings", lambda *_: False)
    monkeypatch.setattr(
        agent, "_resolve_tools_and_prompt", lambda *_, **__: ([_TOOL], "Use the visitor guide.", "")
    )

    async def collect():
        return [
            event
            async for event in agent.chat_stream(
                session.id, "Where is the display?", thinking=False
            )
        ]

    try:
        return asyncio.run(collect())
    finally:
        connection = getattr(store._local, "conn", None)
        if connection:
            connection.close()


def test_sync_tools_omit_only_rejected_temperature(tmp_path):
    llm, calls = _llm("sync", _temperature_rejected(), _reply("The display is upstairs."))
    store = SessionStore(str(tmp_path / "chat.db"))
    agent = chat_agent.ChatboxAgent(store, llm, None)
    messages = [{"role": "user", "content": "Where is the display?"}]
    choice = {"type": "function", "function": {"name": "lookup_collection"}}
    answer = agent._call_llm_with_tools(messages, tools=[_TOOL], tool_choice=choice)
    assert answer["content"] == "The display is upstairs."
    assert len(calls) == 2
    assert calls[0]["temperature"] == 0.1
    assert calls[1] == {key: value for key, value in calls[0].items() if key != "temperature"}
    assert calls[1]["messages"] == messages
    assert calls[1]["tools"] == [_TOOL]
    assert calls[1]["tool_choice"] == choice
    assert calls[1]["max_tokens"] == 73


def test_stream_compatibility_retains_streaming_and_tools(tmp_path, monkeypatch):
    llm, calls = _llm("stream", _temperature_rejected(), iter([_chunk("The display is upstairs.")]))
    events = _stream_events(tmp_path, monkeypatch, llm)
    assert [event["content"] for event in events if event["type"] == "token"] == [
        "The display is upstairs."
    ]
    assert not any(event["type"] == "error" for event in events)
    assert len(calls) == 2
    assert calls[1] == {key: value for key, value in calls[0].items() if key != "temperature"}
    assert calls[1]["stream"] is True
    assert _TOOL in calls[1]["tools"]
    assert calls[1]["messages"][-1] == {"role": "user", "content": "Where is the display?"}


def test_force_answer_adapts_before_losing_final_response():
    llm, calls = _llm("force", _temperature_rejected(), _reply("The display is upstairs."))
    messages = [{"role": "user", "content": "Where is the display?"}]
    answer = chat_agent._force_generate_answer(llm, messages)
    assert answer == "The display is upstairs."
    assert len(calls) == 2
    assert calls[1] == {key: value for key, value in calls[0].items() if key != "temperature"}
    assert calls[1]["messages"][:1] == messages
    assert len(calls[1]["messages"]) == 2
    assert "tools" not in calls[1]


def test_stream_does_not_retry_after_delivering_a_token(tmp_path, monkeypatch):
    def interrupted():
        yield _chunk("The display")
        raise _temperature_rejected()

    llm, calls = _llm("interrupted", interrupted())
    events = _stream_events(tmp_path, monkeypatch, llm)
    assert [event["content"] for event in events if event["type"] == "token"] == ["The display"]
    assert any(event["type"] == "error" for event in events)
    assert len(calls) == 1


def test_force_answer_respects_provider_without_thinking_parameters():
    from src.config import LLMProviderConfig
    from src.providers.llm.openai_compat import OpenAICompatLLM

    stub, calls = _llm(
        "force-no-thinking", _temperature_rejected(), _reply("The display is upstairs.")
    )
    llm = OpenAICompatLLM(
        LLMProviderConfig(
            base_url=stub._base_url,
            api_key="test-only",
            model=stub._model,
            thinking_mode="none",
        )
    )
    llm._client = stub._client
    messages = [{"role": "user", "content": "Where is the display?"}]
    assert chat_agent._force_generate_answer(llm, messages) == "The display is upstairs."
    assert len(calls) == 2
    assert all("extra_body" not in call and "reasoning_effort" not in call for call in calls)
