"""Parameter negotiation uses API errors, independent of model aliases."""

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError

from src.config import LLMProviderConfig
from src.providers.llm.openai_compat import OpenAICompatLLM


def rejection(param, message, *, status=400, code="unsupported_parameter"):
    error = {"message": message, "param": param, "code": code}
    return BadRequestError(
        message,
        response=httpx.Response(status, request=httpx.Request("POST", "https://gateway.example.test/v1/chat/completions")),
        body=error,
    )


TEMPERATURE_ERROR = rejection(
    "temperature",
    "Unsupported value: 'temperature' does not support 0.1 with this model. Only the default (1) value is supported.",
    code="unsupported_value",
)
TOKEN_ERROR = rejection(
    "max_tokens",
    "Unsupported parameter: 'max_tokens'; use 'max_completion_tokens' instead.",
)


@pytest.fixture(autouse=True)
def isolate_rules(monkeypatch):
    try:
        from src.providers.llm import parameters
    except ImportError:
        return
    monkeypatch.setattr(parameters, "_PARAMETER_RULES", {})


def provider(send, *, endpoint="https://gateway.example.test/v1", model="reasoning-test"):
    llm = OpenAICompatLLM(LLMProviderConfig(
        base_url=endpoint, api_key="test-key", default_model=model, thinking_mode="none",
    ))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=send)))
    client.with_options = lambda **kwargs: client
    llm._client = client
    return llm


def answer(kwargs):
    if kwargs.get("stream"):
        return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="Answer."))])])
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Answer."))])


@pytest.mark.parametrize("method", ["generate", "generate_stream", "generate_stream_tagged", "describe_image"])
@pytest.mark.parametrize("token_first", [True, False])
def test_model_rejections_adapt_all_generation_paths(method, token_first):
    calls = []

    def send(**kwargs):
        calls.append(kwargs)
        if token_first and "max_tokens" in kwargs:
            raise TOKEN_ERROR
        if "temperature" in kwargs:
            raise TEMPERATURE_ERROR
        if "max_tokens" in kwargs:
            raise TOKEN_ERROR
        return answer(kwargs)

    llm = provider(send)
    if method == "describe_image":
        result = llm.describe_image("AA==", prompt="Describe the shape.")
        expected_tokens = 1024
    else:
        result = getattr(llm, method)("Describe the shape.", max_tokens=240, response_format={"type": "json_object"})
        if method != "generate":
            result = list(result)
        expected_tokens = 240
    assert result
    assert len(calls) == 3
    assert "temperature" not in calls[-1]
    assert "max_tokens" not in calls[-1]
    assert calls[-1]["max_completion_tokens"] == expected_tokens
    assert calls[-1]["messages"] == calls[0]["messages"]
    if method != "describe_image":
        assert calls[-1]["response_format"] == {"type": "json_object"}


def test_temperature_rule_survives_fresh_instances_and_is_endpoint_model_scoped():
    calls = []

    def send(**kwargs):
        calls.append(kwargs)
        if "temperature" in kwargs:
            raise TEMPERATURE_ERROR
        return answer(kwargs)

    provider(send).generate("First request")
    provider(send, endpoint="https://gateway.example.test/v1/").generate("Next request")
    assert len(calls) == 3
    assert "temperature" not in calls[-1]
    for overrides in ({"endpoint": "https://another.example.test/v1"}, {"model": "other-model"}):
        fresh = []
        provider(lambda _fresh=fresh, **kw: _fresh.append(kw) or answer(kw), **overrides).generate("Independent request", temperature=0)
        assert fresh[0]["temperature"] == 0


@pytest.mark.parametrize("error", [
    rejection("temperature", "temperature must be between 0 and 2", code="invalid_value"),
    rejection("temperature", "Temperature 1.5 is not supported; use a value between 0 and 1", code="unsupported_value"),
    rejection("temperature", "Temperature 1.5 is not supported; use a value between 0 and 1", code="invalid_value"),
    rejection("temperature", "Temperature 1.5 is not supported; use a value between 0 and 1", code=None),
    rejection(None, "Unsupported parameter: 'frequency_penalty'; valid options include temperature"),
    rejection("tools", "Unsupported tools; request included temperature", code="unsupported_parameter"),
    rejection("temperature", "temperature is not supported", status=401),
    rejection("temperature", "temperature is not supported", status=429),
    rejection("temperature", "temperature is not supported", status=500),
])
def test_unrelated_or_transient_errors_propagate_without_parameter_retry(error):
    calls = []

    def send(**kwargs):
        calls.append(kwargs)
        raise error

    with pytest.raises(BadRequestError) as caught:
        provider(send).generate("Request")
    assert caught.value is error
    assert len(calls) == 1


def test_repeated_rejection_does_not_loop_forever():
    calls = []

    def send(**kwargs):
        calls.append(kwargs)
        raise TEMPERATURE_ERROR

    with pytest.raises(BadRequestError):
        provider(send).generate("Request")
    assert len(calls) == 2


def test_errors_after_first_stream_token_do_not_replay_output():
    calls = []

    def send(**kwargs):
        calls.append(kwargs)
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="Partial"))])
        raise TEMPERATURE_ERROR

    stream = provider(send).generate_stream("Request")
    assert next(stream) == "Partial"
    with pytest.raises(BadRequestError):
        next(stream)
    assert len(calls) == 1


def test_batch_uses_learned_rules_and_preserves_explicit_zero():
    def send(**kwargs):
        if "temperature" in kwargs:
            raise TEMPERATURE_ERROR
        if "max_tokens" in kwargs:
            raise TOKEN_ERROR
        return answer(kwargs)

    provider(send).generate("Learn parameters", max_tokens=240)
    llm = provider(send)
    bodies = []

    def upload(file, purpose):
        bodies.extend(json.loads(line)["body"] for line in file.read().decode().splitlines())
        return SimpleNamespace(id="batch-input")

    llm._client.files = SimpleNamespace(create=upload)
    llm._client.batches = SimpleNamespace(create=lambda **kw: SimpleNamespace(id="batch-task"))
    assert llm.batch_submit([{"prompt": "Draft", "temperature": 0, "max_tokens": 240}]) == "batch-task"
    assert "temperature" not in bodies[0]
    assert bodies[0]["max_completion_tokens"] == 240
    llm._model = "ordinary-model"
    llm.batch_submit([{"prompt": "Draft", "temperature": 0}])
    assert bodies[1]["temperature"] == 0


def test_optional_reasoning_modes_keep_independent_temperature_support():
    from src.providers.llm.parameters import create_chat_completion

    calls = []
    original = {"model": "switchable-model", "temperature": 0, "reasoning_effort": "high"}

    def send(**kwargs):
        calls.append(kwargs)
        if kwargs["reasoning_effort"] == "high" and "temperature" in kwargs:
            raise TEMPERATURE_ERROR
        return answer(kwargs)

    create_chat_completion(send, original, base_url="https://gateway.example.test/v1")
    assert original["temperature"] == 0
    create_chat_completion(send, {**original, "reasoning_effort": "none"}, base_url="https://gateway.example.test/v1")
    assert calls[-1]["temperature"] == 0
    create_chat_completion(send, original, base_url="https://gateway.example.test/v1")
    assert "temperature" not in calls[-1]
    assert len(calls) == 4


@pytest.mark.parametrize("body", [
    {"error": {"param": "temperature", "code": "unsupported_parameter", "message": "Unsupported parameter."}},
    {"message": "Unsupported parameter: 'temperature' is not supported with this model."},
])
def test_sdk_error_body_variants_use_the_default_temperature(body):
    error = BadRequestError("Rejected request", response=TEMPERATURE_ERROR.response, body=body)
    calls = []

    def send(**kwargs):
        calls.append(kwargs)
        if "temperature" in kwargs:
            raise error
        return answer(kwargs)

    assert provider(send).generate("Request") == "Answer."
    assert len(calls) == 2
    assert "temperature" not in calls[-1]


def test_connection_test_uses_the_same_parameter_negotiation(monkeypatch):
    import asyncio

    from src.api.routes import config as routes
    from src.providers import llm as factory

    def send(**kwargs):
        if "temperature" in kwargs:
            raise TEMPERATURE_ERROR
        return answer(kwargs)

    config = LLMProviderConfig(id="test-provider", name="Test provider")
    monkeypatch.setattr(routes, "get_config", lambda: SimpleNamespace(llm=SimpleNamespace(providers=[config])))
    monkeypatch.setattr(factory, "create_llm_provider", lambda config: provider(send))
    assert asyncio.run(routes.test_llm_provider("test-provider"))["success"] is True


def test_temperature_negotiation_composes_with_unsupported_thinking_parameter(monkeypatch):
    from src.providers.llm import thinking

    monkeypatch.setattr(thinking, "_LEARNED_MODES", {})
    calls = []

    def send(**kwargs):
        calls.append(kwargs)
        if "temperature" in kwargs:
            raise TEMPERATURE_ERROR
        if "thinking" in (kwargs.get("extra_body") or {}):
            raise rejection("thinking", "Unsupported parameter: 'thinking'.")
        return answer(kwargs)

    llm = provider(send)
    llm._thinking_mode = ""
    assert llm.generate("Request", thinking=False) == "Answer."
    assert "thinking" not in (calls[-1].get("extra_body") or {})
    assert llm._thinking_mode == "none"


def test_thinking_budgets_keep_independent_temperature_capabilities():
    from src.providers.llm.parameters import create_chat_completion

    calls = []
    def send(**kwargs):
        calls.append(kwargs)
        if kwargs["extra_body"]["thinking_budget"] == 2048 and "temperature" in kwargs:
            raise TEMPERATURE_ERROR
        return answer(kwargs)

    base = {"model": "budget-model", "temperature": 0}
    for budget in (2048, 512):
        create_chat_completion(send, {**base, "extra_body": {"enable_thinking": True, "thinking_budget": budget}})
    assert calls[-1]["temperature"] == 0


def test_always_thinking_rejection_still_uses_existing_always_mode(monkeypatch):
    from src.providers.llm import thinking

    monkeypatch.setattr(thinking, "_LEARNED_MODES", {})
    calls = []
    def send(**kwargs):
        calls.append(kwargs)
        if "extra_body" in kwargs:
            raise rejection(None, "Turning off thinking is not allowed for always-thinking models", code=None)
        return answer(kwargs)

    llm = provider(send)
    llm._thinking_mode = ""
    assert llm.generate("Request", thinking=False) == "Answer."
    assert calls[-1]["reasoning_effort"] == "low"
    assert llm._thinking_mode == "always"
