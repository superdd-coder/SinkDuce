"""Main Chat presentation guidance reaches requests without changing other modes."""

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.chatbox.agent import ChatboxAgent, _force_generate_answer
from src.config import AppConfig
from src import prompts
from tests.test_chatbox_agent import (
    store, mock_llm, mock_agentic, _fake_llm_response, _fake_stream, _tc_delta,
)


def _main_policy():
    policy = getattr(prompts, "MAIN_CHAT_FORMATTING_RULES", None)
    assert policy, "Main Chat needs a centralized presentation policy"
    return policy


def _main_force_instruction():
    instruction = getattr(prompts, "MAIN_CHAT_FORCE_ANSWER_USER", None)
    assert instruction, "Main Chat needs a centralized question-scoped forced-answer instruction"
    assert "comprehensive" not in instruction.lower()
    assert "all the search results" not in instruction.lower()
    assert re.search(r"current (?:question|request)", instruction, re.I)
    assert re.search(r"(?:concise|focused|brief).*?(?:answer|reply)", instruction, re.I | re.S)
    assert re.search(r"relevant (?:evidence|results)", instruction, re.I)
    assert re.search(r"requested (?:depth|detail)", instruction, re.I)
    assert "citation" in instruction.lower()
    return instruction


def _assert_concise_svg_policy(system):
    policy = _main_policy()
    assert system.count(policy) == 1
    assert system.count(prompts.KNOWLEDGE_ANSWER_EVIDENCE_RULES) == 1
    assert re.search(r"(?:lead|start|begin) with.*?(?:answer|conclusion|recommendation)", policy, re.I)
    assert re.search(r"(?:straightforward|simple).*?(?:concise|brief)", policy, re.I | re.S)
    assert re.search(r"fenced code block.*?svg", policy, re.I | re.S)
    assert "xmlns" in policy and "viewBox" in policy
    assert "tspan" in policy and "text" in policy.lower()
    assert re.search(r"#[0-9a-f]{6}\b", policy, re.I), "SVGs need a concrete brand palette"
    assert re.search(r"256\s*KiB", policy, re.I)


def test_main_defaults_to_svg_for_supported_cross_entity_paths_with_prose_exceptions():
    visual = _main_policy().split("Visual communication:", 1)[1]
    bullets = [" ".join(bullet.split()) for bullet in re.split(r"\n\s*(?=- )", visual.strip())]
    trigger = next((bullet for bullet in bullets if re.search(r"\bdefault\b.*?\bSVG\b|\bSVG\b.*?\bdefault\b", bullet, re.I)), "")
    assert trigger, "Supported participation paths need a default SVG trigger, beyond optional visual clarity"
    for meaning, pattern in [
        ("the question traces a role's participation, connection or movement path", r"\b(?:role|actor|participant)\b.*?\b(?:path|participat\w*|connect\w*|mov\w*)\b"),
        ("the path crosses entities or layers", r"\b(?:across|between|different|multiple)\b.*?\b(?:entit\w*|actor\w*|organization\w*|subject\w*)\b.*?\b(?:layer|level|tier)\w*\b"),
        ("the evidence supports the structure", r"(?:\bevidence\b.*?\bsupport\w*\b|\b(?:reliable|supported)\b).*?\b(?:entit\w*|node\w*)\b.*?\b(?:link|connect|relation)\w*\b"),
        ("the default is a compact overview", r"\bcompact\b.*?\b(?:SVG|overview)\b"),
    ]:
        assert re.search(pattern, trigger, re.I), meaning

    normalized = " ".join(visual.split())
    assert re.search(r"(?:replac\w*|in place of|instead of).*?(?:repeat\w*|repetit\w*|duplicat\w*).*?(?:structur\w*|explanation\w*|prose)", normalized, re.I), "The diagram should replace repetitive structural explanation"
    assert re.search(r"\b(?:isolated|single)\s+(?:fact|factual)\w*\b", normalized, re.I), "An isolated fact should remain prose"
    assert re.search(r"(?:insufficient|missing|incomplete|limited|lack\w*|without).*?(?:structur\w*|evidence)|(?:structur\w*|evidence).*?(?:insufficient|missing|incomplete|unavailable)", normalized, re.I), "Missing structural evidence should keep the answer textual"
    assert re.search(r"\b(?:user|request)\w*\b.*?\b(?:another|different|other|specific)\b.*?\bformat\b", normalized, re.I), "The user's requested output format takes priority"


@pytest.mark.parametrize("streaming", [False, True])
def test_quick_chat_receives_the_same_svg_policy_as_main_chat(store, mock_llm, mock_agentic, streaming):
    agent = ChatboxAgent(store, mock_llm, mock_agentic)
    session = store.create_session(session_id="quick_svg", collections=["col-test"])
    create = mock_llm._client.chat.completions.create
    create.return_value = _fake_stream([("stop", {"content": "Diagram"})]) if streaming else _fake_llm_response(content="Diagram")
    with patch("src.config.get_config", return_value=AppConfig()), \
         patch.object(agent, "_has_meetings", return_value=False):
        if streaming:
            async def collect():
                return [e async for e in agent.chat_stream(session.id, "Draw the structure", mode="direct", thinking=False)]
            asyncio.run(collect())
        else:
            agent.chat(session.id, "Draw the structure", mode="direct")

    system = create.call_args.kwargs["messages"][0]["content"]
    svg_policy = "Visual communication:" + _main_policy().split("Visual communication:", 1)[1]
    assert system.count(svg_policy) == 1
    assert "Main Chat response policy:" not in system
    assert "256 KiB" in system
    assert "THIS collection" in system


@pytest.mark.parametrize("quick", [False, True])
def test_saving_a_previous_answer_keeps_svg_source_available_and_requires_preserving_it(store, mock_llm, mock_agentic, quick):
    agent = ChatboxAgent(store, mock_llm, mock_agentic)
    session = store.create_session(session_id="quick_save_svg" if quick else "save_svg", collections=["col-test"])
    drawing = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100"><text>Entity A → Entity B</text></svg>'
    answer = "Participation route.\n\n```svg\n" + drawing + "\n```"
    store.add_message(session.id, "user", "Explain the participation route")
    store.add_message(session.id, "assistant", answer)
    store.add_message(session.id, "user", "Save the answer as a Note")
    with patch("src.config.get_config", return_value=AppConfig()), \
         patch.object(agent, "_has_meetings", return_value=False), \
         patch.object(agent, "_build_catalog_text", return_value=""):
        mode = "direct" if quick else "agentic"
        _, prompt, catalog = agent._resolve_tools_and_prompt(mode, session.id, ["col-test"], has_meetings=False)
        messages = agent._build_messages(session.id, "Save the answer as a Note", collections=["col-test"], mode=mode, system_prompt=prompt, catalog_text=catalog)
    assert any(m["role"] == "assistant" and m["content"] == answer for m in messages)
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    assert re.search(r"sav\w*.*?(?:previous|prior|earlier).*?answer.*?Note.*?SVG", system, re.I | re.S)
    assert re.search(r"(?:preserve|copy).*?(?:verbatim|unchanged).*?(?:fence|source)|(?:preserve|copy).*?(?:fence|source).*?(?:verbatim|unchanged)", system, re.I | re.S)
    assert "text-only" in system and "summary" in system


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("custom_role", [None, "Custom analyst role."])
def test_main_formatting_reaches_generation_request(
    store, mock_llm, mock_agentic, streaming, custom_role,
):
    kwargs = {"system_prompt": custom_role} if custom_role else {}
    agent = ChatboxAgent(store, mock_llm, mock_agentic, **kwargs)
    session = store.create_session(title="Main Chat formatting")
    create = mock_llm._client.chat.completions.create
    create.return_value = (
        _fake_stream([("stop", {"content": "Supported answer."})])
        if streaming else _fake_llm_response(content="Supported answer.")
    )
    with patch("src.config.get_config", return_value=AppConfig()), \
         patch("src.chatbox.agent._build_surface_context", return_value=None), \
         patch.object(agent, "_has_meetings", return_value=False), \
         patch.object(agent, "_build_catalog_text", return_value=""):
        if streaming:
            async def collect():
                return [event async for event in agent.chat_stream(session.id, "Explain the result.")]
            asyncio.run(collect())
        else:
            agent.chat(session.id, "Explain the result.")

    system = create.call_args.kwargs["messages"][0]["content"]
    policy = _main_policy()
    _assert_concise_svg_policy(system)
    assert system.startswith(custom_role or prompts.DEFAULT_SYSTEM_PROMPT)
    assert create.call_count == 1
    assert all(message.role != "system" for message in store.get_messages(session.id))


@pytest.mark.parametrize("session_id,mode", [
    ("plain_direct", "direct"),
    ("quick_formatting", "direct"),
    ("meeting_formatting", "direct"),
    ("group_formatting", "agentic"),
])
def test_other_chat_modes_keep_their_base_role_with_citation_and_report_guidance(
    store, mock_llm, mock_agentic, session_id, mode,
):
    agent = ChatboxAgent(store, mock_llm, mock_agentic)
    store.create_session(session_id=session_id, collections=["col-test"])
    create = mock_llm._client.chat.completions.create
    create.return_value = _fake_llm_response(content="Supported answer.")
    with patch("src.config.get_config", return_value=AppConfig()), \
         patch("src.chatbox.agent._build_surface_context", return_value=None), \
         patch.object(agent, "_has_meetings", return_value=False), \
         patch.object(agent, "_build_catalog_text", return_value=""):
        agent.chat(session_id, "Hello", mode=mode)

    system = create.call_args.kwargs["messages"][0]["content"]
    if session_id.startswith("meeting_"):
        expected = prompts.MEETING_CHAT_SYSTEM_PROMPT
    elif session_id.startswith("group_"):
        expected = prompts.MEETING_GROUP_CHAT_SYSTEM_PROMPT
    else:
        expected = (prompts.QUICK_CHAT_SYSTEM_PROMPT % {"collection_name": "col-test"}) + "\n\n" + prompts.KNOWLEDGE_ANSWER_EVIDENCE_RULES
    for guidance in (prompts.CHAT_CITATION_RULES, prompts.CHAT_REPORT_RULES):
        assert system.count(guidance) == 1
        system = system.replace("\n\n" + guidance, "")
    assert system == expected
    assert create.call_count == 1


def test_custom_turn_prompt_keeps_formatting_policy_once_when_rebuilt(store, mock_llm, mock_agentic):
    agent = ChatboxAgent(store, mock_llm, mock_agentic, system_prompt="Constructor role.")
    session = store.create_session(title="Custom turn formatting")
    first = agent._build_messages(session.id, "Question", system_prompt="Turn role.", catalog_text="")
    policy = _main_policy()
    assert first[0]["content"].startswith("Turn role.")
    _assert_concise_svg_policy(first[0]["content"])
    second = agent._build_messages(session.id, "Question", system_prompt=first[0]["content"], catalog_text="")
    assert second[0]["content"] == first[0]["content"]
    assert agent._system_prompt == "Constructor role."


@pytest.mark.parametrize("session_id", ["main_forced", "quick_forced", "meeting_forced", "group_forced"])
def test_provider_force_answer_carries_only_existing_main_formatting(
    store, mock_llm, mock_agentic, session_id,
):
    agent = ChatboxAgent(store, mock_llm, mock_agentic)
    store.create_session(session_id=session_id)
    messages = agent._build_messages(session_id, "Question", system_prompt="Existing role.", catalog_text="")
    model = SimpleNamespace(_client=None, generate=Mock(return_value="Supported answer."))
    assert _force_generate_answer(model, messages) == "Supported answer."
    actual = model.generate.call_args.kwargs["system"]
    policy = _main_policy()
    if session_id.startswith("main_"):
        _assert_concise_svg_policy(actual)
        assert model.generate.call_args.args[0] == _main_force_instruction()
    else:
        assert policy not in actual
        expected = prompts.CHAT_FORCE_ANSWER_SYSTEM
        if session_id.startswith("quick_"):
            expected += "\n\n" + prompts.KNOWLEDGE_ANSWER_EVIDENCE_RULES
        assert actual == expected
        assert model.generate.call_args.args[0] == prompts.CHAT_FORCE_ANSWER_USER
    assert model.generate.call_count == 1


def test_client_force_answer_retains_main_formatting_messages(store, mock_llm, mock_agentic):
    agent = ChatboxAgent(store, mock_llm, mock_agentic)
    session = store.create_session(title="Forced client formatting")
    messages = agent._build_messages(session.id, "Question", system_prompt="Custom role.", catalog_text="")
    create = mock_llm._client.chat.completions.create
    create.return_value = _fake_llm_response(content="Supported answer.")
    assert _force_generate_answer(mock_llm, messages) == "Supported answer."
    outbound = create.call_args.kwargs["messages"]
    assert outbound[:len(messages)] == messages
    _assert_concise_svg_policy(outbound[0]["content"])
    assert outbound[-1]["content"] == _main_force_instruction()
    assert create.call_count == 1


@pytest.mark.parametrize("with_client", [False, True])
@pytest.mark.parametrize("custom_role", [None, "Custom analyst role."])
def test_main_force_answer_uses_its_concise_instruction_in_both_provider_paths(
    store, mock_llm, mock_agentic, with_client, custom_role,
):
    kwargs = {"system_prompt": custom_role} if custom_role else {}
    agent = ChatboxAgent(store, mock_llm, mock_agentic, **kwargs)
    session = store.create_session(title="Main concise forced answer")
    messages = agent._build_messages(session.id, "Answer this specific question.", catalog_text="")
    if with_client:
        model = mock_llm
        model._client.chat.completions.create.return_value = _fake_llm_response(content="Focused answer.")
    else:
        model = SimpleNamespace(_client=None, generate=Mock(return_value="Focused answer."))

    assert _force_generate_answer(model, messages) == "Focused answer."
    if with_client:
        outbound = model._client.chat.completions.create.call_args.kwargs["messages"]
        instruction = outbound[-1]["content"]
        assert outbound[-1]["role"] == "user"
        assert outbound[:-1] == messages
        system = "\n".join(m["content"] for m in outbound if m["role"] == "system")
    else:
        instruction = model.generate.call_args.args[0]
        system = model.generate.call_args.kwargs["system"]
    assert "comprehensive" not in instruction.lower(), "Main Chat must not be told to expand across all results"
    assert instruction == _main_force_instruction()
    _assert_concise_svg_policy(system)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("custom_role", [None, "Custom analyst role."])
def test_actual_main_tool_limit_fallback_receives_concise_svg_policy(
    store, mock_llm, mock_agentic, streaming, custom_role,
):
    kwargs = {"system_prompt": custom_role} if custom_role else {}
    agent = ChatboxAgent(store, mock_llm, mock_agentic, **kwargs)
    session = store.create_session(title="Main forced loop")
    create = mock_llm._client.chat.completions.create
    arguments = '{"raw_query": "specific question", "decompose": false}'
    first_response = (
        _fake_stream([("tool_calls", {"tool_calls": [
            _tc_delta(0, "search", "search_knowledge_base", arguments),
        ]})])
        if streaming else _fake_llm_response(tool_calls=[{
            "id": "search", "type": "function", "function": {
                "name": "search_knowledge_base", "arguments": arguments,
            },
        }])
    )
    create.side_effect = [first_response, _fake_llm_response(content="Focused answer.")]
    # A mocked retrieval fills the single tool round; the actual chat entry
    # point must then send the scoped fallback instruction to the provider.
    with patch("src.config.get_config", return_value=AppConfig()), \
         patch("src.chatbox.agent._build_surface_context", return_value=None), \
         patch("src.chatbox.agent._MAX_TOOL_ROUNDS", 1), \
         patch.object(agent, "_has_meetings", return_value=False), \
         patch.object(agent, "_build_catalog_text", return_value=""):
        if streaming:
            async def collect():
                return [event async for event in agent.chat_stream(session.id, "Answer this specific question.")]
            events = asyncio.run(collect())
            assert events[-1]["type"] == "done"
        else:
            assert agent.chat(session.id, "Answer this specific question.").answer == "Focused answer."

    outbound = create.call_args.kwargs["messages"]
    assert create.call_count == 2
    assert mock_agentic.run.call_count == 1
    assert outbound[0]["content"].startswith(custom_role or prompts.DEFAULT_SYSTEM_PROMPT)
    _assert_concise_svg_policy(outbound[0]["content"])
    assert outbound[-1] == {"role": "user", "content": _main_force_instruction()}
    assert all(message.role != "system" for message in store.get_messages(session.id))


@pytest.mark.parametrize("session_id,mode", [
    ("plain_direct", "direct"),
    ("quick_forced", "direct"),
    ("meeting_forced", "direct"),
    ("group_forced", "agentic"),
])
@pytest.mark.parametrize("with_client", [False, True])
def test_other_modes_force_answer_preserves_legacy_instruction(
    store, mock_llm, mock_agentic, session_id, mode, with_client,
):
    agent = ChatboxAgent(store, mock_llm, mock_agentic)
    store.create_session(session_id=session_id, collections=["col-test"])
    messages = agent._build_messages(session_id, "Question", mode=mode, catalog_text="")
    if with_client:
        model = mock_llm
        model._client.chat.completions.create.return_value = _fake_llm_response(content="Supported answer.")
    else:
        model = SimpleNamespace(_client=None, generate=Mock(return_value="Supported answer."))
    assert _force_generate_answer(model, messages) == "Supported answer."
    if with_client:
        outbound = model._client.chat.completions.create.call_args.kwargs["messages"]
        legacy = getattr(prompts, "CHAT_FORCE_ANSWER_WITH_TOOLS_USER", None)
        assert legacy, "the existing tools-based fallback instruction must stay centralized"
        assert outbound[-1] == {"role": "user", "content": legacy}
        assert "comprehensive" in legacy.lower()
        assert "all the search results" in legacy.lower()
        system = "\n".join(m["content"] for m in outbound if m["role"] == "system")
    else:
        assert model.generate.call_args.args[0] == prompts.CHAT_FORCE_ANSWER_USER
        system = model.generate.call_args.kwargs["system"]
    assert _main_policy() not in system


@pytest.mark.parametrize("streaming", [False, True])
def test_custom_main_system_with_existing_policy_is_not_appended_twice(
    store, mock_llm, mock_agentic, streaming,
):
    custom = "Custom analyst role.\n\n" + _main_policy()
    agent = ChatboxAgent(store, mock_llm, mock_agentic, system_prompt=custom)
    session = store.create_session(title="Main policy already supplied")
    create = mock_llm._client.chat.completions.create
    create.return_value = (
        _fake_stream([("stop", {"content": "Focused answer."})])
        if streaming else _fake_llm_response(content="Focused answer.")
    )
    with patch("src.config.get_config", return_value=AppConfig()), \
         patch("src.chatbox.agent._build_surface_context", return_value=None), \
         patch.object(agent, "_has_meetings", return_value=False), \
         patch.object(agent, "_build_catalog_text", return_value=""):
        if streaming:
            async def collect():
                return [event async for event in agent.chat_stream(session.id, "Question")]
            asyncio.run(collect())
        else:
            agent.chat(session.id, "Question")
    system = create.call_args.kwargs["messages"][0]["content"]
    assert system.startswith(custom)
    _assert_concise_svg_policy(system)
