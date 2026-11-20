"""Inspect Chat Completions and Responses requests serialized by the OpenAI SDK."""

import asyncio
import json
import re
from copy import deepcopy
from itertools import product
from types import SimpleNamespace

import httpx
import pytest
from jsonschema import Draft202012Validator
from openai import OpenAI

from src.chatbox import agent as chat_agent
from src.chatbox import query_tools
from src.config import LLMProviderConfig
from src.db.sessions import SessionStore
from src.prompts import CHAT_ATTACHMENT_TOOLS, CHAT_REPORT_TOOLS
from src.providers.llm import openai_compat, parameters
from src.providers.llm.openai_responses import OpenAIResponsesChat


OPENAI_URL = "https://api.openai.com/v1"
COMPATIBLE_URL = "https://api.openai.com.example.test/v1"
INVALID_ROOT_KEYWORDS = {"oneOf", "anyOf", "allOf", "enum", "const", "not"}


@pytest.fixture
def sdk_provider(monkeypatch):
    clients = []

    def make(handler, *, endpoint=OPENAI_URL, model="fixture-model", api_mode=None):
        def make_client(**kwargs):
            client = OpenAI(
                **kwargs,
                http_client=httpx.Client(transport=httpx.MockTransport(handler)),
                max_retries=0,
            )
            clients.append(client)
            return client

        monkeypatch.setattr(openai_compat, "OpenAI", make_client)
        return openai_compat.OpenAICompatLLM(LLMProviderConfig(
            base_url=endpoint, api_key="test-key", model=model, thinking_mode="toggle",
            api_mode=api_mode,
        ))

    monkeypatch.setattr(parameters, "_PARAMETER_RULES", {})
    yield make
    for client in clients:
        client.close()


def _completion(body, text="Ready."):
    common = {"id": "chatcmpl-fixture", "created": 1, "model": body["model"]}
    choice = {"index": 0, "finish_reason": "stop"}
    message = {"role": "assistant", "content": text}
    if body.get("stream"):
        chunk = {**common, "object": "chat.completion.chunk", "choices": [{**choice, "delta": message}]}
        return httpx.Response(
            200, text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
            headers={"Content-Type": "text/event-stream"},
        )
    return httpx.Response(200, json={
        **common, "object": "chat.completion", "choices": [{**choice, "message": message}],
    })


def _responses_reply(body, output, *events, status="completed", incomplete_details=None):
    response = {
        "id": "resp_fixture", "object": "response", "created_at": 1,
        "model": body["model"], "status": status, "output": output,
    }
    if incomplete_details:
        response["incomplete_details"] = incomplete_details
    if not body.get("stream"):
        return httpx.Response(200, json=response)
    series = [*events, {"type": f"response.{status}", "response": response}]
    return httpx.Response(
        200, headers={"Content-Type": "text/event-stream"},
        text="".join(f"data: {json.dumps({**evt, 'sequence_number': i})}\n\n" for i, evt in enumerate(series)),
    )


def _responses_text(text):
    return {
        "type": "message", "id": "msg_fixture", "role": "assistant", "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _unsupported_tools_response():
    return httpx.Response(400, json={"error": {
        "message": "Function tools with reasoning_effort are not supported for gpt-6.1-sol in /v1/chat/completions.",
        "type": "invalid_request_error", "param": "reasoning_effort",
    }})


@pytest.mark.parametrize(("endpoint", "api_mode", "model", "expected_path"), [
    (OPENAI_URL, None, "gpt-4o-mini", "/v1/responses"),
    ("https://eu.api.openai.com/v1", None, "gpt-4o-mini", "/v1/responses"),
    (COMPATIBLE_URL, None, "gpt-6.1-sol", "/v1/chat/completions"),
    (OPENAI_URL, "chat_completions", "gpt-6.1-sol", "/v1/chat/completions"),
    (COMPATIBLE_URL, "responses", "fixture-model", "/v1/responses"),
])
def test_api_mode_routes_chat_and_generation(sdk_provider, tmp_path, endpoint, api_mode, model, expected_path):
    paths = []

    def handler(request):
        body = json.loads(request.content)
        paths.append(request.url.path)
        if request.url.path == "/v1/responses":
            return _responses_reply(body, [_responses_text("Ready.")])
        return _completion(body)

    llm = sdk_provider(handler, endpoint=endpoint, model=model, api_mode=api_mode)
    agent = chat_agent.ChatboxAgent(SessionStore(str(tmp_path / "chat.db")), llm, None)
    assert agent._call_llm_with_tools([{"role": "user", "content": "Hello"}], tools=[]) == {"content": "Ready."}
    assert llm.generate("Hello") == "Ready."
    assert paths == [expected_path, expected_path]


def test_official_gpt6_chat_uses_responses_for_function_tools(sdk_provider, tmp_path):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append((request.url.path, body))
        if request.url.path == "/v1/chat/completions":
            return _unsupported_tools_response()
        return _responses_reply(body, [_responses_text("Ready.")])

    llm = sdk_provider(handler, model="gpt-6.1-sol")
    agent = chat_agent.ChatboxAgent(SessionStore(str(tmp_path / "chat.db")), llm, None)
    tool = next(tool for tool in CHAT_REPORT_TOOLS if tool["function"]["name"] == "update_report")
    result = agent._call_llm_with_tools([{"role": "user", "content": "Review the fictional report."}], tools=[tool])

    assert [path for path, _ in requests] == ["/v1/responses"]
    body = requests[0][1]
    assert result["content"] == "Ready."
    assert body["store"] is False
    assert body["reasoning"]["effort"] == "low"
    assert {"messages", "temperature", "reasoning_effort"}.isdisjoint(body)
    assert body["tools"] == [{"type": "function", **tool["function"], "strict": False}]


def test_gpt6_stream_replays_reasoning_with_tool_outputs(sdk_provider, monkeypatch, tmp_path):
    requests = []
    reason = {"type": "reasoning", "id": "rs_fixture", "summary": [],
              "encrypted_content": "fixture-encrypted-reasoning"}
    call = {"type": "function_call", "id": "fc_fixture", "call_id": "call_fixture",
            "name": "list_collections", "arguments": "{}", "status": "completed"}

    def handler(request):
        body = json.loads(request.content)
        requests.append((request.url.path, body))
        if request.url.path == "/v1/chat/completions":
            return _unsupported_tools_response()
        if len(requests) == 1:
            return _responses_reply(body, [reason, call])
        return _responses_reply(body, [_responses_text("One project is available.")],
                                {"type": "response.output_text.delta", "delta": "One project is available.",
                                 "item_id": "msg_fixture", "output_index": 0, "content_index": 0})

    store = SessionStore(str(tmp_path / "chat.db"))
    store.create_session(session_id="group_fixture")
    llm = sdk_provider(handler, model="gpt-6.1-sol")
    agent = chat_agent.ChatboxAgent(store, llm, None)
    monkeypatch.setattr(agent, "_build_catalog_text", lambda *_a, **_k: "")
    monkeypatch.setattr(chat_agent, "_build_surface_context", lambda *_a, **_k: "")

    async def dispatch(tool, *args, **kwargs):
        assert tool == "list_collections"
        return '{"collections":["Rehearsal plan"]}'

    monkeypatch.setattr(chat_agent, "dispatch_structure_tool_async", dispatch)

    async def collect():
        return [event async for event in agent.chat_stream("group_fixture", "Which projects are available?",
                                                           mode="direct", thinking=True)]

    events = asyncio.run(collect())
    assert [path for path, _ in requests] == ["/v1/responses", "/v1/responses"]
    first, second = [body for _, body in requests]
    assert [first["reasoning"]["effort"], second["reasoning"]["effort"]] == ["medium", "low"]
    assert second["input"][:-3] == first["input"]
    assert second["input"][-3:-1] == [reason, call]
    output = second["input"][-1]
    assert output["type"] == "function_call_output" and output["call_id"] == "call_fixture"
    assert json.loads(output["output"])["collections"] == ["Rehearsal plan"]
    assert [event["content"] for event in events if event["type"] == "token"] == ["One project is available."]
    assert events[-1]["type"] == "done"
    assert all("fixture-encrypted-reasoning" not in json.dumps(row.metadata)
               for row in store.get_messages("group_fixture"))


def test_gpt6_model_name_on_a_compatible_gateway_still_uses_chat_completions(sdk_provider, tmp_path):
    llm = sdk_provider(_strict_completion, endpoint=COMPATIBLE_URL, model="gpt-6.1-sol")
    agent = chat_agent.ChatboxAgent(SessionStore(str(tmp_path / "chat.db")), llm, None)
    result = agent._call_llm_with_tools([{"role": "user", "content": "Hello."}], tools=CHAT_REPORT_TOOLS)
    assert result["content"] == "Ready."


def test_responses_converts_images_in_legacy_tool_results(sdk_provider):
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        return _responses_reply(body, [_responses_text("Ready.")])

    llm = sdk_provider(handler, endpoint="https://eu.api.openai.com/v1", model="gpt-6.1-sol")
    messages = [
        {"role": "user", "content": "Inspect the test excerpt."},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_fixture", "type": "function", "function": {"name": "read_section", "arguments": "{}"}},
        ], "reasoning_content": "Old provider's internal field."},
        {"role": "tool", "tool_call_id": "call_fixture", "content": [
            {"type": "text", "text": "A sample excerpt."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA==", "detail": None}},
        ]},
    ]
    original = deepcopy(messages)
    result = chat_agent._force_generate_answer(llm, messages)
    assert messages == original
    assert result == "Ready."
    body = bodies[0]
    assert "tools" not in body and "tool_choice" not in body
    assert body["max_output_tokens"] == 4096
    assert body["input"][1] == {"type": "function_call", "call_id": "call_fixture", "name": "read_section", "arguments": "{}"}
    assert body["input"][2] == {"type": "function_call_output", "call_id": "call_fixture", "output": [
        {"type": "input_text", "text": "A sample excerpt."},
        {"type": "input_image", "image_url": "data:image/png;base64,AA==", "detail": "auto"},
    ]}


def test_responses_stream_does_not_dispatch_an_incomplete_function(sdk_provider):
    call = {"type": "function_call", "call_id": "call_fixture", "name": "read_section",
            "arguments": '{"sect', "status": "incomplete"}
    llm = sdk_provider(lambda req: _responses_reply(json.loads(req.content), [call]), model="gpt-6.1-sol")
    stream = OpenAIResponsesChat.for_provider(llm).create({
        "model": llm._model, "messages": [{"role": "user", "content": "Inspect a test excerpt."}],
        "stream": True,
    })
    with pytest.raises(RuntimeError, match="incomplete function call"):
        next(stream)


def test_responses_drops_reasoning_when_input_scope_changes(sdk_provider):
    bodies = []
    reasoning = {"type": "reasoning", "id": "rs_fixture", "summary": [], "encrypted_content": "old-encrypted-prefix"}
    call = {"type": "function_call", "id": "fc_fixture", "call_id": "call_fixture",
            "name": "read_section", "arguments": "{}", "status": "completed"}

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        return _responses_reply(body, [reasoning, call] if len(bodies) == 1 else [_responses_text("Ready.")])

    llm = sdk_provider(handler, model="gpt-6.1-sol")
    responses = OpenAIResponsesChat.for_provider(llm)
    request = {"model": llm._model, "messages": [{"role": "user", "content": "Original context."}]}
    responses.create(request)
    messages = [
        {"role": "user", "content": "Current context."},
        {"role": "assistant", "tool_calls": [{"id": "call_fixture", "function": {"name": "read_section", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_fixture", "content": "Current result."},
    ]
    assert responses.create({**request, "messages": messages}).choices[0].message.content == "Ready."
    assert [item.get("type") for item in bodies[1]["input"]] == [None, "function_call", "function_call_output"]
    assert "old-encrypted-prefix" not in json.dumps(bodies[1])


def _check_tool_schema(tool):
    assert tool["type"] == "function"
    function = tool["function"]
    name = function["name"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name)
    schema = function["parameters"]
    assert schema["type"] == "object", name
    assert not INVALID_ROOT_KEYWORDS.intersection(schema), name
    assert set(schema.get("required", ())) <= set(schema["properties"]), name
    Draft202012Validator.check_schema(schema)


def _strict_completion(request):
    assert request.url.path == "/v1/chat/completions"
    body = json.loads(request.content)
    for index, tool in enumerate(body.get("tools", ())):
        function = tool["function"]
        schema = function["parameters"]
        if schema.get("type") != "object" or INVALID_ROOT_KEYWORDS.intersection(schema):
            return httpx.Response(400, json={"error": {
                "message": f"Invalid schema for function '{function['name']}': schema must have type 'object' and not have 'oneOf'/'anyOf'/'allOf'/'enum'/'const'/'not' at the top level.",
                "type": "invalid_request_error",
                "param": f"tools[{index}].function.parameters",
                "code": "invalid_function_parameters",
            }})
        _check_tool_schema(tool)
    return _completion(body)


def test_all_registered_chat_tool_schemas_are_openai_compatible():
    tools = [
        *query_tools.STRUCTURE_TOOL_SCHEMAS.values(), *CHAT_ATTACHMENT_TOOLS, *CHAT_REPORT_TOOLS,
        query_tools.LOOKUP_GROUP_TRANSCRIPT_TOOL,
    ]
    for (mode, scope), web_enabled in product([
        ("agentic", {}), ("direct", {}), ("direct", {"is_meeting": True}),
        ("direct", {"is_group": True}),
    ], (False, True)):
        tools.extend(query_tools.tools_for_mode(mode, **scope, web_search_enabled=web_enabled))
    for tool in tools:
        _check_tool_schema(tool)


@pytest.mark.parametrize(("session_id", "mode"), [
    ("main", "agentic"), ("quick_fixture", "direct"), ("meeting_fixture", "direct"),
    ("group_fixture", "direct"),
])
def test_chat_tool_payload_passes_openai_validation_on_both_request_paths(
    tmp_path, monkeypatch, sdk_provider, session_id, mode,
):
    store = SessionStore(str(tmp_path / "chat.db"))
    store.create_session(session_id=session_id)
    llm = sdk_provider(_strict_completion, api_mode="chat_completions")
    agent = chat_agent.ChatboxAgent(store, llm, None)
    monkeypatch.setattr(agent, "_build_catalog_text", lambda *_a, **_k: "")
    monkeypatch.setattr(chat_agent, "_build_surface_context", lambda *_a, **_k: "")
    tools, _, _ = agent._resolve_tools_and_prompt(mode, session_id)

    result = agent._call_llm_with_tools([{"role": "user", "content": "Hello"}], tools=tools)
    assert result["content"] == "Ready."

    async def collect():
        return [event async for event in agent.chat_stream(session_id, "Hello", mode=mode, thinking=False)]

    events = asyncio.run(collect())
    assert [event["content"] for event in events if event["type"] == "token"] == ["Ready."]
    assert events[-1]["type"] == "done"


@pytest.mark.parametrize("method", ["generate", "generate_stream", "generate_stream_tagged", "describe_image", "force_answer"])
@pytest.mark.parametrize(("endpoint", "api_mode"), [
    (OPENAI_URL, "chat_completions"), (COMPATIBLE_URL, "chat_completions"),
    (OPENAI_URL, "responses"), (COMPATIBLE_URL, "responses"),
])
def test_generation_request_uses_fields_supported_by_the_endpoint(sdk_provider, method, endpoint, api_mode):
    bodies = []
    use_responses = api_mode == "responses"

    def handler(request):
        assert request.url.path == ("/v1/responses" if use_responses else "/v1/chat/completions")
        body = json.loads(request.content)
        bodies.append(body)
        if use_responses:
            return _responses_reply(body, [_responses_text("Ready.")])
        return _completion(body)

    llm = sdk_provider(handler, endpoint=endpoint, api_mode=api_mode)
    if method == "describe_image":
        assert llm.describe_image("AA==", prompt="Describe the image.") == "Ready."
        if use_responses:
            assert bodies[0]["input"][0]["content"][1]["type"] == "input_image"
        else:
            assert bodies[0]["messages"][0]["content"][1]["type"] == "image_url"
        limit = 1024
    elif method == "force_answer":
        assert chat_agent._force_generate_answer(llm, [{"role": "user", "content": "Summarize."}]) == "Ready."
        limit = 4096
    else:
        result = getattr(llm, method)(
            "Return a JSON result.", max_tokens=73, temperature=0, thinking=False,
            response_format={"type": "json_object"},
        )
        if method == "generate":
            assert result == "Ready."
        elif method == "generate_stream":
            assert list(result) == ["Ready."]
        else:
            assert list(result) == [("Ready.", False)]
        assert bodies[0]["temperature"] == 0
        if use_responses:
            assert bodies[0]["text"] == {"format": {"type": "json_object"}}
        else:
            assert bodies[0]["response_format"] == {"type": "json_object"}
        limit = 73
    assert len(bodies) == 1
    body = bodies[0]
    if use_responses:
        assert body["max_output_tokens"] == limit
        assert body["store"] is False
        assert {"messages", "reasoning", "include", "reasoning_effort", "thinking", "response_format"}.isdisjoint(body)
    elif endpoint == COMPATIBLE_URL:
        assert body["max_tokens"] == limit
        assert body["thinking"] == {"type": "disabled"}
        assert "max_completion_tokens" not in body
    else:
        assert body["max_completion_tokens"] == limit
        assert {"thinking", "enable_thinking", "thinking_budget", "max_tokens"}.isdisjoint(body)


def test_responses_uses_the_structured_output_schema(sdk_provider):
    schema = {"name": "example", "schema": {"type": "object"}, "strict": False}

    def handler(request):
        assert request.url.path == "/v1/responses"
        body = json.loads(request.content)
        assert body["text"] == {"format": {"type": "json_schema", **schema}}
        return _responses_reply(body, [_responses_text("{}")])

    llm = sdk_provider(handler, model="gpt-4o-mini")
    assert llm.generate("Return JSON.", response_format={"type": "json_schema", "json_schema": schema}) == "{}"


def test_responses_returns_visible_text_at_the_token_limit(sdk_provider):
    def handler(request):
        assert request.url.path == "/v1/responses"
        body = json.loads(request.content)
        return _responses_reply(
            body, [_responses_text("Partial answer")],
            status="incomplete", incomplete_details={"reason": "max_output_tokens"},
        )

    llm = sdk_provider(handler, model="gpt-4o-mini")
    assert llm.generate("Describe the story.", max_tokens=16) == "Partial answer"
    assert list(llm.generate_stream("Describe the story.", max_tokens=16)) == ["Partial answer"]


def test_batch_uses_responses_and_can_read_earlier_completion_results(sdk_provider, monkeypatch):
    def unexpected_request(request):
        pytest.fail(f"Unexpected HTTP path {request.url.path}")

    llm = sdk_provider(unexpected_request, endpoint=COMPATIBLE_URL, api_mode="responses")
    requests = []

    def upload(file, purpose):
        requests.extend(json.loads(line) for line in file.read().decode().splitlines())
        return SimpleNamespace(id="fixture-file")

    def submit(**kwargs):
        assert kwargs["endpoint"] == "/v1/responses"
        return SimpleNamespace(id="fixture-batch")

    monkeypatch.setattr(llm._client.files, "create", upload)
    monkeypatch.setattr(llm._client.batches, "create", submit)
    assert llm.batch_submit([{"prompt": "Draft a title.", "max_tokens": 64}]) == "fixture-batch"
    assert requests[0]["url"] == "/v1/responses"
    assert requests[0]["body"]["max_output_tokens"] == 64
    assert requests[0]["body"]["input"] == [{"role": "user", "content": "Draft a title."}]

    output = "\n".join(json.dumps({"custom_id": str(i), "response": {"body": body}}) for i, body in enumerate([
        {"output": [_responses_text("A new draft")]},
        {"choices": [{"message": {"content": "An older draft"}}]},
    ]))
    monkeypatch.setattr(llm._client.files, "content", lambda _id: SimpleNamespace(text=output))
    monkeypatch.setattr(llm._client.batches, "retrieve", lambda _id: SimpleNamespace(
        status="completed", output_file_id="fixture-output", request_counts=SimpleNamespace(total=2),
    ))
    assert llm.batch_poll("fixture-batch") == ["A new draft", "An older draft"]


def test_empty_tool_list_omits_tool_choice(sdk_provider):
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        return _completion(body)

    llm = sdk_provider(handler)
    result = parameters.create_chat_completion(llm._client.chat.completions.create, {
        "model": llm._model, "messages": [{"role": "user", "content": "Hello"}],
        "tools": [], "tool_choice": "none",
    }, base_url=llm._base_url)
    assert result.choices[0].message.content == "Ready."
    assert "tools" not in bodies[0] and "tool_choice" not in bodies[0]


def test_openai_tool_images_follow_the_full_result_batch(sdk_provider):
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        return _completion(body)

    llm = sdk_provider(handler)
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}
    readable = {"type": "text", "text": '{"section":"A"}'}
    calls = [
        {"id": ident, "type": "function", "function": {"name": "read_section", "arguments": "{}"}}
        for ident in ("call_first", "call_second")
    ]
    messages = [
        {"role": "user", "content": "Read both sections."},
        {"role": "assistant", "content": None, "tool_calls": calls, "reasoning_content": "Other provider metadata."},
        {"role": "tool", "tool_call_id": "call_first", "name": "read_section", "content": [readable, image]},
        {"role": "tool", "tool_call_id": "call_second", "content": "Second result."},
    ]
    original = deepcopy(messages)
    parameters.create_chat_completion(llm._client.chat.completions.create, {
        "model": llm._model, "messages": messages, "reasoning_effort": "low",
    }, base_url=llm._base_url)

    assert messages == original
    assert bodies[0]["reasoning_effort"] == "low"
    sent = bodies[0]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "tool", "tool", "user"]
    assert sent[1]["tool_calls"] == calls and "reasoning_content" not in sent[1]
    assert sent[2] == {"role": "tool", "tool_call_id": "call_first", "content": [readable]}
    assert sent[3] == messages[-1]
    assert sent[4]["content"][-1] == image
    assert "call_first" in sent[4]["content"][0]["text"]


def test_third_party_tool_result_extensions_are_preserved(sdk_provider):
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        return _completion(body)

    llm = sdk_provider(handler, endpoint=COMPATIBLE_URL)
    messages = [
        {"role": "assistant", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "read_section", "arguments": "{}"}},
        ], "reasoning_content": "Model reasoning."},
        {"role": "tool", "tool_call_id": "call_1", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
        ]},
    ]
    parameters.create_chat_completion(llm._client.chat.completions.create, {
        "model": llm._model, "messages": messages,
    }, base_url=llm._base_url)
    assert bodies[0]["messages"] == messages
