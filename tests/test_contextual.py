import threading
import time
from unittest.mock import MagicMock

from src.rag.chunker import Chunk
from src.rag.contextual import (
    CONTEXT_BATCH_SIZE,
    ContextualRetrieval,
    _IngestRequestLimiter,
    _parse_context_batch,
    _table_source_image_ids,
    chunk_situating_card,
    is_image_only_text,
)


def _chunks(*texts: str, chunk_type: str = "normal") -> list[Chunk]:
    return [
        Chunk(text=text, metadata={"chunk_index": i, "source": "test.txt"}, chunk_type=chunk_type)
        for i, text in enumerate(texts)
    ]


def _json_summary(short: str = "A short overview.", structured: str = "") -> str:
    import json
    return json.dumps({"short_summary": short, "structured_summary": structured})


def _json_contexts(mapping: dict[int, str]) -> str:
    import json
    return json.dumps({
        "contexts": [{"id": i, "context": ctx} for i, ctx in mapping.items()],
    })


def test_situating_card_includes_full_body_and_index():
    text = (
        "| Step | Process | Flow Rate |\n"
        "| --- | --- | --- |\n"
        "| 1 | Intake | 100 m3/h |\n"
    )
    chunk = Chunk(
        text=text,
        metadata={"heading_path": "## 1.1 Process Flow Summary", "chunk_index": 0},
    )
    card = chunk_situating_card(chunk, 3)
    assert card.startswith("[3]")
    assert "heading:" not in card
    assert "| 1 | Intake | 100 m3/h |" in card
    assert ":::image" not in card


def test_situating_card_keeps_image_fence():
    fence = ":::image\nimage_id: abcdef123456\nfile_id: \n:::\n"
    chunk = Chunk(text=fence, metadata={"heading_path": "## Photo"})
    card = chunk_situating_card(chunk, 5)
    assert card.startswith("[5]")
    assert ":::image" in card
    assert "abcdef123456" in card


def test_situating_card_strips_table_source_fence():
    text = (
        ":::image\n"
        "image_id: aabbccddeeff00112233445566778899\n"
        "file_id: \n"
        ":::\n"
        "| Col | Val |\n"
        "| --- | --- |\n"
        "| a | 1 |\n"
    )
    chunk = Chunk(text=text, metadata={"heading_path": "## Costs"})
    card = chunk_situating_card(chunk, 2)
    assert card.startswith("[2]")
    assert "heading:" not in card
    assert "| a | 1 |" in card
    assert ":::image" not in card
    assert "aabbccddeeff00112233445566778899" not in card


def test_table_source_image_ids_skips_independent_figure():
    text = (
        ":::image\n"
        "image_id: aabbccddeeff00112233445566778899\n"
        "file_id: \n"
        ":::\n"
        "| Col | Val |\n"
        "| --- | --- |\n"
        "| a | 1 |\n\n"
        ":::image\n"
        "image_id: 11223344556677889900aabbccddeeff\n"
        "file_id: \n"
        "description: A site photo.\n"
        ":::\n"
    )
    assert _table_source_image_ids(text) == {"aabbccddeeff00112233445566778899"}


def test_multimodal_keeps_description_next_to_image():
    from src.rag.agentic_query import _build_multimodal_context

    text = (
        "See the figure.\n"
        ":::image\n"
        "image_id: 11223344556677889900aabbccddeeff\n"
        "file_id: file_demo\n"
        "ocr_text: INTAKE\n"
        "description: Aerial view of the intake lagoon at Site B.\n"
        ":::\n"
    )
    parts = _build_multimodal_context(
        text,
        {"11223344556677889900aabbccddeeff": {"base64": "abc", "mime": "image/png"}},
    )
    texts = [p.get("text", "") for p in parts if p.get("type") == "text"]
    assert any("Aerial view of the intake lagoon at Site B." in t for t in texts)
    assert any("INTAKE" in t for t in texts)
    assert any(p.get("type") == "image_url" for p in parts)
    assert not any(":::image" in t for t in texts)


def test_multimodal_target_chunk_image_has_no_alt_text():
    from src.rag.agentic_query import _build_multimodal_context

    text = (
        "[2]\n"
        ":::image\n"
        "image_id: 11223344556677889900aabbccddeeff\n"
        "file_id: file_demo\n"
        "ocr_text: INTAKE\n"
        "description: Aerial view of the intake lagoon at Site B.\n"
        ":::\n"
    )
    parts = _build_multimodal_context(
        text,
        {"11223344556677889900aabbccddeeff": {"base64": "abc", "mime": "image/png"}},
        include_alt_text=False,
    )
    texts = [p.get("text", "") for p in parts if p.get("type") == "text"]
    assert texts == ["[2]"]
    assert any(p.get("type") == "image_url" for p in parts)
    assert not any("INTAKE" in t or "Aerial view" in t for t in texts)


def test_multimodal_missing_image_keeps_alt_text():
    from src.rag.agentic_query import _build_multimodal_context

    text = (
        ":::image\n"
        "image_id: 11223344556677889900aabbccddeeff\n"
        "file_id: file_demo\n"
        "ocr_text: INTAKE\n"
        "description: Aerial view of the intake lagoon at Site B.\n"
        ":::\n"
    )
    parts = _build_multimodal_context(
        text,
        {"00000000000000000000000000000000": {"base64": "x", "mime": "image/png"}},
        include_alt_text=False,
    )
    texts = [p.get("text", "") for p in parts if p.get("type") == "text"]
    assert any("INTAKE" in t for t in texts)
    assert any("Aerial view" in t for t in texts)
    assert not any(p.get("type") == "image_url" for p in parts)


def test_is_image_only_empty_fence():
    fence = ":::image\nimage_id: abcdef123456\nfile_id: \n:::\n"
    assert is_image_only_text(fence)
    assert not is_image_only_text(fence + "\nSome prose after the figure.")
    described = ":::image\nimage_id: abcdef123456\ndescription: A pump skid\n:::\n"
    assert not is_image_only_text(described)


def test_parse_context_batch_json():
    raw = '{"contexts": [{"id": 0, "context": "About unit A."}, {"id": 2, "context": "About unit C."}]}'
    parsed = _parse_context_batch(raw, [0, 1, 2])
    assert parsed[0] == "About unit A."
    assert parsed[2] == "About unit C."
    assert 1 not in parsed


def test_add_context_writes_per_chunk_situating():
    llm = MagicMock()
    llm.generate.side_effect = [
        _json_summary("Doc about Python.", "===DATA===\n- Python started in 1991\n===FACTS===\n- Multi-paradigm\n===INSIGHTS===\n- None identified"),
        _json_contexts({0: "Introduces the Python language.", 1: "Notes that Python is multi-paradigm."}),
    ]
    cr = ContextualRetrieval(llm=llm, cache_warmup_delay=0, summary_retry_delay=0)
    chunks = _chunks("Python is a popular language.", "It supports multiple paradigms.")
    result = cr.add_context(
        chunks, full_document="Python is a popular language. It supports multiple paradigms."
    )

    assert result[0].metadata["summary"] == "Doc about Python."
    assert result[0].metadata["context"] == "Introduces the Python language."
    assert result[1].metadata["context"] == "Notes that Python is multi-paradigm."
    assert "_structured_summary" in result[0].metadata
    assert llm.generate.call_count == 2
    system = llm.generate.call_args_list[0].kwargs.get("system") or llm.generate.call_args_list[0][1].get("system")
    assert system  # shared ingest system prompt


def test_add_context_tabular_skips_context_llm():
    llm = MagicMock()
    llm.generate.return_value = _json_summary("A workbook.")
    cr = ContextualRetrieval(llm=llm, cache_warmup_delay=0, summary_retry_delay=0)
    chunks = _chunks("row 1", "row 2", "row 3")
    cr.add_context(chunks, full_document="row 1\n\nrow 2", tabular=True)

    assert llm.generate.call_count == 1
    assert chunks[0].metadata["summary"] == "A workbook."
    assert not chunks[0].metadata.get("context")


def test_add_context_switch_off_still_runs_summary():
    llm = MagicMock()
    llm.generate.return_value = _json_summary("Still summarized.")
    cr = ContextualRetrieval(llm=llm, cache_warmup_delay=0, summary_retry_delay=0)
    chunks = _chunks("alpha", "beta")
    cr.add_context(chunks, full_document="alpha beta", contextual_enabled=False)

    assert llm.generate.call_count == 1
    assert chunks[0].metadata["summary"] == "Still summarized."
    assert not chunks[0].metadata.get("context")


def test_add_context_skips_parent_chunks():
    llm = MagicMock()
    llm.generate.side_effect = [
        _json_summary("Doc."),
        _json_contexts({0: "Child A.", 1: "Child B."}),
    ]
    cr = ContextualRetrieval(llm=llm, cache_warmup_delay=0, summary_retry_delay=0)
    parents = _chunks("PARENT BODY " * 20, chunk_type="parent")
    children = _chunks("child a text", "child b text")
    for child in children:
        child.chunk_type = "child"
    result = cr.add_context(parents + children, full_document="full")

    assert result[0].chunk_type == "parent"
    assert not result[0].metadata.get("context")
    assert result[1].metadata["context"] == "Child A."
    assert result[2].metadata["context"] == "Child B."
    # Second generate is the context batch — parent text must not be in the prompt
    ctx_prompt = llm.generate.call_args_list[1][0][0]
    assert "PARENT BODY" not in ctx_prompt


def test_add_context_includes_image_only_chunks():
    llm = MagicMock()
    llm.generate.side_effect = [
        _json_summary("A report with figures."),
        _json_contexts({0: "Project X, section 2, process-flow figure."}),
    ]
    cr = ContextualRetrieval(llm=llm, cache_warmup_delay=0, summary_retry_delay=0)
    fence = ":::image\nimage_id: abcdef123456\nfile_id: \n:::\n"
    chunks = _chunks(fence)
    cr.add_context(chunks, full_document="Project X\n\n" + fence, is_visual=False)

    assert llm.generate.call_count == 2
    assert chunks[0].metadata["context"] == "Project X, section 2, process-flow figure."


def test_add_context_batches_of_ten():
    llm = MagicMock()
    n = 23
    texts = [f"chunk body {i} about entity {i}" for i in range(n)]

    def _gen(prompt, **kwargs):
        if "short_summary" in (kwargs.get("response_format") or {}).get("type", "") or "Structured Summary" in str(prompt):
            return _json_summary("Many chunks.")
        # context batch
        return _json_contexts({i: f"Situates chunk {i}." for i in range(n)})

    llm.generate.side_effect = [
        _json_summary("Many chunks."),
        _json_contexts({i: f"Situates chunk {i}." for i in range(10)}),
        _json_contexts({i: f"Situates chunk {i}." for i in range(10, 20)}),
        _json_contexts({i: f"Situates chunk {i}." for i in range(20, 23)}),
    ]
    cr = ContextualRetrieval(llm=llm, cache_warmup_delay=0, summary_retry_delay=0)
    chunks = _chunks(*texts)
    cr.add_context(chunks, full_document="\n".join(texts))

    assert llm.generate.call_count == 1 + 3
    assert chunks[0].metadata["context"] == "Situates chunk 0."
    assert chunks[22].metadata["context"] == "Situates chunk 22."
    assert CONTEXT_BATCH_SIZE == 10


def test_add_context_missing_ids_do_not_borrow():
    llm = MagicMock()
    llm.generate.side_effect = [
        _json_summary("Doc."),
        _json_contexts({0: "Only the first chunk."}),
    ]
    cr = ContextualRetrieval(llm=llm, cache_warmup_delay=0, summary_retry_delay=0)
    chunks = _chunks("first block of text", "second block of text")
    cr.add_context(chunks, full_document="first second")
    assert chunks[0].metadata["context"] == "Only the first chunk."
    assert chunks[1].metadata.get("context") == ""


def test_ingest_request_limiter_caps_concurrent_calls(monkeypatch):
    class _E:
        max_parallel_context = 2

    class _C:
        enrichment = _E()

    monkeypatch.setattr("src.config.get_config", lambda: _C())
    limiter = _IngestRequestLimiter()
    peak = 0
    current = 0
    lock = threading.Lock()

    def worker():
        nonlocal peak, current
        with limiter:
            with lock:
                current += 1
                peak = max(peak, current)
            time.sleep(0.06)
            with lock:
                current -= 1

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive()
    assert peak == 2


def test_summary_and_context_across_files_overlap():
    """Two files can sit in ingest LLM at once — no file-level enrich lock."""
    barrier = threading.Barrier(2, timeout=3)
    errors: list[BaseException] = []

    def make_llm():
        llm = MagicMock()

        def gen(prompt, **kwargs):
            barrier.wait()
            return _json_summary("S")

        llm.generate.side_effect = gen
        return llm

    def run_one():
        try:
            cr = ContextualRetrieval(llm=make_llm(), cache_warmup_delay=0, summary_retry_delay=0)
            chunks = _chunks("alpha text here")
            cr.add_context(chunks, full_document="alpha text here", contextual_enabled=False)
        except BaseException as exc:
            errors.append(exc)

    t1 = threading.Thread(target=run_one)
    t2 = threading.Thread(target=run_one)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not t1.is_alive() and not t2.is_alive()
    assert errors == []


def test_no_file_level_enrich_lock():
    import src.tasks.handlers as handlers

    assert not hasattr(handlers, "_enrich_lock")
    assert hasattr(handlers, "_enrich_executor")


def test_build_enriched_text_image_only_uses_description():
    from src.tasks.handlers import _build_enriched_text

    chunk = Chunk(
        text=(
            ":::image\n"
            "image_id: aabbccddeeff00112233445566778899\n"
            "file_id: file1\n"
            "description: Aerial view of the intake lagoon.\n"
            ":::\n"
        ),
        metadata={"source_label": "site.pdf", "context": "Project X, site plan figure."},
    )
    text = _build_enriched_text(chunk)
    assert "Source: site.pdf" in text
    assert "Context: Project X, site plan figure." in text
    assert "Aerial view of the intake lagoon." in text
    assert ":::image" not in text


def test_build_enriched_text_includes_ocr_and_description():
    from src.tasks.handlers import _build_enriched_text

    fence = (
        ":::image\n"
        "image_id: aabbccddeeff00112233445566778899\n"
        "file_id: file1\n"
        "ocr_text: INTAKE 100 m3/h\n"
        "description: Aerial view of the intake lagoon.\n"
        ":::\n"
    )
    chunk = Chunk(
        text=fence,
        metadata={"source_label": "site.pdf"},
    )
    text = _build_enriched_text(chunk)
    assert "Source: site.pdf" in text
    assert "INTAKE 100 m3/h" in text
    assert "Aerial view of the intake lagoon." in text
    assert ":::image" not in text


def test_build_enriched_text_falls_back_to_image_meta():
    from src.tasks.handlers import _build_enriched_text

    chunk = Chunk(
        text=":::image\nimage_id: aabbccddeeff00112233445566778899\nfile_id: file1\n:::\n",
        metadata={
            "source_label": "site.pdf",
            "images": [{"image_id": "aabbccddeeff00112233445566778899", "ocr_text": "PUMP P-101"}],
        },
    )
    text = _build_enriched_text(chunk)
    assert "PUMP P-101" in text


def test_build_enriched_text_strips_table_source_fence():
    from src.tasks.handlers import _build_enriched_text

    fence = (
        ":::image\n"
        "image_id: aabbccddeeff00112233445566778899\n"
        "file_id: \n"
        ":::\n\n"
        "| Col | Val |\n| --- | --- |\n| a | 1 |\n"
    )
    chunk = Chunk(
        text=fence,
        metadata={"source_label": "sheet.xlsx", "sheet_name": "CAPEX", "context": "Cost table."},
    )
    text = _build_enriched_text(chunk)
    assert "Source: sheet.xlsx" in text
    assert "Sheet: CAPEX" in text
    assert "Context: Cost table." in text
    assert ":::image" not in text
    assert "| Col | Val |" in text
    assert "Document:" not in text
