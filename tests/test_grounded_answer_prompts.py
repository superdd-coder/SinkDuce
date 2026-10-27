"""Evidence rules reach real request builders; these tests do not judge LLM answers."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.chatbox.agent import ChatboxAgent
from src.config import AppConfig
from src.rag.agent_nodes import node_combined_grade
from src.rag.agent_state import AgentState
from src.rag.retriever import RetrievedChunk
from tests.test_chatbox_agent import (
    store, mock_llm, mock_agentic, _fake_llm_response, _fake_stream,
)


ANSWER_RULE = "Separate source facts, mathematical deductions, and causal explanations."
FINDINGS_RULE = "Ground causal explanations in primary-source passages"


@pytest.mark.parametrize("mode", ["direct", "agentic"])
@pytest.mark.parametrize("streaming", [False, True])
def test_evidence_rules_reach_chat_model_without_extra_calls(
    store, mock_llm, mock_agentic, mode, streaming,
):
    custom_role = "Explain clearly and preserve the user's preferred language."
    agent = ChatboxAgent(store, mock_llm, mock_agentic, system_prompt=custom_role)
    session = store.create_session(title="Evidence policy request")
    create = mock_llm._client.chat.completions.create
    create.return_value = (
        _fake_stream([("stop", {"content": "Supported answer."})])
        if streaming else _fake_llm_response(content="Supported answer.")
    )
    with patch("src.config.get_config", return_value=AppConfig()), \
         patch.object(agent, "_has_meetings", return_value=False), \
         patch.object(agent, "_build_catalog_text", return_value=""):
        if streaming:
            async def collect():
                return [event async for event in agent.chat_stream(
                    session.id, "Explain the reported change.", mode=mode,
                )]
            asyncio.run(collect())
        else:
            agent.chat(session.id, "Explain the reported change.", mode=mode)

    system = create.call_args.kwargs["messages"][0]["content"]
    assert ANSWER_RULE in system, "The model request has no evidence policy"
    assert system.count(ANSWER_RULE) == 1
    if mode == "agentic":
        assert system.startswith(custom_role)
    else:
        assert "quick Q&A assistant" in system
    assert create.call_count == 1
    assert all(message.role != "system" for message in store.get_messages(session.id))


@pytest.mark.parametrize("root", ["", "Compare the two reports and explain the change."])
def test_scoped_and_legacy_grade_receive_evidence_rules_without_schema_changes(root):
    body = "Reported payments: 100 units in one report, 90 units in the next."
    model = SimpleNamespace(generate=Mock(return_value=json.dumps({
        "relevant_indices": [0], "retained_info": body, "gap_analysis": "",
    })))
    state = AgentState(original_query="Find the reported payments.", original_user_question=root)
    chunk = RetrievedChunk(body, 0.9, {"id": "point-a", "source": "report", "collection": "col-a"})
    node_combined_grade(state, [chunk], llm=model)

    system = " ".join(model.generate.call_args.kwargs["system"].split())
    assert FINDINGS_RULE in system, "The grading request has no causal evidence policy"
    assert system.count(FINDINGS_RULE) == 1
    assert body in model.generate.call_args.args[0]
    assert model.generate.call_count == 1
    assert state.retained_chunks == [chunk]
    assert state.retained_info == body
    assert state.current_gap_analysis == ""


def test_rebuilding_messages_keeps_custom_role_without_accumulating_policy(store, mock_llm, mock_agentic):
    agent = ChatboxAgent(store, mock_llm, mock_agentic, system_prompt="Custom analyst role.")
    session = store.create_session(title="Repeated request construction")
    first = agent._build_messages(session.id, "Question", catalog_text="")
    second = agent._build_messages(
        session.id, "Question", system_prompt=first[0]["content"], catalog_text="",
    )
    assert ANSWER_RULE in first[0]["content"]
    assert second[0]["content"] == first[0]["content"]
    assert agent._system_prompt == "Custom analyst role."


@pytest.mark.parametrize("session_id", ["meeting_policy", "group_policy"])
def test_document_policy_keeps_meeting_and_group_system_prompts_unchanged(
    store, mock_llm, mock_agentic, session_id,
):
    agent = ChatboxAgent(store, mock_llm, mock_agentic)
    store.create_session(session_id=session_id, title="Existing meeting policy")
    messages = agent._build_messages(session_id, "Question", system_prompt="Meeting policy.", catalog_text="")
    assert messages[0]["content"] == "Meeting policy."


@pytest.mark.parametrize("document_policy", [False, True])
def test_force_answer_provider_fallback_preserves_document_policy(document_policy):
    from src.chatbox.agent import _force_generate_answer
    from src.prompts import CHAT_FORCE_ANSWER_SYSTEM, KNOWLEDGE_ANSWER_EVIDENCE_RULES

    model = SimpleNamespace(_client=None, generate=Mock(return_value="Supported answer."))
    system = "Existing role."
    if document_policy:
        system += "\n\n" + KNOWLEDGE_ANSWER_EVIDENCE_RULES
    _force_generate_answer(model, [{"role": "system", "content": system}])

    actual_system = model.generate.call_args.kwargs["system"]
    if document_policy:
        assert ANSWER_RULE in actual_system
        assert actual_system.count(ANSWER_RULE) == 1
    else:
        assert actual_system == CHAT_FORCE_ANSWER_SYSTEM
    assert model.generate.call_count == 1
