"""LLM thinking_effort → DashScope budget / DeepSeek reasoning_effort."""

from src.providers.llm.openai_compat import (
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
