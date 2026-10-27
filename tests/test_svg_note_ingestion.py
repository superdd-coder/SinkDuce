"""SVG stays editable in Notes while ingestion stores atomic PNG evidence."""

from io import BytesIO
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

from src.parsers.markdown import MarkdownParser, extract_markdown_images
from src.parsers.base import ParsedDocument
from src.parsers.image_utils import process_document_images, annotate_chunks_with_images
from src.rag.markdown_chunker import MarkdownChunker, MarkdownParentChildChunker


SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 240">
<title>Participation route</title>
<desc>Entity A provides financing to entity B.</desc>
<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="8" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#1a5e3d"/></marker></defs>
<rect width="640" height="240" fill="#fdfbf7"/>
<rect x="30" y="60" width="180" height="90" fill="#1a5e3d"/>
<text x="50" y="110" fill="white">投资人 A</text>
<path d="M210,100 L420,100" stroke="#1a5e3d" marker-end="url(#arrow)"/>
<text x="440" y="110">Entity B</text>
</svg>'''


def fenced(svg=SVG, marker="```", language="svg"):
    return f"{marker}{language}\n{svg}\n{marker}\n"


@pytest.mark.parametrize("marker,language", [("```", "svg"), ("~~~~", "SVG")])
def test_svg_fence_becomes_real_png_and_searchable_text(marker, language):
    original = "# Saved Note\n\nBefore.\n\n" + fenced(marker=marker, language=language) + "\nAfter."
    content, images = extract_markdown_images(original)
    assert len(images) == 1
    image = images[0]
    assert image.image_format == "png"
    assert image.is_diagram
    with Image.open(BytesIO(image.image_bytes)) as png:
        assert png.format == "PNG"
        assert png.width / png.height == pytest.approx(640 / 240, abs=0.02)
        assert png.width <= 2560 and png.height <= 2560
    assert ":::image" in content and image.image_id in content
    assert "<svg" not in content and "<path" not in content and "url(#arrow)" not in content
    assert "投资人 A" in content and "Entity B" in content
    assert "Entity A provides financing" in content
    assert "Before." in content and "After." in content


def test_ordinary_code_and_nested_svg_examples_remain_literal():
    example = "````markdown\n" + fenced() + "````\n\n```python\nprint('hello')\n```\n"
    content, images = extract_markdown_images(example)
    assert images == []
    assert content == example


@pytest.mark.parametrize("bad_svg", [
    '<svg xmlns="http://www.w3.org/2000/svg"><path></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><image href="file:///private/image.png"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><style>@import "https://example.com/style.css";</style></svg>',
    '<!DOCTYPE svg [<!ENTITY secret SYSTEM "file:///etc/passwd">]><svg xmlns="http://www.w3.org/2000/svg">&secret;</svg>',
])
def test_invalid_or_external_svg_fails_before_chunking(bad_svg):
    with pytest.raises(ValueError, match="SVG"):
        extract_markdown_images(fenced(bad_svg))


def test_incomplete_svg_fence_is_not_ingested_as_broken_code():
    with pytest.raises(ValueError, match="SVG"):
        extract_markdown_images("```svg\n" + SVG)


def test_source_limit_and_extreme_dimensions_are_bounded():
    with pytest.raises(ValueError, match="SVG"):
        extract_markdown_images(fenced(SVG.replace("</svg>", "<!--" + "x" * (256 * 1024) + "--></svg>")))
    _, images = extract_markdown_images(fenced('<svg xmlns="http://www.w3.org/2000/svg" width="1000000" height="800000"><rect width="100%" height="100%" fill="green"/></svg>'))
    assert images
    with Image.open(BytesIO(images[0].image_bytes)) as png:
        assert max(png.size) <= 2560


@pytest.mark.parametrize("parent_child", [False, True])
def test_long_svg_is_an_atomic_image_in_normal_and_parent_child_chunks(tmp_path, parent_child):
    long_svg = SVG.replace("</svg>", "\n".join(f'<text x="20" y="{i+20}">Stage {i}: A → B</text>' for i in range(100)) + "</svg>")
    content, images = extract_markdown_images("Intro.\n\n" + fenced(long_svg) + "\nClosing.")
    assert len(images) == 1
    doc = process_document_images(ParsedDocument(content, images=images), "file-svg", tmp_path, describe=False, ocr=False)
    chunker = MarkdownParentChildChunker(child_chunk_size=64) if parent_child else MarkdownChunker(max_tokens=64)
    chunks = chunker.chunk_with_metadata(doc.content, source="__note__:test-svg")
    annotate_chunks_with_images(chunks, doc.images)
    image_chunks = [c for c in chunks if ":::image" in c.text]
    assert image_chunks
    assert all("<svg" not in c.text and "<path" not in c.text for c in chunks)
    for chunk in image_chunks:
        assert chunk.text.count(":::image") == 1
        refs = chunk.metadata["images"]
        assert refs[0]["image_id"] == images[0].image_id
        assert refs[0]["file_id"] == "file-svg"
        assert (tmp_path / "images" / f"{refs[0]['image_id']}.png").exists()


def test_markdown_parser_used_by_reingestion_preserves_the_source_file(tmp_path):
    path = tmp_path / "note.md"
    source = "# Note\n\n" + fenced()
    path.write_text(source)
    doc = MarkdownParser().parse(path)
    assert len(doc.images) == 1
    assert ":::image" in doc.content and "<svg" not in doc.content
    assert path.read_text() == source


def test_tiny_deliberate_diagram_is_not_filtered_as_a_logo(tmp_path):
    content, images = extract_markdown_images(fenced('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20" fill="green"/></svg>'))
    assert len(images) == 1
    doc = process_document_images(ParsedDocument(content, images=images), "small-svg", tmp_path, describe=False, ocr=False)
    assert len(doc.images) == 1
    assert ":::image" in doc.content


def test_svg_source_labels_survive_ocr_classification(monkeypatch):
    from src.parsers.image_utils import ocr_classify_document_images

    content, images = extract_markdown_images(fenced())
    doc = ParsedDocument(content, images=images)
    monkeypatch.setattr("src.parsers.image_utils._classify_image", lambda *args: ("text", "noisy OCR"))
    ocr_classify_document_images(doc)
    assert "投资人 A" in doc.images[0].ocr_text
    assert "noisy OCR" not in doc.content
    assert doc.images[0]._ocr_kind == "visual"  # arrows and layout still need visual interpretation


@pytest.fixture
def ingest_pipeline(tmp_path, monkeypatch):
    from src.notes import routes, store
    from src.tasks import handlers

    db = MagicMock()
    db.collection_exists.return_value = True
    db.get_collection_config.return_value = {
        "chunk_mode": "parent_child", "child_chunk_size": 64,
        "contextual_enabled": False, "cloud_parsing": False,
    }
    embedding = SimpleNamespace(dimensions=2, embed_texts=lambda texts: [[1., 1.] for _ in texts])
    service = SimpleNamespace(db=db, config=SimpleNamespace(mineru=None), embedding=embedding, catalog=None)
    monkeypatch.setattr(routes, "services", service)
    monkeypatch.setattr(handlers, "services", service)
    monkeypatch.setattr(routes, "get_collection_embedding", lambda *args: embedding)
    monkeypatch.setattr(handlers, "get_collection_embedding", lambda *args: embedding)
    monkeypatch.setattr("src.rag.contextual.resolve_ingest_vision", lambda: (None, ""))
    monkeypatch.setattr("src.rag.contextual.resolve_enrichment_target", lambda *args: (None, ""))
    monkeypatch.setattr("src.rag.contextual.enrichment_model_is_visual", lambda *args: False)
    monkeypatch.setattr("src.rag.contextual.run_ingest_summary", lambda *args, **kwargs: {})

    def enrich(chunks, *args, **kwargs):
        callback = kwargs.get("on_chunk_ready")
        if callback:
            for chunk in chunks:
                callback(chunk, "")
        return chunks

    monkeypatch.setattr(routes, "_do_enrich", enrich)
    monkeypatch.setattr(handlers, "_do_enrich", enrich)
    monkeypatch.setattr(handlers, "_bump_sparse_recalc_counter", lambda *args: None)
    monkeypatch.setattr(handlers, "_after_ingest_definitive_followup", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.file_mgmt.definitive_suggestions.schedule_definitive_suggestion_judge", lambda *args: None)
    monkeypatch.setattr("src.collections.file_index.record_ingest_chunks", lambda *args: None)
    monkeypatch.setattr("src.file_mgmt.service.register_ingested_source_file", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.collections.file_index.load", lambda *args: {})
    monkeypatch.setattr("src.identity.get_actor", lambda: SimpleNamespace(id="svg-test", kind="local", role_id="owner"))
    monkeypatch.setattr(store, "NOTES_DIR", tmp_path / "notes")

    def files_dir(collection, file_id):
        directory = tmp_path / "files" / file_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    monkeypatch.setattr("src.collections.file_index.ensure_files_dir", files_dir)
    return db, files_dir


def test_note_ingest_indexes_png_evidence_without_changing_editable_svg(ingest_pipeline):
    from src.notes import routes, store

    db, files_dir = ingest_pipeline
    note = store.create_note("svg-col", "Diagram")
    original = "# Saved Note\n\n" + fenced()
    store.save_content(note.id, original)
    routes._do_ingest_note("svg-col", note.id, note.title, original)
    payloads = db.upsert_points.call_args.kwargs["payloads"]
    image_payloads = [p for p in payloads if p.get("images")]
    assert image_payloads
    file_id = next(p["file_id"] for p in image_payloads if p.get("file_id"))
    for payload in image_payloads:
        assert "<svg" not in payload["text"]
        assert "投资人 A" in payload["text"]
        ref = payload["images"][0]
        assert ref["file_id"] == file_id
        assert (files_dir("svg-col", ref["file_id"]) / "images" / f"{ref['image_id']}.png").exists()
    assert store.get_content(note.id) == original


def test_version_ingest_regenerates_png_after_svg_edit(ingest_pipeline):
    from src.tasks import handlers
    from src.tasks.task_manager import Task

    db, files_dir = ingest_pipeline
    rendered = []
    for version, label in [("v1", "Entity B"), ("v2", "Entity C")]:
        directory = files_dir("svg-col", version)
        path = directory / "note.md"
        original = fenced(SVG.replace("Entity B", label))
        path.write_text(original)
        asyncio.run(handlers.upload_handler(
            Task(id=f"svg-{version}", filename="note.md"), str(path), "svg-col",
            "__note__:svg-note", file_id=version, version_id=version,
        ))
        payloads = db.upsert_points.call_args.kwargs["payloads"]
        visual = next(p for p in payloads if p.get("images"))
        assert label in visual["text"] and "<svg" not in visual["text"]
        assert visual["version_id"] == version
        ref = visual["images"][0]
        assert ref["file_id"] == version
        rendered.append((directory / "images" / f"{ref['image_id']}.png").read_bytes())
        assert path.read_text() == original
    assert rendered[0] != rendered[1]
