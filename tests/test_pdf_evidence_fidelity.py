"""PDF evidence must survive clipping, image filtering and fence rewrites."""

import io
from types import SimpleNamespace

import pytest
from PIL import Image

from src.parsers.base import ImageInfo, ParsedDocument
from src.parsers.image_utils import (
    _is_too_small, build_image_block, filter_images,
    process_document_images, rewrite_image_fences_in_document,
)
from src.parsers.pdf import _extract_page_images, _parse_page_elements
from src.rag.chunker import _annotate_position


def png(size=(320, 320)):
    stream = io.BytesIO()
    Image.new("RGB", size, "white").save(stream, format="PNG")
    return stream.getvalue()


class Page:
    bbox = (0, 0, 300, 400)
    width, height = 300, 400

    def __init__(self, box, broken_crop=False):
        self.images = [dict(zip(("x0", "top", "x1", "bottom"), box))]
        self.calls = []
        self.broken_crop = broken_crop

    def within_bbox(self, box):
        self.calls.append(box)
        if (box[0] < 0 or box[1] < 0 or box[2] > 300 or box[3] > 400):
            raise ValueError("Bounding box is not fully within parent page")
        if self.broken_crop:
            raise ValueError("region rendering failed")
        return self

    def to_image(self, resolution):
        return SimpleNamespace(original=Image.new("RGB", (625, 834), "white"))


@pytest.mark.parametrize("box", [(0, -0.000004, 300, 400.000001), (12, -38, 280, 340)])
def test_image_boxes_are_clipped_to_page_before_rendering(box):
    page = Page(box)
    images = _extract_page_images(page, 1)
    assert images[0].image_bytes, "Recoverable image silently became no-bytes"
    assert all(b[1] >= 0 and b[3] <= 400 for b in page.calls)


def test_failed_region_render_preserves_whole_page_evidence():
    page = Page((10, 10, 290, 390), broken_crop=True)
    images = _extract_page_images(page, 1)
    assert images[0].image_bytes, "A failed crop must retain the page as fallback"
    assert images[0].is_page_image


def test_vector_diagram_keeps_page_image_even_when_text_layer_exists():
    page = Page((0, 0, 300, 400))
    page.images = []
    page.lines = [object()] * 12
    page.rects, page.curves = [], []
    page.find_tables = lambda: []
    page.extract_text = lambda: "Owner A 35% Owner B 65% Vehicle"
    elements, tables, images = _parse_page_elements(page, 1)
    assert any(getattr(i, "is_page_image", False) and i.image_bytes for i in images)
    assert any(kind == "image" for _, kind, _ in elements)


def test_table_detection_does_not_hide_other_vector_relationships_on_same_page():
    page = Page((0, 0, 300, 400))
    page.images = []
    page.lines, page.rects, page.curves = [object()] * 12, [], []
    page.find_tables = lambda: [SimpleNamespace(
        bbox=(20, 20, 200, 80), extract=lambda: [["Owner", "Value"], ["A", "35%"]],
    )]
    page.extract_text = lambda: "Owner A 35% Owner B 65% Vehicle"
    _, tables, images = _parse_page_elements(page, 1)
    assert tables
    assert any(i.is_page_image and i.image_bytes for i in images)


def test_image_metadata_failure_keeps_original_page_instead_of_returning_no_images():
    class BrokenMetadata(Page):
        @property
        def images(self):
            raise ValueError("cannot decode image dictionary")

    images = _extract_page_images(BrokenMetadata.__new__(BrokenMetadata), 1)
    assert images and images[0].is_page_image and images[0].image_bytes


def test_scanned_page_ocr_is_attributed_to_image_instead_of_primary_pdf_text(monkeypatch):
    from src.parsers import pdf
    from src.rag.evidence_integrity import primary_passage
    page = Page((0, 0, 300, 400))
    page.lines, page.rects, page.curves = [], [], []
    page.find_tables = lambda: []
    page.extract_text = lambda: ""
    monkeypatch.setattr(pdf, "_ocr_page", lambda image: "AUDITOR B misread document heading")
    elements, _, images = _parse_page_elements(page, 1)
    text = "\n".join(value for _, _, value in elements)
    assert "AUDITOR B" in text, "Keep OCR for retrieval and diagnosis"
    assert "AUDITOR B" not in primary_passage(SimpleNamespace(text=text))
    assert any(i.ocr_text for i in images)


def test_failed_vision_description_keeps_original_page_image(tmp_path, monkeypatch):
    from src.parsers import image_utils
    img = ImageInfo("b" * 32, page_number=1, image_bytes=png(), is_page_image=True)
    doc = ParsedDocument(build_image_block(img), images=[img])
    monkeypatch.setattr(image_utils, "_classify_image", lambda *args, **kwargs: ("visual", ""))
    monkeypatch.setattr(image_utils, "describe_images", lambda *args, **kwargs: [])
    process_document_images(doc, "f" * 32, tmp_path, vision_provider=object(), vision_model_id="vision")
    assert doc.images and img.image_id in doc.content
    assert (tmp_path / "images" / (img.image_id + ".png")).is_file()


def test_failure_to_save_primary_page_is_visible_instead_of_dropping_evidence(tmp_path, monkeypatch):
    from src.parsers import image_utils
    img = ImageInfo("b" * 32, page_number=1, image_bytes=png(), is_page_image=True)
    doc = ParsedDocument(build_image_block(img), images=[img])
    monkeypatch.setattr(image_utils, "_save_image_to_disk", lambda *args: False)
    with pytest.raises(ValueError, match="original image"):
        process_document_images(doc, "f" * 32, tmp_path, describe=False, ocr=False)


def test_filter_uses_raster_pixels_instead_of_pdf_point_dimensions():
    assert not _is_too_small((0, 0, 80, 80), png())
    assert _is_too_small((0, 0, 800, 800), png((30, 30)))


def test_repeated_full_page_scans_are_not_discarded_as_logos():
    scans = [ImageInfo(str(n), page_number=n, image_bytes=png()) for n in (1, 2, 3)]
    for img in scans:
        img.is_page_image = True
    assert filter_images(scans) == scans


def test_table_source_is_not_removed_by_small_image_heuristic():
    img = ImageInfo("table", image_bytes=png((200, 40)), bbox=(0, 0, 100, 20), is_table_source=True)
    assert filter_images([img]) == [img]


def two_pages(img):
    first = "Page-one body\n" + build_image_block(img) + "\n"
    second = "Page-two primary evidence"
    return ParsedDocument(first + second, position_map=[
        {"char_offset": 0, "page_number": 1},
        {"char_offset": len(first), "page_number": 2},
    ], images=[img])


@pytest.mark.parametrize("keep", [False, True])
def test_filter_and_file_id_rewrite_keep_later_page_positions_correct(tmp_path, keep):
    img = ImageInfo("a" * 32, image_bytes=png() if keep else None)
    doc = two_pages(img)
    process_document_images(doc, "f" * 32, tmp_path, describe=False, ocr=False)
    offset = doc.content.index("Page-two primary evidence")
    meta = {}
    _annotate_position(meta, offset, doc.position_map)
    assert meta["page_number"] == 2
    assert doc.position_map[1]["char_offset"] == offset


def test_late_ocr_and_vision_rewrites_shift_page_positions():
    img = ImageInfo("a" * 32)
    doc = two_pages(img)
    img.ocr_text = "Recognized source text " * 20
    img.description = "Machine generated figure interpretation " * 10
    rewrite_image_fences_in_document(doc)
    assert doc.position_map[1]["char_offset"] == doc.content.index("Page-two primary evidence")


def test_chunks_keep_valid_offsets_into_final_parsed_text_after_late_image_updates():
    from src.parsers import image_utils
    from src.rag.chunker import Chunk
    img = ImageInfo("a" * 32)
    doc = two_pages(img)
    original = doc.content
    chunk = Chunk("Page-two primary evidence", {"char_offset": original.index("Page-two primary evidence")})
    img.ocr_text = "Late transcription " * 50
    rewrite_image_fences_in_document(doc)
    rebase = getattr(image_utils, "rebase_chunk_positions", None)
    assert callable(rebase), "Late image updates leave chunk character offsets stale"
    rebase([chunk], original, doc)
    assert doc.content[chunk.metadata["char_offset"]:].startswith(chunk.text)
    assert chunk.metadata["page_number"] == 2
