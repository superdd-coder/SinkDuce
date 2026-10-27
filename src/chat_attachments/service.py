from __future__ import annotations

import base64
import io
import hashlib
import json
import logging
import subprocess
import threading
import weakref
from PIL import Image

from src.chat_attachments.store import AttachmentStore
from src.parsers import PARSERS, parse_file
from src.parsers.base import ImageInfo, ParsedDocument
from src.parsers.image_utils import normalize_raster_image
from src.prompts import CHAT_ATTACHMENT_CONTEXT, CHAT_ATTACHMENT_IMAGE_FALLBACK, CHAT_ATTACHMENT_INVENTORY

logger = logging.getLogger(__name__)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".jp2", ".tif", ".tiff"}
TOOL_NAMES = {"list_chat_attachments", "read_chat_attachment", "search_chat_attachment"}
INLINE_CHARS = 16000
READ_CHARS = 16000
_prepare_locks = weakref.WeakValueDictionary()
_prepare_guard = threading.Lock()


def capabilities(cfg=None) -> dict:
    local = set(PARSERS) | IMAGE_EXTENSIONS
    from src.chat_attachments.rendering import office_binary
    if office_binary():
        local.add(".ppt")
    return {"extensions": sorted(local), "cloud_only": [],
            "max_file_bytes": 50 * 1024 * 1024, "max_attachments": 10}


def _image(data: bytes, ext: str, image_id: str) -> dict | None:
    normalized = normalize_raster_image(data, ext)
    if normalized is None:
        return None
    raster, fmt = normalized
    # Keep source bytes untouched; bound only the model-facing rendition.
    with Image.open(io.BytesIO(raster)) as original:
        if max(original.size) > 2048 or len(raster) > 2 * 1024 * 1024:
            original.thumbnail((2048, 2048))
            rendered = original.convert("RGB")
            output = io.BytesIO()
            rendered.save(output, format="JPEG", quality=85)
            raster, fmt = output.getvalue(), "jpeg"
    mime = "image/jpeg" if fmt in {"jpg", "jpeg"} else f"image/{fmt}"
    return {"id": image_id, "url": f"data:{mime};base64,{base64.b64encode(raster).decode()}", "ocr": ""}


def parse_attachment(store: AttachmentStore, sid: str, aid: str, mineru_cfg=None) -> None:
    """Prepare local source evidence without OCR, descriptions or cloud parsing."""
    a = store.get(sid, aid)
    if a is None:
        return
    store.update(sid, aid, status="processing", error="")
    warnings = []
    parser = "local"
    try:
        path = store.source_path(sid, aid)
        ext = path.suffix.lower()
        from src.chat_attachments.rendering import inspect_pdf, office_pdf
        doc = ParsedDocument("")
        images = []
        if ext in IMAGE_EXTENSIONS:
            img = _image(path.read_bytes(), ext, aid)
            if img is None:
                raise ValueError("The image could not be decoded")
            images.append(img)
        elif ext == ".pdf":
            doc.content, doc.position_map, images = inspect_pdf(path)
        elif ext in {".ppt", ".pptx"}:
            try:
                converted = office_pdf(path)
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                logger.warning("Chat presentation rendering unavailable: %s", type(exc).__name__)
                converted = None
            if converted:
                doc.content, doc.position_map, images = inspect_pdf(converted, render_source="pages")
                for ref in images:
                    ref["slide_number"] = ref["page_number"]
            elif ext == ".pptx":
                doc = parse_file(path)
                warnings.append("Whole-slide rendering is unavailable; presentation text and embedded images are available, but charts and layout may be incomplete.")
            else:
                raise ValueError("Legacy PPT requires a local presentation renderer. Save the presentation as PDF or PPTX.")
        else:
            if ext not in PARSERS:
                raise ValueError("No local parser supports this file type")
            if ext == ".xls":
                raise ValueError("Local legacy XLS parsing is unavailable. Save the file as XLSX.")
            doc = parse_file(path)
        for img in doc.images:
            if img.image_bytes:
                item = _image(img.image_bytes, img.image_format, img.image_id)
                if item:
                    item.update(ocr=img.ocr_text or "", page_number=img.page_number, slide_number=img.slide_number)
                    images.append(item)
        if not doc.content.strip() and not images:
            raise ValueError("No readable text or images found in this file")
        # Existing spreadsheet parsers cap at 10k rows. Be explicit at this boundary.
        if ext in {".csv", ".tsv", ".xlsx", ".xls"} and parser == "local" and len(doc.content.splitlines()) >= 10000:
            warnings.append("The local table parser is limited to 10,000 rows per sheet/file; later rows may be omitted.")
        missing = doc.metadata.get("image_render_failures")
        if missing:
            warnings.append(f"Some page images could not be rendered: {missing}")
        store.finish(sid, aid, content=doc.content, position_map=doc.position_map,
                     images=images, parser=parser, warnings=warnings, native=True)
    except Exception as exc:
        logger.warning("Chat attachment parsing failed", exc_info=True)
        store.update(sid, aid, status="failed", error=str(exc)[:500], warnings=warnings)


def prepare_for_model(store: AttachmentStore, sid: str, ids: list[str], *, is_visual: bool) -> None:
    """Cache OCR/description readings only when a text-only recipient needs them."""
    if is_visual:
        return
    from src.parsers.image_utils import build_image_block, describe_document_images, ocr_classify_document_images, rewrite_image_fences_in_document
    from src.rag.contextual import resolve_ingest_vision
    from src.prompts import VISUAL_PROMPT

    provider, model = resolve_ingest_vision()
    target = hashlib.sha256(json.dumps([
        getattr(provider, "id", ""), getattr(provider, "base_url", ""),
        getattr(provider, "api_key", ""), model,
    ]).encode()).hexdigest() if provider and model else ""
    rows = {a.id: a for a in store.list(sid, bound_only=True)}
    for aid in ids:
        if a := store.get(sid, aid):
            rows[aid] = a
    for a in rows.values():
        if a.status != "ready" or not a.image_count:
            continue
        key = (str(store.root), sid, a.id)
        with _prepare_guard:
            lock = _prepare_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                _prepare_locks[key] = lock
        with lock:
            a = store.get(sid, a.id)
            if a is None or (a.text_prepared and a.description_model == target):
                continue
            parsed = store.parsed(sid, a.id)
            refs = parsed["images"]
            doc = ParsedDocument(store.native_text(sid, a.id))
            reading_refs = []
            for index, ref in enumerate(refs):
                if ref.get("text_only"):
                    continue
                url = store.image_url(sid, a.id, index)
                header, encoded = url.split(";base64,", 1)
                img = ImageInfo(ref["id"], image_bytes=base64.b64decode(encoded),
                                image_format=header.rsplit("/", 1)[-1],
                                page_number=ref.get("page_number"), slide_number=ref.get("slide_number"),
                                ocr_text=ref.get("ocr", ""),
                                description=ref.get("description", "") if a.description_model == target else "",
                                is_page_image=ref.get("is_page_image", False))
                if ref.get("ocr_kind"):
                    img._ocr_kind = ref["ocr_kind"]
                elif ref.get("has_text_layer"):
                    img._ocr_kind = "mixed"  # exact native text already exists; only visual details need a description
                doc.images.append(img)
                reading_refs.append(ref)
            pending = ParsedDocument("", images=[img for img in doc.images if not getattr(img, "_ocr_kind", None)])
            ocr_classify_document_images(pending, write_content=False)
            if target:
                describe_document_images(doc, vision_provider=provider, vision_model_id=model,
                                         vision_prompt=VISUAL_PROMPT, write_content=False, clear_bytes=False)
            warnings = [w for w in a.warnings if not w.startswith("Some images have no complete text reading")]
            for ref, img in zip(reading_refs, doc.images):
                ref.update(ocr=img.ocr_text, description=img.description, ocr_kind=getattr(img, "_ocr_kind", "visual"))
                if f"image_id: {img.image_id}" not in doc.content:
                    doc.content += "\n\n" + build_image_block(img)
            rewrite_image_fences_in_document(doc)
            if any(not img.description and getattr(img, "_ocr_kind", "visual") != "text" for img in doc.images):
                warnings.append("Some images have no complete text reading; charts and layout may be incomplete for this model.")
            store.finish(sid, a.id, content=doc.content, position_map=parsed["position_map"], images=refs,
                         parser="local", warnings=warnings, text_prepared=True, description_model=target)


def _available(store: AttachmentStore, sid: str, aid: str):
    a = store.get(sid, aid)
    if a is None or not a.bound or a.status != "ready":
        raise ValueError("Attachment not available in this conversation")
    return a


def _project_text(content: str, images: list[dict], is_visual: bool, selected: set[str]) -> str:
    from src.rag.image_evidence import render_image_evidence
    return render_image_evidence(content, {}, is_visual=is_visual, skip_ids=selected,
                                 omitted_ids={img["id"] for img in images} - selected)


def hydrate_attachment_history(store: AttachmentStore, sid: str, messages: list[dict], *, is_visual: bool):
    """Restore a bounded set of previously viewed pages from session-owned files."""
    from src.parsers.image_utils import build_image_block

    out = [dict(message) for message in messages]
    remaining = 4
    attached = set()
    for message in reversed(out):
        if message.get("role") != "tool" or not isinstance(message.get("content"), str):
            continue
        try:
            payload = json.loads(message["content"])
            if not isinstance(payload, dict) or not payload.get("attached_image_indices"):
                continue
            aid = payload["attachment_id"]
            _available(store, sid, aid)
            refs = store.parsed(sid, aid)["images"]
            indices = [i for i in payload["attached_image_indices"][:4]
                       if isinstance(i, int) and 0 <= i < len(refs)]
            if is_visual:
                selected, parts = [], []
                for index in indices:
                    key = (aid, index)
                    if key not in attached and remaining:
                        parts.append({"type": "image_url", "image_url": {"url": store.image_url(sid, aid, index)}})
                        selected.append(index)
                        remaining -= 1
                        attached.add(key)
                payload["attached_image_indices"] = selected
                payload["omitted_image_indices"] = [i for i in indices if i not in selected]
                message["content"] = [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}, *parts]
            else:
                readings = []
                for index in indices:
                    ref = refs[index]
                    img = ImageInfo(ref["id"], ocr_text=ref.get("ocr", ""), description=ref.get("description", ""))
                    readings.append(_project_text(build_image_block(img), [], False, set()))
                payload["attached_image_indices"] = []
                message["content"] = json.dumps(payload, ensure_ascii=False) + "\n" + "\n".join(readings)[:READ_CHARS]
        except (ValueError, TypeError, KeyError, OSError):
            continue
    return out


def execute_attachment_tool(store: AttachmentStore, sid: str, name: str, args: dict, *, is_visual: bool = False):
    try:
        if name == "list_chat_attachments":
            rows = store.list(sid, bound_only=True)
            offset = max(0, int(args.get("offset", 0)))
            return json.dumps({"total": len(rows), "attachments": [
                a.model_dump(include={"id", "filename", "lines", "image_count", "warnings"})
                for a in rows[offset:offset + 20]],
                "next_offset": offset + 20 if offset + 20 < len(rows) else None}, ensure_ascii=False)
        aid = args.get("attachment_id", "")
        a = _available(store, sid, aid)
        lines = store.text(sid, aid).splitlines()
        if name == "search_chat_attachment":
            query = str(args.get("query", "")).strip()
            if not query or len(query) > 500:
                raise ValueError("Provide a search query of 1–500 characters")
            matches = [{"line": i + 1, "text": line[:600]} for i, line in enumerate(lines) if query.casefold() in line.casefold()]
            return json.dumps({"attachment_id": aid, "filename": a.filename, "matches": matches[:20], "total_matches": len(matches)}, ensure_ascii=False)
        if name != "read_chat_attachment":
            raise ValueError("Unknown attachment tool")
        start = max(1, int(args.get("start_line", 1)))
        count = min(200, max(1, int(args.get("line_count", 120))))
        start_char = max(0, int(args.get("start_char", 0)))
        block = "\n".join(lines[start - 1:start - 1 + count])
        content = block[start_char:start_char + READ_CHARS]
        consumed = block[:start_char + len(content)]
        if start_char + len(content) < len(block):
            next_line = start + consumed.count("\n")
            next_char = len(consumed.rsplit("\n", 1)[-1])
        else:
            next_line, next_char = start + count, 0
        parsed = store.parsed(sid, aid)
        images = parsed["images"]
        image_index = args.get("image_index")
        idx = int(image_index) if image_index is not None else None
        if idx is not None and not 0 <= idx < len(images):
            raise ValueError("Image index is out of range")
        image_count = min(4, max(1, int(args.get("image_count", 1))))
        indices = list(range(idx, min(idx + image_count, len(images)))) if idx is not None and is_visual else []
        selected = {images[i]["id"] for i in indices}
        payload = {"attachment_id": aid, "filename": a.filename, "start_line": start,
                   "total_lines": len(lines), "content": _project_text(content, images, is_visual, selected),
                   "has_more": next_line <= len(lines),
                   "next_start_line": next_line, "next_start_char": next_char,
                   "attached_image_indices": indices,
                   "next_image_index": indices[-1] + 1 if indices and indices[-1] + 1 < len(images) else None,
                   "warnings": a.warnings, "position_map": parsed["position_map"][:80],
                   "image_count": len(images), "images": [
                       {"image_index": i, **{k: v for k, v in img.items() if k not in {"ocr", "description"}}}
                       for i, img in enumerate(images) if max(0, (idx or 0) - 5) <= i < (idx or 0) + 15]}
        # Page/offset metadata is trustworthy only while the returned text
        # remains an exact native window (OCR/image projections may rewrite it).
        full_text = store.text(sid, aid)
        native_text = store.native_text(sid, aid)
        offset = sum(len(line) + 1 for line in lines[:start - 1]) + start_char
        if (full_text.startswith(native_text) and offset + len(content) <= len(native_text)
                and payload["content"] == content):
            payload["char_offset"] = offset
            known = [p for p in parsed["position_map"] if p.get("char_offset", 0) <= offset]
            if known:
                payload["location"] = {k: v for k, v in known[-1].items()
                    if k in {"page_number", "slide_number", "paragraph_index", "sheet_name"}}
        text = json.dumps(payload, ensure_ascii=False)
        if idx is not None:
            img = images[idx]
            if is_visual:
                return [{"type": "text", "text": text}] + [
                    {"type": "image_url", "image_url": {"url": store.image_url(sid, aid, i)}} for i in indices]
            text += "\n" + CHAT_ATTACHMENT_IMAGE_FALLBACK.format(ocr=(img.get("ocr") or "(unavailable)")[:READ_CHARS])
        return text
    except (ValueError, TypeError, OSError) as exc:
        return json.dumps({"error": str(exc)})


def attachment_context(store: AttachmentStore, sid: str, current_ids: list[str], *, is_visual: bool):
    rows = store.list(sid, bound_only=True)
    if not rows:
        return []
    current = set(current_ids)
    total = len(rows)
    recency = {a.id: i for i, a in enumerate(rows)}
    rows = sorted(rows, key=lambda a: (a.id not in current, -recency[a.id]))[:20]
    note = CHAT_ATTACHMENT_INVENTORY.format(total=total, shown=len(rows))
    parts = [{"type": "text", "text": note}]
    headers = []
    for a in rows:
        headers.append({"attachment_id": a.id, "filename": a.filename, "lines": a.lines,
                        "characters": a.characters, "image_count": a.image_count,
                        "attached_image_indices": [], "visual_preview_complete": False,
                        "warnings": [w[:200] for w in a.warnings[:2]], "excerpt_complete": False})
    budget = max(0, INLINE_CHARS - 64 - len(note) - sum(
        len(CHAT_ATTACHMENT_CONTEXT.format(manifest=json.dumps(h, ensure_ascii=False), content=""))
        for h in headers))
    image_budget = 4
    for a, manifest in zip(rows, headers):
        content = store.text(sid, a.id)
        images = store.parsed(sid, a.id)["images"]
        visual_indices = [i for i, img in enumerate(images) if not img.get("text_only")]
        indices = visual_indices[:image_budget] if a.id in current and is_visual else []
        selected = {images[i]["id"] for i in indices}
        manifest["attached_image_indices"] = indices
        manifest["visual_preview_complete"] = is_visual and len(selected) == len(visual_indices)
        take = len(content) if a.id in current and len(content) <= budget else min(600, budget, len(content))
        projected = _project_text(content[:take], images, is_visual, selected)
        excerpt = projected[:budget]
        budget -= len(excerpt)
        manifest["excerpt_complete"] = take == len(content) and len(projected) == len(excerpt)
        parts.append({"type": "text", "text": CHAT_ATTACHMENT_CONTEXT.format(
            manifest=json.dumps(manifest, ensure_ascii=False), content=excerpt)})
        if a.id in current:
            for idx, image in enumerate(images):
                if is_visual and idx in indices:
                    parts.append({"type": "image_url", "image_url": {"url": store.image_url(sid, a.id, idx)}})
                    image_budget -= 1
                elif not is_visual and budget > 250 and not image.get("text_only") and f"image_id: {image['id']}" not in content:
                    fallback = CHAT_ATTACHMENT_IMAGE_FALLBACK.format(ocr=(image.get("ocr") or "(unavailable)")[:min(2000, budget - 250)])
                    parts.append({"type": "text", "text": fallback})
                    budget -= len(fallback)
    return parts
