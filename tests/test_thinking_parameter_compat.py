"""Thinking posture probes tolerate explicit completion parameter errors."""

from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError

from src.providers.llm import parameters, thinking

BASE_URL = "https://provider.example.test/v1"
MODEL = "fixture-reasoning-model"


def _bad_request(message: str, *, param: str | None = None) -> BadRequestError:
    body = {"message": message}
    if param is not None:
        body.update(param=param, code="unsupported_parameter")
    response = httpx.Response(
        400,
        request=httpx.Request("POST", f"{BASE_URL}/chat/completions"),
        json={"error": body},
    )
    return BadRequestError(message, response=response, body=body)


def test_probe_retries_token_parameter_before_classifying_thinking(monkeypatch):
    calls, options = [], {}
    responses = iter(
        (
            _bad_request(
                "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead.",
                param="max_tokens",
            ),
            _bad_request("Unsupported parameter: 'thinking'.", param="thinking"),
        )
    )

    def send(**kwargs):
        calls.append(kwargs)
        raise next(responses)

    def client(**kwargs):
        options.update(kwargs)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=send)))

    monkeypatch.setattr(thinking, "OpenAI", client)
    monkeypatch.setattr(thinking, "_LEARNED_MODES", {})
    monkeypatch.setattr(parameters, "_PARAMETER_RULES", {})

    assert thinking.probe_thinking_mode(BASE_URL, "fixture-key", MODEL) == "none"
    assert thinking.learned_thinking_mode(BASE_URL, MODEL) == "none"
    assert len(calls) == 2
    assert calls[0]["max_tokens"] == calls[1]["max_completion_tokens"] == 1
    assert "max_tokens" not in calls[1]
    assert calls[0]["extra_body"] == calls[1]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert options["timeout"].connect == 6 and options["timeout"].read == 15


def test_unsupported_thinking_message_does_not_need_structured_param():
    assert thinking.is_thinking_param_unsupported(_bad_request("Unsupported parameter: thinking"))
    assert not thinking.is_thinking_param_unsupported(
        _bad_request(
            "Unsupported parameter: 'temperature' for a thinking model.", param="temperature"
        )
    )


@pytest.mark.parametrize("param", [None, "tool_choice"])
def test_tool_choice_rejection_does_not_reject_thinking(param):
    assert not thinking.is_thinking_param_unsupported(
        _bad_request("tool_choice is not allowed in thinking mode", param=param)
    )
