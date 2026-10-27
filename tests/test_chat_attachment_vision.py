import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.providers.llm.openai_compat import OpenAICompatLLM


def llm(endpoint="https://gateway.test/v1", model="model", key="key"):
    result = object.__new__(OpenAICompatLLM)
    result._base_url = endpoint
    result._model = model
    result.supports_vision = False
    result._client = SimpleNamespace(api_key=key)
    return result


def test_automatic_vision_probe_is_semantic_cached_and_endpoint_scoped(tmp_path, monkeypatch):
    from src.chat_attachments import vision
    monkeypatch.setattr(vision, "cache_root", lambda: tmp_path)
    monkeypatch.setattr(vision, "_challenge", lambda: ("data:image/png;base64,AA==", "472961"))
    probe = MagicMock(return_value="472961")
    monkeypatch.setattr(vision, "_ask", probe)
    first = llm()
    assert vision.ensure_chat_vision(first) is True
    assert first.supports_vision is True
    assert vision.ensure_chat_vision(llm()) is True
    assert probe.call_count == 1
    assert vision.ensure_chat_vision(llm(endpoint="https://other.test/v1")) is True
    assert probe.call_count == 2
    assert vision.ensure_chat_vision(llm(key="different")) is True
    assert probe.call_count == 3
    assert "key" not in "".join(p.read_text() for p in tmp_path.glob("*.json"))


def test_probe_errors_and_silent_image_ignoring_do_not_claim_vision(tmp_path, monkeypatch):
    from src.chat_attachments import vision
    monkeypatch.setattr(vision, "cache_root", lambda: tmp_path)
    monkeypatch.setattr(vision, "_challenge", lambda: ("data:image/png;base64,AA==", "472961"))
    monkeypatch.setattr(vision, "_ask", lambda *a: "I cannot see this image")
    assert vision.ensure_chat_vision(llm()) is False
    monkeypatch.setattr(vision, "_ask", MagicMock(side_effect=TimeoutError()))
    assert vision.ensure_chat_vision(llm(model="slow")) is False
    assert vision.ensure_chat_vision(SimpleNamespace(supports_vision=False)) is False
    assert vision.ensure_chat_vision(SimpleNamespace(supports_vision=True)) is True


@pytest.mark.parametrize("visual", [True, False])
def test_stream_prepares_attachments_for_actual_recipient(tmp_path, monkeypatch, visual):
    from src.chatbox.agent import ChatboxAgent
    from src.chat_attachments import service
    from src.db.sessions import SessionStore
    from tests.test_chatbox_agent import _fake_stream
    sessions = SessionStore(str(tmp_path / "sessions.db"))
    sessions.create_session(session_id="chat")
    recipient = MagicMock()
    recipient.supports_vision = visual
    recipient._client.chat.completions.create.return_value = _fake_stream([(None, {"content": "answer"}), ("stop", {})])
    agent = ChatboxAgent(sessions, recipient, None)
    a = agent._attachments.create("chat", "note.txt", b"text")
    service.parse_attachment(agent._attachments, "chat", a.id)
    prepare = MagicMock(wraps=service.prepare_for_model)
    monkeypatch.setattr(service, "prepare_for_model", prepare)
    monkeypatch.setattr(agent, "_has_meetings", lambda *a: False)
    monkeypatch.setattr(agent, "_build_catalog_text", lambda *a, **kw: "")
    monkeypatch.setattr("src.chatbox.agent._build_surface_context", lambda *a: "")
    async def run():
        return [e async for e in agent.chat_stream("chat", "read", mode="direct", attachment_ids=[a.id])]
    assert asyncio.run(run())[-1]["type"] == "done"
    prepare.assert_called_once_with(agent._attachments, "chat", [a.id], is_visual=visual)


def test_probe_uses_compatible_parameters_for_reasoning_endpoints():
    from src.chat_attachments.vision import _ask
    model = llm()
    model._default_max_tokens = 0
    model._thinking_mode = ""
    model._is_dashscope = False
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        if "max_tokens" in kwargs:
            raise ValueError("Unsupported parameter: 'max_tokens'; use 'max_completion_tokens' instead")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="472961"))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    model._client.with_options = lambda **kwargs: client
    assert _ask(model, "data:image/png;base64,AA==") == "472961"
    assert calls[-1]["max_completion_tokens"] >= 128
    assert calls[0]["temperature"] == 1
    assert "extra_body" not in calls[0], "Unknown thinking modes should not add vendor-specific parameters"
