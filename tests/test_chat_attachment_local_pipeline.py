import base64
import io
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

from src.chat_attachments import service
from src.chat_attachments.store import AttachmentStore


def pdf_bytes(texts):
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    kids = []
    for text in texts:
        page = len(objects) + 1
        kids.append(f"{page} 0 R")
        content = f"0 0 1 rg 30 30 60 40 re f BT /F1 12 Tf 36 720 Td ({text}) Tj ET".encode()
        objects.extend([
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {page+2} 0 R >> >> /Contents {page+1} 0 R >>".encode(),
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ])
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(texts)} >>".encode()
    out, offsets = b"%PDF-1.4\n", [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(out)
    out += f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    return out + f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()


@pytest.fixture
def store(tmp_path):
    return AttachmentStore(tmp_path / "attachments")


def test_chat_pdf_uses_local_preparation_even_when_mineru_is_configured(store, monkeypatch):
    cloud = MagicMock(side_effect=RuntimeError("Chat must not call MinerU"))
    monkeypatch.setattr(service, "parse_with_mineru", cloud, raising=False)
    a = store.create("chat", "report.pdf", pdf_bytes(["First page", "Second page"]))
    service.parse_attachment(store, "chat", a.id, SimpleNamespace(enabled=True, api_token="configured"))
    cloud.assert_not_called()
    result = store.get("chat", a.id)
    assert result.status == "ready", result.error
    assert result.parser == "local"
    assert "First page" in store.text("chat", a.id)
    assert "Second page" in store.text("chat", a.id)


def test_pdf_preparation_keeps_all_page_references_without_ocr_or_eager_render(store, monkeypatch):
    ocr = MagicMock(side_effect=AssertionError("Visual preparation must skip OCR"))
    monkeypatch.setattr("src.parsers.image_utils._ocr_image", ocr)
    a = store.create("chat", "scan.pdf", pdf_bytes(["", "", ""]))
    service.parse_attachment(store, "chat", a.id, None)
    result = store.get("chat", a.id)
    assert result.status == "ready", result.error
    assert result.image_count == 3
    assert [p["page_number"] for p in store.parsed("chat", a.id)["images"]] == [1, 2, 3]
    assert not list((store.root / "chat" / a.id).glob("image-*.txt"))
    url = store.image_url("chat", a.id, 1)
    with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as image:
        assert max(image.size) <= 2048
        assert image.getextrema() != ((255, 255), (255, 255), (255, 255))
    assert store.image_url("chat", a.id, 1) == url
    ocr.assert_not_called()


def test_image_upload_defers_ocr_until_a_text_only_model_needs_it(store, monkeypatch):
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "blue").save(buf, format="PNG")
    ocr = MagicMock(return_value=("Detected words", 0.9))
    monkeypatch.setattr("src.parsers.image_utils._ocr_image", ocr)
    a = store.create("chat", "photo.png", buf.getvalue())
    service.parse_attachment(store, "chat", a.id, None)
    ocr.assert_not_called()
    prepare = getattr(service, "prepare_for_model", None)
    assert callable(prepare), "Chat needs model-specific preparation with a reusable cache"
    prepare(store, "chat", [a.id], is_visual=True)
    ocr.assert_not_called()
    original = store.image_url("chat", a.id, 0)
    monkeypatch.setattr("src.rag.contextual.resolve_ingest_vision", lambda: (None, ""))
    prepare(store, "chat", [a.id], is_visual=False)
    assert ocr.call_count == 1
    assert "Detected words" in store.text("chat", a.id)
    prepare(store, "chat", [a.id], is_visual=False)
    assert ocr.call_count == 1
    prepare(store, "chat", [a.id], is_visual=True)
    assert store.image_url("chat", a.id, 0) == original
    assert store.source_path("chat", a.id).read_bytes() == buf.getvalue()


def test_visual_page_reads_are_batched_with_coverage_cursors(store):
    a = store.create("chat", "scan.pdf", pdf_bytes([""] * 7))
    service.parse_attachment(store, "chat", a.id)
    store.bind("chat", [a.id])
    context = service.attachment_context(store, "chat", [a.id], is_visual=True)
    assert sum(p["type"] == "image_url" for p in context) == 4
    header = next(p["text"] for p in context if "Conversation attachment" in p.get("text", ""))
    assert '"attached_image_indices": [0, 1, 2, 3]' in header
    assert '"visual_preview_complete": false' in header
    result = service.execute_attachment_tool(store, "chat", "read_chat_attachment", {
        "attachment_id": a.id, "image_index": 4, "image_count": 4,
    }, is_visual=True)
    assert sum(p["type"] == "image_url" for p in result) == 3
    payload = json.loads(result[0]["text"])
    assert payload["next_image_index"] is None
    assert payload["attached_image_indices"] == [4, 5, 6]
    assert [r["page_number"] for r in payload["images"]] == list(range(1, 8))


def test_pptx_falls_back_to_text_when_existing_renderer_times_out(store, monkeypatch):
    import subprocess
    from pptx import Presentation
    from src.chat_attachments import rendering
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "Quarterly chart"
    output = io.BytesIO()
    presentation.save(output)
    monkeypatch.setattr(rendering, "office_pdf", MagicMock(side_effect=subprocess.TimeoutExpired("renderer", 90)))
    a = store.create("chat", "deck.pptx", output.getvalue())
    service.parse_attachment(store, "chat", a.id)
    parsed = store.get("chat", a.id)
    assert parsed.status == "ready", parsed.error
    assert "Quarterly chart" in store.text("chat", a.id)
    assert any("layout" in w for w in parsed.warnings)


def test_legacy_ppt_is_available_only_with_an_existing_local_renderer(monkeypatch):
    from src.chat_attachments import rendering
    monkeypatch.setattr(rendering, "office_binary", lambda: None)
    assert ".ppt" not in service.capabilities()["extensions"]
    monkeypatch.setattr(rendering, "office_binary", lambda: "/existing/soffice")
    assert ".ppt" in service.capabilities()["extensions"]
    assert service.capabilities(SimpleNamespace(enabled=True, api_token="token"))["cloud_only"] == []


def test_native_text_pdf_needs_no_ocr_or_raster_for_text_model(store, monkeypatch):
    from src.chat_attachments import rendering
    data = pdf_bytes(["Exact native text"]).replace(b"0 0 1 rg 30 30 60 40 re f ", b" " * 30)
    # Keep stream lengths/xref offsets stable when removing the vector rectangle.
    a = store.create("chat", "text.pdf", data)
    service.parse_attachment(store, "chat", a.id)
    monkeypatch.setattr("src.rag.contextual.resolve_ingest_vision", lambda: (None, ""))
    render = MagicMock(wraps=rendering.render_page)
    monkeypatch.setattr(rendering, "render_page", render)
    service.prepare_for_model(store, "chat", [a.id], is_visual=False)
    render.assert_not_called()
    assert store.text("chat", a.id).strip() == "Exact native text"
    assert store.get("chat", a.id).warnings == []


def test_image_descriptions_are_cached_and_refreshed_when_description_model_changes(store, monkeypatch):
    output = io.BytesIO()
    Image.new("RGB", (64, 64), "blue").save(output, format="PNG")
    a = store.create("chat", "chart.png", output.getvalue())
    service.parse_attachment(store, "chat", a.id)
    provider = SimpleNamespace(id="provider", base_url="https://test/v1", api_key="secret")
    target = ["model-a"]
    monkeypatch.setattr("src.rag.contextual.resolve_ingest_vision", lambda: (provider, target[0]))
    monkeypatch.setattr("src.parsers.image_utils._ocr_image", lambda *a, **kw: ("", 0))
    calls = []
    def describe(doc, **kw):
        calls.append(kw["vision_model_id"])
        for img in doc.images:
            if not img.description:
                img.description = "Description by " + kw["vision_model_id"]
    monkeypatch.setattr("src.parsers.image_utils.describe_document_images", describe)
    service.prepare_for_model(store, "chat", [a.id], is_visual=True)
    assert not calls
    service.prepare_for_model(store, "chat", [a.id], is_visual=False)
    service.prepare_for_model(store, "chat", [a.id], is_visual=False)
    assert calls == ["model-a"]
    target[0] = "model-b"
    service.prepare_for_model(store, "chat", [a.id], is_visual=False)
    assert "Description by model-b" in store.text("chat", a.id)
    assert "Description by model-a" not in store.text("chat", a.id)


def test_page_read_history_restores_originals_for_followup_and_text_for_model_switch(store, monkeypatch):
    a = store.create("chat", "scan.pdf", pdf_bytes([""] * 3))
    service.parse_attachment(store, "chat", a.id)
    store.bind("chat", [a.id])
    result = service.execute_attachment_tool(store, "chat", "read_chat_attachment", {
        "attachment_id": a.id, "image_index": 1,
    }, is_visual=True)
    messages = [{"role": "tool", "content": result[0]["text"]}, {"role": "user", "content": "Explain that figure"}]
    restore = getattr(service, "hydrate_attachment_history", None)
    assert callable(restore), "Attachment page reads need cross-turn image memory"
    restored = restore(store, "chat", messages, is_visual=True)
    assert restored[0]["content"][1] == result[1]
    assert isinstance(messages[0]["content"], str), "Projection must leave stored history unchanged"
    parsed = store.parsed("chat", a.id)
    parsed["images"][1]["ocr"] = "Machine reading of page two"
    store.finish("chat", a.id, content="", position_map=parsed["position_map"], images=parsed["images"], parser="local")
    textual = restore(store, "chat", messages, is_visual=False)
    assert "Machine reading of page two" in textual[0]["content"]
    assert "image_url" not in json.dumps(textual)
    assert restore(store, "other", messages, is_visual=True)[0]["content"] == messages[0]["content"]


def test_deletion_during_lazy_render_does_not_recreate_session_files(store, monkeypatch):
    from src.chat_attachments import rendering
    a = store.create("chat", "scan.pdf", pdf_bytes([""]))
    service.parse_attachment(store, "chat", a.id)
    def render(*args):
        store.purge("chat")
        return "data:image/png;base64,AA=="
    monkeypatch.setattr(rendering, "render_page", render)
    with pytest.raises(FileNotFoundError):
        store.image_url("chat", a.id, 0)
    assert not (store.root / "chat").exists()


def test_page_render_resolution_is_bounded_before_pdfium_allocates_a_large_bitmap(monkeypatch):
    import pdfplumber
    from pathlib import Path
    from src.chat_attachments.rendering import render_page
    page = MagicMock(width=10000, height=20000)
    def render(*, resolution):
        assert max(page.width, page.height) * resolution / 72 <= 2048.01
        return SimpleNamespace(original=Image.new("RGB", (100, 200), "blue"))
    page.to_image.side_effect = render
    pdf = MagicMock()
    pdf.__enter__.return_value.pages = [page]
    monkeypatch.setattr(pdfplumber, "open", lambda path: pdf)
    assert render_page(Path("large.pdf"), 1).startswith("data:image/jpeg;base64,")


def test_native_text_only_pages_do_not_trigger_a_vision_probe_or_inline_images(store, tmp_path, monkeypatch):
    from src.chatbox.agent import ChatboxAgent
    from src.db.sessions import SessionStore
    drawing = b"0 0 1 rg 30 30 60 40 re f "
    data = pdf_bytes(["Native text"]).replace(drawing, b" " * len(drawing))
    a = store.create("chat", "text.pdf", data)
    service.parse_attachment(store, "chat", a.id)
    store.bind("chat", [a.id])
    sessions = SessionStore(str(tmp_path / "native.db"))
    sessions.create_session(session_id="chat")
    agent = ChatboxAgent(sessions, SimpleNamespace(supports_vision=False), None)
    agent._attachments = store
    probe = MagicMock(return_value=False)
    monkeypatch.setattr("src.chat_attachments.vision.ensure_chat_vision", probe)
    monkeypatch.setattr("src.rag.contextual.resolve_ingest_vision", lambda: (None, ""))
    agent._prepare_chat_attachments("chat", [a.id])
    probe.assert_not_called()
    context = service.attachment_context(store, "chat", [a.id], is_visual=True)
    assert all(p["type"] == "text" for p in context)
    assert '"visual_preview_complete": true' in json.dumps(context).replace('\\"', '"')
