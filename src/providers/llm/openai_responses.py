"""Responses transport for language model generation and Chat tool calls."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from types import SimpleNamespace

_GPT6_MODEL = re.compile(r"^gpt-6(?:[.-]|$)", re.I)
_REASONING_MODEL = re.compile(r"^(?:gpt-[56]|o\d+)(?:[.-]|$)", re.I)


def _input_content(content):
    if not isinstance(content, list):
        return content or ""
    parts = []
    for part in content:
        if part["type"] == "text":
            parts.append({"type": "input_text", "text": part["text"]})
        elif part["type"] == "image_url":
            image = part["image_url"]
            parts.append({
                "type": "input_image",
                "image_url": image["url"],
                "detail": image.get("detail") or "auto",
            })
        else:
            raise ValueError(f"Unsupported Responses input content: {part['type']}")
    return parts or ""


def _input_items(messages: list[dict]) -> list[dict]:
    items = []
    for message in messages:
        role = message["role"]
        content = _input_content(message.get("content"))
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": message["tool_call_id"], "output": content})
            continue
        calls = message.get("tool_calls") or []
        if content or not calls:
            items.append({"role": role, "content": content})
        for call in calls:
            items.append({
                "type": "function_call", "call_id": call["id"],
                "name": call["function"]["name"], "arguments": call["function"]["arguments"],
            })
    return items


def prepare_responses_kwargs(kwargs: dict, *, thinking: bool = False, effort: str | None = None) -> dict:
    model = kwargs["model"]
    payload = {"model": model, "input": _input_items(kwargs["messages"]), "store": False}

    reasoning_effort = kwargs.get("reasoning_effort")
    if not reasoning_effort and _GPT6_MODEL.match(model):
        reasoning_effort = (effort or "medium") if thinking else "low"
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}
    reasoning = reasoning_effort or _REASONING_MODEL.match(model)
    if reasoning and reasoning_effort != "none":
        payload["include"] = ["reasoning.encrypted_content"]
    elif kwargs.get("temperature") is not None:
        payload["temperature"] = kwargs["temperature"]

    if tools := kwargs.get("tools"):
        # Keep optional function arguments in the schema validated by SinkDuce.
        payload["tools"] = [{"type": "function", "strict": False, **tool["function"]} for tool in tools]
        choice = kwargs.get("tool_choice", "auto")
        if isinstance(choice, dict):
            choice = {"type": "function", "name": choice["function"]["name"]}
        payload["tool_choice"] = choice
    if response_format := kwargs.get("response_format"):
        if response_format["type"] == "json_schema":
            response_format = {"type": "json_schema", **response_format["json_schema"]}
        payload["text"] = {"format": response_format}
    limit = kwargs.get("max_completion_tokens") or kwargs.get("max_tokens")
    if limit and limit > 0:
        payload["max_output_tokens"] = limit
    return payload


def responses_output_text(output: list[dict]) -> str:
    return "".join(
        part.get("text") or part.get("refusal") or ""
        for item in output if item["type"] == "message"
        for part in item["content"] if part["type"] in {"output_text", "refusal"}
    )


def _fingerprint(items: list[dict]) -> bytes:
    return hashlib.sha256(json.dumps(items, sort_keys=True, ensure_ascii=False).encode()).digest()


def _call_signatures(items: list[dict]) -> dict[str, tuple[str, str]]:
    return {
        item["call_id"]: (item["name"], item["arguments"])
        for item in items if item.get("type") == "function_call"
    }


@dataclass
class _ToolRound:
    prefix_size: int
    prefix_hash: bytes
    output: list[dict]
    calls: dict[str, tuple[str, str]]

    def replay(self, items: list[dict]) -> list[dict]:
        # Reuse encrypted reasoning only when the authorized input prefix and
        # the whole tool batch are still present. Retrying with trimmed context
        # or changing tool selection must not reintroduce removed material.
        end = self.prefix_size + 2 * len(self.calls)
        batch = items[self.prefix_size:end]
        if len(items) < end or _call_signatures(batch) != self.calls:
            return items
        results = {item["call_id"]: item for item in batch if item.get("type") == "function_call_output"}
        if results.keys() != self.calls.keys() or _fingerprint(items[:self.prefix_size]) != self.prefix_hash:
            return items
        return [
            *items[:self.prefix_size], *self.output,
            *(results[call_id] for call_id in self.calls), *items[end:],
        ]


class OpenAIResponsesChat:
    """Keep Responses state in a single turn, using the agent's existing tool protocol."""

    def __init__(self, send):
        self._send = send
        self._rounds: list[_ToolRound] = []

    @classmethod
    def for_provider(cls, llm) -> OpenAIResponsesChat | None:
        if getattr(llm, "_api_mode", None) == "responses":
            return cls(llm._client.responses.create)
        return None

    def create(self, kwargs: dict, *, thinking: bool = False, effort: str | None = None):
        payload = prepare_responses_kwargs(kwargs, thinking=thinking, effort=effort)
        items = payload["input"]
        for round_ in self._rounds:
            items = round_.replay(items)
        payload["input"] = items
        if kwargs.get("stream"):
            stream = self._send(**payload, stream=True)
            return self._stream(stream, items)
        return self._complete(self._send(**payload), items)

    def _complete(self, response, items):
        incomplete_reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
        text_truncated = (
            response.status == "incomplete" and incomplete_reason == "max_output_tokens"
            and all(item.type != "function_call" for item in response.output)
        )
        if response.status != "completed" and not text_truncated:
            error = getattr(response, "error", None)
            detail = getattr(error, "message", None) or incomplete_reason
            raise RuntimeError(f"OpenAI Responses {response.status}: {detail or 'response did not complete'}")
        if any(
            item.type == "function_call" and getattr(item, "status", None) not in (None, "completed")
            for item in response.output
        ):
            raise RuntimeError("OpenAI Responses returned an incomplete function call")

        output = [
            item.model_dump(mode="json", exclude_none=True)
            for item in response.output
            if item.type in {"message", "function_call"}
            or (item.type == "reasoning" and getattr(item, "encrypted_content", None))
        ]
        calls = _call_signatures(output)
        if calls:
            self._rounds.append(_ToolRound(len(items), _fingerprint(items), output, calls))
        tool_calls = [
            SimpleNamespace(
                index=index, id=call_id, type="function",
                function=SimpleNamespace(name=name, arguments=arguments),
            )
            for index, (call_id, (name, arguments)) in enumerate(calls.items())
        ]
        message = SimpleNamespace(content=responses_output_text(output), tool_calls=tool_calls)
        finish_reason = "length" if text_truncated else "tool_calls" if tool_calls else "stop"
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)])

    def _stream(self, stream, items):
        def chunk(content="", tool_calls=None, finish_reason=None):
            delta = SimpleNamespace(content=content, tool_calls=tool_calls)
            return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)])

        seen_text = False
        with stream:
            for event in stream:
                if event.type in {"response.output_text.delta", "response.refusal.delta"}:
                    if event.delta:
                        seen_text = True
                        yield chunk(content=event.delta)
                elif event.type in {"response.completed", "response.failed", "response.incomplete", "response.cancelled"}:
                    choice = self._complete(event.response, items).choices[0]
                    yield chunk(
                        content="" if seen_text else choice.message.content,
                        tool_calls=choice.message.tool_calls,
                        finish_reason=choice.finish_reason,
                    )
                    return
                elif event.type == "error":
                    raise RuntimeError(f"OpenAI Responses stream: {event.message}")
        raise RuntimeError("OpenAI Responses stream ended before completion")
