"""LLM thinking_effort → DashScope budget / DeepSeek reasoning_effort."""

import types

from src.providers.llm.openai_compat import (
    OpenAICompatLLM,
    build_thinking_extra_body,
    thinking_create_kwargs,
)


def test_dashscope_low_sets_thinking_budget():
    extra = build_thinking_extra_body(True, True, "low")
    assert extra["enable_thinking"] is True
    assert extra["thinking_budget"] == 1024
    assert "reasoning_effort" not in thinking_create_kwargs(True, True, "low")


def test_dashscope_medium_sets_thinking_budget():
    extra = build_thinking_extra_body(True, True, "medium")
    assert extra["thinking_budget"] == 4096


def test_dashscope_off_has_no_budget():
    extra = build_thinking_extra_body(True, False, "low")
    assert extra == {"enable_thinking": False}


def test_deepseek_low_sets_reasoning_effort():
    kwargs = thinking_create_kwargs(False, True, "low")
    assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
    assert kwargs["reasoning_effort"] == "low"


def test_deepseek_medium_maps_to_high():
    kwargs = thinking_create_kwargs(False, True, "medium")
    assert kwargs["reasoning_effort"] == "high"


def test_deepseek_high_maps_to_max():
    kwargs = thinking_create_kwargs(False, True, "high")
    assert kwargs["reasoning_effort"] == "max"


def test_deepseek_off_has_no_reasoning_effort():
    kwargs = thinking_create_kwargs(False, False, "low")
    assert kwargs == {"extra_body": {"thinking": {"type": "disabled"}}}


def _vision_llm(base_url: str):
    """OpenAICompatLLM with a fake client capturing create() kwargs."""
    from src.config import LLMProviderConfig

    llm = OpenAICompatLLM(
        LLMProviderConfig(
            id="t", name="t", base_url=base_url,
            api_key="sk-test", default_model="vision-m",
        )
    )
    captured: dict = {}

    class _Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            msg = types.SimpleNamespace(content="desc")
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    fake_client = types.SimpleNamespace(
        with_options=lambda **_kw: types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=_Completions())
        )
    )
    llm._client = fake_client
    return llm, captured


def test_describe_image_disables_thinking_dashscope():
    llm, captured = _vision_llm("https://dashscope.aliyuncs.com/compatible-mode/v1")
    llm.describe_image("aGk=", "image/png")
    assert captured["extra_body"] == {"enable_thinking": False}


def test_describe_image_disables_thinking_openai_compat():
    llm, captured = _vision_llm("https://api.deepseek.com/v1")
    llm.describe_image("aGk=", "image/png")
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in captured
