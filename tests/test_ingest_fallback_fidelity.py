"""Fallback success must be observable as local parsing, including auth failures."""

from src.parsers.mineru_parser import MinerUError
from tests.test_rag_index_integrity import ingest
import pytest


@pytest.fixture(autouse=True)
def offline_summary(monkeypatch):
    monkeypatch.setattr("src.rag.contextual.run_ingest_summary", lambda *args, **kwargs: {
        "short_summary": "A synthetic document.", "structured_summary": "", "inspect": {},
    })


def test_business_auth_failure_without_http_auth_code_is_recognized():
    assert MinerUError("MinerU API error: user authenticate failed", code=10000).is_auth_error


def test_successful_local_fallback_is_not_recorded_as_mineru(ingest, monkeypatch, tmp_path):
    from src.tasks import handlers
    from src.rag.ingest_trace import load_trace
    run, db, services, chunks = ingest
    monkeypatch.setattr(handlers, "_is_mineru_ready", lambda *args: True)

    def fail(*args):
        raise MinerUError("user authenticate failed", code=10000)

    monkeypatch.setattr(handlers, "parse_with_mineru", fail)
    run()
    parse = next(s for s in load_trace(tmp_path)["steps"] if s["id"] == "parse")
    assert parse["data"]["parser"] == "local"
    assert parse["data"]["fallback_from"] == "MinerU"
    assert parse["data"]["auth_error"] is True


def test_unrecoverable_page_image_is_not_published_as_successful_ingestion(ingest, monkeypatch):
    from src.parsers.base import ParsedDocument
    from src.tasks import handlers
    run, db, services, chunks = ingest
    monkeypatch.setattr(handlers, "parse_file", lambda path: ParsedDocument(
        "A surviving header", metadata={"image_render_failures": [2]}, file_type="pdf",
    ))
    with pytest.raises(Exception, match="page evidence"):
        run()
    db.upsert_points.assert_not_called()


def test_timed_out_image_worker_cannot_publish_or_mutate_document_later(ingest, monkeypatch):
    import threading
    import time
    from src.parsers.base import ImageInfo, ParsedDocument
    from src.parsers.image_utils import build_image_block
    from src.tasks import handlers
    run, db, services, chunks = ingest
    image = ImageInfo("a" * 32, image_bytes=b"synthetic image")
    doc = ParsedDocument(build_image_block(image), images=[image])
    monkeypatch.setattr(handlers, "parse_file", lambda path: doc)
    monkeypatch.setattr(handlers, "IMAGE_JOB_DEADLINE_SEC", 0.001)
    finished = threading.Event()

    def slow_ocr(worker_doc, **kwargs):
        time.sleep(0.12)
        worker_doc.images[0].ocr_text = "late uncommitted OCR result"
        finished.set()

    monkeypatch.setattr("src.parsers.image_utils.ocr_classify_document_images", slow_ocr)
    with pytest.raises(Exception, match="[Ii]mage.*deadline"):
        run()
    assert finished.wait(1)
    db.upsert_points.assert_not_called()
    assert not image.ocr_text


@pytest.mark.parametrize("embedding_fails", [False, True])
def test_structured_summary_is_published_only_after_document_store(ingest, monkeypatch, embedding_fails):
    from unittest.mock import Mock
    from src.tasks import handlers
    from src.rag import contextual
    run, db, services, chunks = ingest
    published = []

    class FakeContextual:
        inspect = {}

        def __init__(self, **kwargs):
            pass

        def add_context(self, rows, **kwargs):
            for row in rows:
                row.metadata['_structured_summary'] = '{"facts":["A source fact"]}'
            kwargs['on_summary']('Synthetic summary')
            for row in rows:
                kwargs['on_chunk_ready'](row, '')
            return rows

    # Use the production enrichment wrapper, with both model and store mocked.
    original = _PRODUCTION_ENRICH
    monkeypatch.setattr(handlers, '_do_enrich', original)
    monkeypatch.setattr(handlers, '_get_enriching_llm', lambda config: object())
    monkeypatch.setattr(contextual, 'ContextualRetrieval', FakeContextual)

    def publish(*args):
        published.append(True)
        assert db.upsert_points.called, 'Summary became searchable before original document'

    monkeypatch.setattr(handlers, '_store_structured_summary', publish)
    if embedding_fails:
        services.embedding.embed_texts = Mock(side_effect=RuntimeError('embedding unavailable'))
        monkeypatch.setattr('time.sleep', lambda _: None)
        with pytest.raises(Exception, match='[Ee]mbedding'):
            run()
        assert published == []
    else:
        run()
        assert published == [True]
        assert all('_structured_summary' not in p for p in db.upsert_points.call_args.kwargs['payloads'])


from src.tasks.handlers import _do_enrich as _PRODUCTION_ENRICH
