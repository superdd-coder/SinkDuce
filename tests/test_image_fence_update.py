from src.parsers.base import ImageInfo
from src.parsers.image_utils import (
    _update_description_in_content,
    build_image_block,
)


def _img(**kwargs) -> ImageInfo:
    data = {
        "image_id": "aabbccddeeff00112233445566778899",
        "file_id": "file001",
        "ocr_text": "",
        "description": "",
    }
    data.update(kwargs)
    return ImageInfo(**data)


def test_normalize_raster_image_and_save_rewrites_office_ext(tmp_path):
    import io
    from PIL import Image
    from src.parsers.image_utils import normalize_raster_image, _save_image_to_disk

    png_bytes = _tiny_png()
    out = normalize_raster_image(png_bytes, "x-wmf")
    assert out is not None
    data, fmt = out
    assert fmt == "png"
    Image.open(io.BytesIO(data)).verify()

    img = _img(image_bytes=png_bytes, image_format="x-wmf")
    img.image_id = "aabbccddeeff00112233445566778899"
    assert _save_image_to_disk(tmp_path, img)
    assert img.image_format == "png"
    assert (tmp_path / "images" / f"{img.image_id}.png").is_file()


def test_save_unreadable_bytes_still_writes_file(tmp_path):
    from src.parsers.image_utils import _save_image_to_disk

    img = _img(image_bytes=b"not-an-image", image_format="x-wmf")
    img.image_id = "bbccddeeff00112233445566778899aa"
    # Not displayable — fence should be dropped — but bytes stay on disk.
    assert _save_image_to_disk(tmp_path, img) is False
    saved = list((tmp_path / "images").glob(f"{img.image_id}.*"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == b"not-an-image"


def test_normalize_skips_office_vector_without_pillow():
    from src.parsers.image_utils import normalize_raster_image, sniff_raster_format

    raw = b"\xd7\xcd\xc6\x9a" + b"\x00" * 64
    assert sniff_raster_format(raw) is None
    assert normalize_raster_image(raw, "x-wmf") is None
    assert normalize_raster_image(b"not-an-image", "emf") is None


def test_normalize_still_opens_png_labeled_as_wmf():
    from src.parsers.image_utils import normalize_raster_image

    out = normalize_raster_image(_tiny_png(), "x-wmf")
    assert out is not None
    assert out[1] == "png"


def test_ocr_downscales_before_rapidocr(monkeypatch):
    import io
    from PIL import Image
    from src.parsers.image_utils import OCR_MAX_SIDE, _ocr_image

    buf = io.BytesIO()
    Image.new("RGB", (2400, 800), (30, 30, 30)).save(buf, format="PNG")
    seen: dict = {}

    def fake_ocr(img):
        seen["size"] = img.size
        return "", 0.0

    monkeypatch.setattr("src.parsers.rapid_ocr.ocr_array", fake_ocr)
    text, conf = _ocr_image(buf.getvalue(), image_format="png")
    assert text == ""
    assert conf == 0.0
    assert max(seen["size"]) <= OCR_MAX_SIDE


def test_ocr_skips_office_vector():
    from src.parsers.image_utils import _ocr_image

    text, conf = _ocr_image(b"\xd7\xcd\xc6\x9a" + b"\x00" * 32, image_format="x-wmf")
    assert text == ""
    assert conf == 0.0


def test_rapidocr_warmup_logs_ready(monkeypatch):
    from src.parsers import rapid_ocr

    monkeypatch.setattr(rapid_ocr, "get_engine", lambda: object())
    assert rapid_ocr.warmup() is True


def test_rapidocr_warmup_failure_is_nonfatal(monkeypatch):
    from src.parsers import rapid_ocr

    def _boom():
        raise RuntimeError("no weights")

    monkeypatch.setattr(rapid_ocr, "get_engine", _boom)
    assert rapid_ocr.warmup() is False


def test_bundled_rapidocr_models_exist():
    from src.parsers.rapid_ocr import _CLS, _DET, _REC, bundled_model_dir

    d = bundled_model_dir()
    assert (d / _DET).is_file()
    assert (d / _REC).is_file()
    assert (d / _CLS).is_file()


def test_rapidocr_downscales_large_array():
    import numpy as np
    from src.parsers.rapid_ocr import OCR_MAX_SIDE, _to_rgb_array

    arr = np.zeros((2400, 800, 3), dtype="uint8")
    out = _to_rgb_array(arr)
    assert out is not None
    assert max(out.shape[:2]) <= OCR_MAX_SIDE


def test_ocr_array_uses_engine(monkeypatch):
    from src.parsers import rapid_ocr

    class _Out:
        txts = ("PUMP P-101",)
        scores = (0.97,)

    monkeypatch.setattr(rapid_ocr, "get_engine", lambda: (lambda _img: _Out()))
    text, conf = rapid_ocr.ocr_array(__import__("numpy").zeros((32, 32, 3), dtype="uint8"))
    assert text == "PUMP P-101"
    assert 96 <= conf <= 98


def test_describe_one_skips_on_timeout():
    from src.parsers.image_utils import _describe_one

    class _LLM:
        def describe_image(self, *_a, **_k):
            raise TimeoutError("The read operation timed out")

    out = _describe_one(_img(image_bytes=_tiny_png()), _LLM(), prompt="describe")
    assert out == ""


def _tiny_png() -> bytes:
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (20, 80, 40)).save(buf, format="PNG")
    return buf.getvalue()


def test_find_image_file_globs_office_extension(tmp_path, monkeypatch):
    from src.file_mgmt import storage_paths as sp

    col = tmp_path / "col_x" / "files" / "file_x" / "ver_x" / "images"
    col.mkdir(parents=True)
    target = col / "aabbccddeeff00112233445566778899.x-wmf"
    target.write_bytes(b"wmf-bytes")
    monkeypatch.setattr(sp, "COLLECTIONS_DIR", tmp_path)
    monkeypatch.setattr(
        sp, "managed_file_dir",
        lambda c, f: tmp_path / c / "files" / f,
    )
    found = sp.find_image_file("col_x", "file_x", "aabbccddeeff00112233445566778899")
    assert found == target


def test_is_unretryable_image_error():
    from src.providers.retry import is_unretryable_image_error

    err = RuntimeError(
        "Error code: 400 - {'error': {'message': '<400> InternalError.Algo.InvalidParameter: "
        "The image format is illegal and cannot be opened'}}"
    )
    assert is_unretryable_image_error(err)
    assert not is_unretryable_image_error(RuntimeError("429 throttling"))


def test_build_image_block_keeps_description_inside_fence():
    block = build_image_block(_img(description="A pump skid next to tank T-101."))
    assert block.startswith(":::image\n")
    assert block.rstrip().endswith(":::")
    inner = block[len(":::image\n") :].rsplit(":::", 1)[0]
    assert "description: A pump skid next to tank T-101." in inner
    assert inner.count("\n") >= 3


def test_multiline_description_is_one_fence_line():
    desc = "First sentence.\n\nSecond sentence about the figure."
    block = build_image_block(_img(description=desc))
    assert "Second sentence" in block
    assert block.count(":::image") == 1
    after_close = block.split(":::")[-1]
    assert "Second sentence" not in after_close
    desc_lines = [ln for ln in block.splitlines() if ln.startswith("description:")]
    assert len(desc_lines) == 1
    assert "\n" not in desc_lines[0]


def test_update_writes_description_inside_empty_fence():
    content = (
        "Intro.\n\n"
        ":::image\n"
        "image_id: aabbccddeeff00112233445566778899\n"
        "file_id: \n"
        "description: \n"
        ":::\n\n"
        "Caption after the figure.\n"
    )
    out = _update_description_in_content(
        content, _img(file_id="file001", description="Aerial view of the lagoon.")
    )
    assert "description: Aerial view of the lagoon." in out
    fence, rest = out.split(":::", 2)[1], out.split(":::")[-1]
    assert "Aerial view of the lagoon." in fence
    assert "Aerial view of the lagoon." not in rest
    assert "Caption after the figure." in rest


def test_update_after_multiline_ocr_still_puts_description_inside():
    content = (
        ":::image\n"
        "image_id: aabbccddeeff00112233445566778899\n"
        "file_id: file001\n"
        "ocr_text: Line one of OCR\n"
        "Line two of OCR still in the old fence\n"
        ":::\n"
        "Following table row.\n"
    )
    out = _update_description_in_content(
        content,
        _img(ocr_text="Line one of OCR\nLine two of OCR", description="Process flow of unit A."),
    )
    assert "description: Process flow of unit A." in out
    body_after = out.split(":::")[-1]
    assert "Process flow of unit A." not in body_after
    assert "Following table row." in body_after


def test_update_preserves_table_after_fence_and_backslash_in_desc():
    content = (
        ":::image\n"
        "image_id: aabbccddeeff00112233445566778899\n"
        "file_id: file001\n"
        ":::\n\n"
        "| Col | Val |\n| --- | --- |\n| a | 1 |\n"
    )
    out = _update_description_in_content(
        content, _img(description=r"Path hint C:\next\unit on the P&ID.")
    )
    assert r"C:\next\unit" in out
    assert "| Col | Val |" in out
    close = out.index("\n:::")
    assert out.index("description:") < close
    assert close < out.index("| Col | Val |")


def test_refresh_chunk_image_refs_reads_fence_ocr():
    from src.parsers.image_utils import refresh_chunk_image_refs

    text = (
        ":::image\n"
        "image_id: aabbccddeeff00112233445566778899\n"
        "file_id: file001\n"
        "ocr_text: PUMP P-101\n"
        "description: Discharge pump skid.\n"
        ":::\n"
    )
    refs = refresh_chunk_image_refs(
        text,
        existing=[{
            "image_id": "aabbccddeeff00112233445566778899",
            "ocr_text": "",
            "description": "",
            "page_number": 5,
        }],
    )
    assert len(refs) == 1
    assert refs[0]["ocr_text"] == "PUMP P-101"
    assert refs[0]["description"] == "Discharge pump skid."
    assert refs[0]["page_number"] == 5


def test_parent_child_packs_image_with_neighbors_when_child_budget_allows():
    from src.rag.markdown_chunker import MarkdownParentChildChunker

    fence = (
        ":::image\n"
        "image_id: aabbccddeeff00112233445566778899\n"
        "file_id: file1\n"
        ":::\n"
    )
    text = (
        "## Plant photo\n\n"
        "Intro sentence.\n\n"
        f"{fence}\n"
        "Caption prose after the figure.\n"
    )
    chunks = MarkdownParentChildChunker(
        child_chunk_size=512, child_buffer_ratio=0.5,
    ).chunk_with_metadata(text, source="t.md")
    children = [c for c in chunks if c.chunk_type == "child"]
    image_children = [c for c in children if "aabbccddeeff00112233445566778899" in c.text]
    assert image_children
    assert any("Intro sentence" in c.text and ":::image" in c.text for c in image_children)


def test_ocr_classify_keeps_usable_text_on_visual_images(monkeypatch):
    from src.parsers.base import ParsedDocument
    from src.parsers.image_utils import ocr_classify_document_images

    monkeypatch.setattr(
        "src.parsers.image_utils._classify_image",
        lambda _b, _fmt="", lang="eng": ("visual", "PUMP P-101 DISCHARGE"),
    )
    monkeypatch.setattr("src.parsers.image_utils._ocr_text_is_garbage", lambda _t: False)
    img = _img(image_bytes=b"fakepng", ocr_text="")
    doc = ParsedDocument(
        content=(
            ":::image\n"
            "image_id: aabbccddeeff00112233445566778899\n"
            "file_id: file001\n"
            ":::\n"
        ),
        images=[img],
    )
    ocr_classify_document_images(doc)
    assert doc.images[0].ocr_text == "PUMP P-101 DISCHARGE"
    assert "ocr_text: PUMP P-101 DISCHARGE" in doc.content


def test_describe_one_backs_off_on_429(monkeypatch):
    from src.parsers.image_utils import _describe_one

    sleeps: list[float] = []
    monkeypatch.setattr("src.parsers.image_utils.time.sleep", sleeps.append)
    monkeypatch.setattr("src.providers.retry.random.uniform", lambda a, b: 0.0)
    hits = {"n": 0}

    class _LLM:
        def describe_image(self, *_a, **_k):
            hits["n"] += 1
            if hits["n"] < 3:
                raise RuntimeError("Error code: 429 Throttling")
            return "A process figure."

    out = _describe_one(_img(image_bytes=_tiny_png()), _LLM(), prompt="describe")
    assert out == "A process figure."
    assert hits["n"] == 3
    assert sleeps == [2.0, 4.0]


def test_describe_does_not_wait_for_ocr(monkeypatch):
    from src.parsers.base import ParsedDocument
    from src.parsers.image_utils import describe_document_images

    def _ocr_boom(*_a, **_k):
        raise AssertionError("Vision must not run OCR first")

    monkeypatch.setattr("src.parsers.image_utils.ocr_classify_document_images", _ocr_boom)

    def _fake_describe(images, *_a, **_k):
        for img in images:
            img.description = "A process figure."
        return list(images)

    monkeypatch.setattr("src.parsers.image_utils.describe_images", _fake_describe)
    img = _img(image_bytes=b"fakepng", description="")
    doc = ParsedDocument(
        content=(
            ":::image\n"
            "image_id: aabbccddeeff00112233445566778899\n"
            "file_id: file001\n"
            ":::\n"
        ),
        images=[img],
    )
    describe_document_images(
        doc,
        vision_provider=object(),
        vision_model_id="vision-x",
        vision_prompt="describe",
        write_content=False,
        clear_bytes=False,
    )
    assert img.description == "A process figure."
    assert img.image_bytes == b"fakepng"


def test_process_drops_unreadable_vector_fence(tmp_path):
    from src.parsers.base import ParsedDocument
    from src.parsers.image_utils import process_document_images

    img = _img(image_bytes=b"\xd7\xcd\xc6\x9a" + b"\x00" * 32, image_format="x-wmf")
    img.image_id = "ccdddeeff00112233445566778899aa"
    doc = ParsedDocument(
        content=(
            "Before.\n\n"
            ":::image\n"
            f"image_id: {img.image_id}\n"
            "file_id: \n"
            ":::\n\n"
            "After.\n"
        ),
        images=[img],
    )
    out = process_document_images(doc, "file001", tmp_path, describe=False, ocr=False)
    assert out.images == []
    assert ":::image" not in out.content
    assert "Before." in out.content and "After." in out.content
    saved = list((tmp_path / "images").glob(f"{img.image_id}.*"))
    assert len(saved) == 1


def test_filter_without_ocr_does_not_classify(monkeypatch, tmp_path):
    from src.parsers.base import ParsedDocument
    from src.parsers.image_utils import process_document_images

    called = {"n": 0}

    def _boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("OCR should not run during filter-only pass")

    monkeypatch.setattr("src.parsers.image_utils._classify_image", _boom)
    monkeypatch.setattr("src.parsers.image_utils.filter_images", lambda imgs: list(imgs))
    img = _img(image_bytes=_tiny_png(), description="")
    img.image_id = "aabbccddeeff00112233445566778899"
    doc = ParsedDocument(
        content=(
            ":::image\n"
            "image_id: aabbccddeeff00112233445566778899\n"
            "file_id: \n"
            ":::\n"
        ),
        images=[img],
    )
    out = process_document_images(
        doc, "file001", tmp_path, describe=False, ocr=False,
    )
    assert called["n"] == 0
    assert out.images
    assert out.images[0].image_bytes  # kept for later OCR / Vision
    assert not out.images[0].ocr_text


def test_find_spans_handles_crlf_and_empty_description_line():
    from src.parsers.image_utils import _find_image_block_spans, _update_description_in_content

    crlf = (
        ":::image\r\n"
        "image_id: aabbccddeeff00112233445566778899\r\n"
        "file_id: \r\n"
        "description: \r\n"
        ":::\r\n"
        "Caption after.\r\n"
    )
    spans = _find_image_block_spans(crlf, "aabbccddeeff00112233445566778899")
    assert len(spans) == 1
    start, end = spans[0]
    assert crlf[start:end].strip().endswith(":::")
    assert "Caption after" not in crlf[start:end]

    out = _update_description_in_content(
        crlf, _img(file_id="file001", description="A cooling tower.")
    )
    assert "description: A cooling tower." in out
    after = out.split(":::")[-1]
    assert "A cooling tower." not in after
    assert "Caption after." in after


def test_apply_updates_rewrites_empty_visual_fence():
    from src.rag.chunker import Chunk
    from src.parsers.image_utils import apply_image_updates_to_chunks

    chunk = Chunk(
        text=(
            ":::image\n"
            "image_id: aabbccddeeff00112233445566778899\n"
            "file_id: \n"
            "description: \n"
            ":::\n"
        ),
        metadata={},
    )
    apply_image_updates_to_chunks(
        [chunk],
        [_img(file_id="file001", description="Intake works photograph.", ocr_text="INTAKE 100")],
    )
    assert "file_id: file001" in chunk.text
    assert "description: Intake works photograph." in chunk.text
    assert "ocr_text: INTAKE 100" in chunk.text
    assert chunk.text.rstrip().endswith(":::")
    refs = chunk.metadata.get("images") or []
    assert refs
    assert refs[0]["ocr_text"] == "INTAKE 100"
    assert refs[0]["description"] == "Intake works photograph."


def test_update_rewrites_every_slice_with_same_image_id():
    fence = (
        ":::image\n"
        "image_id: aabbccddeeff00112233445566778899\n"
        "file_id: file001\n"
        ":::\n"
    )
    content = fence + "| r1 |\n\n" + fence + "| r2 |\n"
    out = _update_description_in_content(content, _img(description="Cost table figure."))
    assert out.count("description: Cost table figure.") == 2
