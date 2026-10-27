"""Image evidence must follow the receiving model, without changing the index."""

import json
import base64
from io import BytesIO
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image

from src.rag.retriever import RetrievedChunk


IID = "a" * 32
OTHER = "b" * 32
_buffer = BytesIO()
Image.new("RGB", (2, 2), "blue").save(_buffer, format="PNG")
PNG_BASE64 = base64.b64encode(_buffer.getvalue()).decode("ascii")
IMAGE = {"base64": PNG_BASE64, "mime": "image/png"}


def fence(iid=IID):
    return (f":::image\nimage_id: {iid}\nfile_id: file1\n"
            "ocr_text: OCR_SENTINEL 40%\ndescription: DESCRIPTION_SENTINEL\n:::\n")


def text_of(content):
    return content if isinstance(content, str) else "\n".join(
        p.get("text", "") for p in content if p.get("type") == "text")


def image_count(content):
    return sum(p.get("type") == "image_url" for p in content) if isinstance(content, list) else 0


def assert_fallback(content):
    text = text_of(content)
    assert "OCR_SENTINEL" in text and "DESCRIPTION_SENTINEL" in text
    assert "not original document text" in text
    assert "row" in text and "column" in text and "incorrect" in text


class CapturingLLM:
    def __init__(self, visual, response="[]"):
        self.supports_vision = visual
        self.response = response
        self.calls = []

    def generate(self, prompt, **kwargs):
        self.calls.append(deepcopy(prompt))
        return self.response


def chunk():
    return RetrievedChunk("Primary prose\n" + fence(), 0.9, {
        "id": "point1", "collection": "col1", "source": "document.pdf",
        "file_id": "file1", "version_id": "v1", "is_current": True,
        "images": [{"image_id": IID, "file_id": "file1"}],
    })


@pytest.mark.parametrize("visual,images,expected", [
    (True, {IID: IMAGE}, 1), (False, {IID: IMAGE}, 0),
    (True, {}, 0), (True, {IID: {"base64": "", "mime": "image/png"}}, 0),
])
def test_rendering_prefers_original_or_explicitly_marks_fallback(visual, images, expected):
    from src.rag.image_evidence import render_image_evidence
    content = "Primary prose\n" + fence() + "Trailing prose"
    rendered = render_image_evidence(content, images, is_visual=visual)
    assert image_count(rendered) == expected
    assert "Primary prose" in text_of(rendered) and "Trailing prose" in text_of(rendered)
    if expected:
        assert "OCR_SENTINEL" not in text_of(rendered)
        assert "DESCRIPTION_SENTINEL" not in text_of(rendered)
    else:
        assert_fallback(rendered)
    assert "OCR_SENTINEL" in content


def test_mixed_images_get_individual_fallback_without_claiming_document_absence():
    from src.rag.image_evidence import render_image_evidence
    rendered = render_image_evidence(fence() + fence(OTHER), {IID: IMAGE}, is_visual=True)
    assert image_count(rendered) == 1
    assert text_of(rendered).count("OCR_SENTINEL") == 1
    assert_fallback(rendered)
    assert "absence from the original image" in text_of(rendered)


def test_retrieval_retains_both_machine_readings_and_canonical_chunk():
    from src.rag.index_text import build_index_text
    from src.rag.image_evidence import render_image_evidence
    c = chunk()
    before = deepcopy(c)
    render_image_evidence(c.text, {IID: IMAGE}, is_visual=True)
    indexed = build_index_text(c)
    assert "OCR_SENTINEL" in indexed and "DESCRIPTION_SENTINEL" in indexed
    assert c == before


@pytest.mark.parametrize("visual", [True, False])
def test_grading_receives_image_policy_for_its_own_model(visual):
    from src.rag.agent_nodes import node_combined_grade
    from src.rag.agent_state import AgentState
    llm = CapturingLLM(visual, json.dumps({"relevant_indices": [0], "retained_info": "", "gap_analysis": ""}))
    with patch("src.rag.agentic_query._stitch_images_from_chunks", return_value={IID: IMAGE}):
        node_combined_grade(AgentState(original_query="ownership"), [chunk()], llm=llm)
    assert image_count(llm.calls[0]) == int(visual)
    if visual:
        assert "OCR_SENTINEL" not in text_of(llm.calls[0])
    else:
        assert_fallback(llm.calls[0])


def test_grading_json_retry_preserves_original_image_without_machine_text():
    from src.rag.agent_nodes import node_combined_grade
    from src.rag.agent_state import AgentState
    llm = CapturingLLM(True)
    responses = iter(["invalid", '{"relevant_indices":[0],"retained_info":"","gap_analysis":""}'])
    def generate(prompt, **kwargs):
        llm.calls.append(deepcopy(prompt))
        return next(responses)
    llm.generate = generate
    with patch("src.rag.agentic_query._stitch_images_from_chunks", return_value={IID: IMAGE}):
        node_combined_grade(AgentState(original_query="ownership"), [chunk()], llm=llm)
    assert len(llm.calls) == 2
    assert all(image_count(p) == 1 and "OCR_SENTINEL" not in text_of(p) for p in llm.calls)


@pytest.mark.parametrize("visual", [True, False])
def test_evidence_planning_uses_its_own_model_capability(visual):
    from src.rag.decomposer import Decomposer
    llm = CapturingLLM(visual)
    with patch("src.rag.agentic_query._stitch_images_from_chunks", return_value={IID: IMAGE}):
        Decomposer(llm).plan_with_evidence("ownership", [], [chunk()])
    assert image_count(llm.calls[0]) == int(visual)
    if visual:
        assert "OCR_SENTINEL" not in text_of(llm.calls[0])
    else:
        assert_fallback(llm.calls[0])


@pytest.mark.parametrize("visual,available", [(True, True), (True, False), (False, True)])
def test_ingest_prefix_and_context_batch_have_same_policy(visual, available):
    from src.rag.contextual import _document_user_content, ContextualRetrieval
    from src.rag.chunker import Chunk
    llm = CapturingLLM(visual, '{"contexts":[{"id":0,"context":"scope"}]}')
    with patch("src.rag.contextual._collect_images_for_text", return_value={IID: IMAGE} if available else {}):
        prefix = _document_user_content(fence(), is_visual=visual, collection_id="col1", file_id="file1")
        cr = ContextualRetrieval(llm, cache_warmup_delay=0)
        cr._situate_batch(prefix, [(0, Chunk(text=fence(), metadata={}))],
                          is_visual=visual, collection_id="col1", file_id="file1",
                          prefix_image_ids={IID} if visual and available else set())
    sent = llm.calls[0]
    assert image_count(sent) == int(visual and available)
    if visual and available:
        assert "OCR_SENTINEL" not in text_of(sent)
    else:
        assert_fallback(sent)


@pytest.mark.parametrize("visual", [True, False])
def test_json_document_reader_is_rendered_and_deduplicated_before_chat(visual, tmp_path):
    from src.chatbox.agent import _hydrate_history_images
    from src.chatbox.image_memory import snapshot_session_images
    snapshot_session_images("session1", {IID: f"data:image/png;base64,{PNG_BASE64}"}, base_dir=tmp_path)
    doc = "Primary prose\n" + fence()
    canonical = json.dumps({"file_id": "file1", "total_chars": len(doc), "content": doc,
                            "text": doc, "has_more": False})
    messages = [{"role": "tool", "tool_call_id": "read", "content": canonical}]
    result = _hydrate_history_images(messages, session_id="session1", image_store_dir=tmp_path, is_visual=visual)
    sent = result[0]["content"]
    assert image_count(sent) == int(visual)
    assert text_of(sent).count("Primary prose") == 1
    assert "total_chars" in text_of(sent)
    if visual:
        assert "OCR_SENTINEL" not in text_of(sent)
    else:
        assert_fallback(sent)
    assert messages[0]["content"] == canonical, "request projection must not mutate persisted history"


def test_document_reader_loads_original_without_a_preexisting_session_snapshot(tmp_path):
    from src.chatbox.agent import _hydrate_history_images
    from src.chatbox.image_memory import load_session_image
    message = {"role": "tool", "tool_call_id": "read",
               "content": json.dumps({"content": fence(), "text": fence()})}
    with patch("src.parsers.image_utils.encode_image_base64", return_value=(PNG_BASE64, "image/png")) as load:
        result = _hydrate_history_images([message], session_id="session1", image_store_dir=tmp_path, is_visual=True)
    assert image_count(result[0]["content"]) == 1
    assert load.call_count == 1
    assert load_session_image("session1", IID, base_dir=tmp_path) == f"data:image/png;base64,{PNG_BASE64}"


def test_corrupt_session_snapshot_uses_readable_original_instead(tmp_path):
    from src.chatbox.agent import _hydrate_history_images
    from src.chatbox.image_memory import snapshot_session_images
    snapshot_session_images("session1", {IID: "data:image/png;base64,NOT-BASE64"}, base_dir=tmp_path)
    with patch("src.parsers.image_utils.encode_image_base64", return_value=(PNG_BASE64, "image/png")) as load:
        result = _hydrate_history_images([{"role": "tool", "content": fence()}],
                                         session_id="session1", image_store_dir=tmp_path, is_visual=True)
    assert load.call_count == 1
    assert image_count(result[0]["content"]) == 1
    assert "NOT-BASE64" not in json.dumps(result)
    assert "OCR_SENTINEL" not in text_of(result[0]["content"])


def test_ingest_keeps_original_table_screenshot_as_well_as_table_markdown():
    from src.rag.contextual import _document_user_content
    table = fence() + "| Entity | Equity |\n| --- | --- |\n| Entity A | 40% |\n"
    with patch("src.parsers.image_utils._encode_image_base64_direct", return_value=(PNG_BASE64, "image/png")):
        prompt = _document_user_content(table, is_visual=True, collection_id="col1", file_id="file1")
    assert image_count(prompt) == 1
    assert "| Entity A | 40% |" in text_of(prompt)
    assert "OCR_SENTINEL" not in text_of(prompt)


def test_unknown_provider_never_borrows_vision_flag_from_another_provider():
    from src.chatbox.agent import _chat_model_is_visual
    from src.config import AppConfig, LLMProviderConfig
    cfg = AppConfig()
    cfg.llm.providers = [LLMProviderConfig(id="vision", model="same-model", default_model="same-model",
                                         visual_model_ids=["same-model"])]
    with patch("src.config.get_config", return_value=cfg):
        assert _chat_model_is_visual(SimpleNamespace(_model="same-model", supports_vision=False)) is False


def test_stitching_merges_metadata_and_fence_refs_without_mutating_metadata():
    from src.rag.agentic_query import _stitch_images_from_chunks
    c = chunk()
    c.text += fence(OTHER)
    original = deepcopy(c.metadata)
    with patch("src.parsers.image_utils._encode_image_base64_direct", return_value=(PNG_BASE64, "image/png")):
        images = _stitch_images_from_chunks([c])
    assert set(images) == {IID, OTHER}
    assert c.metadata == original


def test_missing_or_partial_fences_never_leak_unmarked_machine_text():
    from src.rag.image_evidence import render_image_evidence
    partial = fence()[:-5]
    rendered = render_image_evidence(partial, {}, is_visual=True)
    assert "OCR_SENTINEL" not in text_of(rendered) or "not original document text" in text_of(rendered)


def test_older_history_budget_uses_reference_without_ocr_and_current_read_keeps_all_images(tmp_path):
    from src.chatbox.agent import _hydrate_history_images, _MAX_INLINE_HISTORY_IMAGES
    from src.chatbox.image_memory import snapshot_session_images
    ids = [f"{i:032x}" for i in range(_MAX_INLINE_HISTORY_IMAGES + 2)]
    snapshot_session_images("session1", {i: f"data:image/png;base64,{PNG_BASE64}" for i in ids}, base_dir=tmp_path)
    old = [{"role": "tool", "content": fence(i)} for i in ids]
    out = _hydrate_history_images(old + [{"role": "user", "content": "Follow-up"}],
                                  session_id="session1", image_store_dir=tmp_path, is_visual=True)
    assert sum(image_count(m["content"]) for m in out) == _MAX_INLINE_HISTORY_IMAGES
    assert "OCR_SENTINEL" not in text_of(out[0]["content"])
    assert "stored" in text_of(out[0]["content"]) and "budget" in text_of(out[0]["content"])
    out = _hydrate_history_images([{"role": "user", "content": "Read now"}] + old,
                                  session_id="session1", image_store_dir=tmp_path, is_visual=True)
    assert sum(image_count(m["content"]) for m in out) == len(ids)


@pytest.mark.parametrize("visual", [True, False])
def test_optional_direct_and_aggregate_synthesis_follow_policy(visual):
    from unittest.mock import MagicMock
    from src.rag.direct_query import DirectQueryModule
    from src.rag.aggregator import Aggregator, SubQueryResult
    llm = CapturingLLM(visual, "Answer")
    retriever = MagicMock()
    retriever.retrieve.return_value = [chunk()]
    db = MagicMock()
    db.get_collection_config.return_value = {"chunk_mode": "normal"}
    with patch("src.rag.agentic_query._stitch_images_from_chunks", return_value={IID: IMAGE}):
        result = DirectQueryModule(retriever, db, llm=llm).retrieve(
            "ownership", ["col1"], generate_answer=True, rerank_enabled=False)
        Aggregator(llm).aggregate([SubQueryResult("ownership", [chunk()], "")])
    assert "OCR_SENTINEL" in result.context, "canonical output remains available for later receivers"
    assert len(llm.calls) == 2
    for prompt in llm.calls:
        assert image_count(prompt) == int(visual)
        if visual:
            assert "OCR_SENTINEL" not in text_of(prompt)
        else:
            assert_fallback(prompt)


def test_provider_capability_uses_selected_override_and_provider_card():
    from src.config import LLMProviderConfig
    from src.providers.llm import create_llm_for_provider
    visual = LLMProviderConfig(id="visual", api_key="test-only", model="text-model", default_model="text-model",
                               visual_model_ids=["same-model"])
    text = LLMProviderConfig(id="text", api_key="test-only", model="same-model", default_model="same-model",
                             visual_model_ids=[])
    with patch("src.providers.llm.openai_compat.OpenAI"):
        assert create_llm_for_provider(visual).supports_vision is False
        assert create_llm_for_provider(visual, model="same-model").supports_vision is True
        assert create_llm_for_provider(text, model="same-model").supports_vision is False


@pytest.mark.parametrize("streaming", [False, True])
def test_real_chat_document_reader_delivers_original_and_persists_canonical(
    store, mock_llm, mock_agentic, tmp_path, streaming,
):
    import asyncio
    from unittest.mock import AsyncMock
    from src.chatbox.agent import ChatboxAgent
    from src.config import AppConfig
    from tests.test_chatbox_agent import _fake_llm_response, _fake_stream, _tc_delta
    mock_llm.supports_vision = True
    doc = "Primary prose\n" + fence()
    canonical = json.dumps({"file_id": "file1", "content": doc, "text": doc, "has_more": False})
    args = json.dumps({"collection": "col1", "file_id": "file1"})
    create = mock_llm._client.chat.completions.create
    create.side_effect = ([
        _fake_stream([("tool_calls", {"tool_calls": [_tc_delta(0, "read", "get_document_text", args)]})]),
        _fake_stream([("stop", {"content": "Answer"})]),
    ] if streaming else [
        _fake_llm_response(tool_calls=[{"id": "read", "type": "function", "function": {
            "name": "get_document_text", "arguments": args}}]),
        _fake_llm_response(content="Answer"),
    ])
    agent = ChatboxAgent(store, mock_llm, mock_agentic)
    agent._image_store_dir = tmp_path / "images"
    session = store.create_session(title="Image delivery")
    with patch("src.config.get_config", return_value=AppConfig()), \
         patch("src.chatbox.agent.execute_structure_tool", return_value=canonical), \
         patch("src.chatbox.agent.execute_structure_tool_async", new=AsyncMock(return_value=canonical)), \
         patch("src.parsers.image_utils.encode_image_base64", return_value=(PNG_BASE64, "image/png")):
        if streaming:
            async def collect():
                return [e async for e in agent.chat_stream(session.id, "Read document")]
            asyncio.run(collect())
        else:
            agent.chat(session.id, "Read document")
    sent = next(m["content"] for m in create.call_args_list[1].kwargs["messages"] if m.get("role") == "tool")
    assert image_count(sent) == 1
    assert "OCR_SENTINEL" not in text_of(sent)
    assert text_of(sent).count("Primary prose") == 1
    saved = next(m.content for m in store.get_messages(session.id) if m.role == "tool")
    persisted = json.loads(saved)
    assert persisted.pop("evidence"), "The reader must retain citation identities with its canonical content"
    assert persisted == json.loads(canonical)


from tests.test_chatbox_agent import store, mock_llm, mock_agentic  # noqa: E402,F401
