"""Shared image utilities: filtering, Vision LLM description, path resolution."""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from src.parsers.base import ImageInfo

logger = logging.getLogger(__name__)

# ── filtering thresholds ────────────────────────────────────────────────

_MIN_WIDTH = 150       # px
_MIN_HEIGHT = 150      # px
_MIN_AREA = 20_000     # px²
_MAX_REPEAT_PAGES = 3  # same hash on >= N pages → logo / template element


def _image_hash(image_bytes: bytes) -> str:
    """SHA-256 of raw image bytes."""
    return hashlib.sha256(image_bytes).hexdigest()


def _is_too_small(bbox: tuple | None, image_bytes: bytes | None = None) -> bool:
    """Check if image dimensions or area are below thresholds.

    If bbox is None, try to infer from image_bytes via Pillow.
    Returns True (too small → skip) or False (keep).
    """
    if bbox is not None:
        x0, y0, x1, y1 = bbox
        w = abs(x1 - x0)
        h = abs(y1 - y0)
        if w < _MIN_WIDTH and h < _MIN_HEIGHT:
            return True
        if w * h < _MIN_AREA:
            return True
        return False

    # No bbox — try image bytes
    if image_bytes is not None:
        try:
            from PIL import Image
            import io
            with Image.open(io.BytesIO(image_bytes)) as img:
                w, h = img.size
                if w < _MIN_WIDTH and h < _MIN_HEIGHT:
                    return True
                if w * h < _MIN_AREA:
                    return True
        except Exception:
            pass

    return False


def filter_images(images: list[ImageInfo]) -> list[ImageInfo]:
    """Apply size, repetition, and data-availability filters. Returns kept images.

    Filters applied (return True means *skip*):
    0. No bytes: image_bytes is None or empty → skip (can't save or describe)
    1. Size: bbox or image_bytes dimensions below thresholds
    2. Repetition: same hash appears on >= MAX_REPEAT_PAGES pages
    """
    if not images:
        return []

    # 0. Skip images without byte data (e.g. vector elements pdfplumber can't render)
    kept = []
    for img in images:
        if img.image_bytes and len(img.image_bytes) > 0:
            kept.append(img)
        else:
            logger.debug(
                "[ImageFilter] Skipped (no bytes): img_id=%s page=%s slide=%s",
                img.image_id, img.page_number, img.slide_number,
            )

    # 1. Size filter
    kept2 = []
    for img in kept:
        if not _is_too_small(img.bbox, img.image_bytes):
            kept2.append(img)
        else:
            logger.debug(
                "[ImageFilter] Skipped (too small): img_id=%s page=%s slide=%s",
                img.image_id, img.page_number, img.slide_number,
            )

    # 2. Repetition filter
    hash_page_map: dict[str, set[int]] = {}
    for img in kept2:
        if img.image_bytes is None:
            continue
        h = _image_hash(img.image_bytes)
        page = img.page_number or img.slide_number or 0
        hash_page_map.setdefault(h, set()).add(page)

    result = []
    for img in kept2:
        if img.image_bytes is None:
            result.append(img)
            continue
        h = _image_hash(img.image_bytes)
        if len(hash_page_map.get(h, set())) >= _MAX_REPEAT_PAGES:
            logger.debug(
                "[ImageFilter] Skipped (repeated %d pages): img_id=%s hash=%s",
                len(hash_page_map[h]), img.image_id, h[:16],
            )
            continue
        result.append(img)

    return result


# ── OCR classification ───────────────────────────────────────────────────

_OCR_MIN_CHARS = 30        # min chars: below this → visual (Vision LLM)
_OCR_TEXT_ONLY_CHARS = 200 # above this + clean → pure text (OCR only, skip Vision LLM)
_OCR_MIN_CONFIDENCE = 60   # min mean confidence


def _ocr_text_is_garbage(text: str) -> bool:
    """Heuristic: OCR text is likely garbage if it contains too many short
    fragments (1-2 char tokens) or non-word symbols — typical of Tesseract
    hallucinating on flowcharts, diagrams, and photos.

    Real English/Chinese text has mostly 3+ character words; garbage OCR
    from graphics produces scattered single letters, digits, and symbols
    (e.g. "a 7 é 5 4 3 2 1 us wunimasway ...").
    """
    if not text:
        return True
    tokens = text.split()
    if not tokens:
        return True
    # Count short fragments (1-2 chars) that are NOT CJK (single CJK char
    # can be a valid word in Chinese)
    short = 0
    for t in tokens:
        if len(t) <= 2 and not ("一" <= t <= "鿿") and not t.isalpha():
            short += 1
    ratio = short / len(tokens)
    return ratio > 0.3


def _ocr_image(image_bytes: bytes, lang: str = "chi_sim+eng") -> tuple[str, float]:
    """Run Tesseract OCR on image bytes. Returns (text, mean_confidence)."""
    try:
        import pytesseract
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(image_bytes))
        data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
        confidences = [int(c) for c in data["conf"] if c != "-1" and int(c) > 0]
        words = [t for t, c in zip(data["text"], data["conf"]) if t.strip() and c != "-1"]
        text = " ".join(words)
        mean_conf = sum(confidences) / len(confidences) if confidences else 0.0
        return text.strip(), mean_conf
    except Exception:
        logger.debug("[OCR] Tesseract failed", exc_info=True)
        return "", 0.0


def _classify_image(image_bytes: bytes) -> tuple[str, str]:
    """Classify image as 'text', 'mixed', or 'visual' based on OCR quality.

    Returns (classification, ocr_text).
    - 'text': dense high-confidence clean text → OCR only, skip Vision LLM
    - 'mixed': moderate text or text with garbage characters → OCR + Vision LLM
    - 'visual': little or low-quality text → Vision LLM only
    """
    ocr_text, confidence = _ocr_image(image_bytes)
    chars = len(ocr_text)

    if chars >= _OCR_TEXT_ONLY_CHARS and confidence >= _OCR_MIN_CONFIDENCE:
        if _ocr_text_is_garbage(ocr_text):
            logger.debug("[OCR] TEXT→MIXED (garbage ratio): %d chars, conf=%.0f%%", chars, confidence)
            return "mixed", ocr_text
        logger.debug("[OCR] Classified as TEXT: %d chars, conf=%.0f%%", chars, confidence)
        return "text", ocr_text
    if chars >= _OCR_MIN_CHARS and confidence >= _OCR_MIN_CONFIDENCE:
        logger.debug("[OCR] Classified as MIXED: %d chars, conf=%.0f%%", chars, confidence)
        return "mixed", ocr_text
    logger.debug("[OCR] Classified as VISUAL: %d chars, conf=%.0f%%", chars, confidence)
    return "visual", ocr_text


# ── Vision LLM description ──────────────────────────────────────────────

def _describe_one(
    img: ImageInfo,
    visual_llm,
    prompt: str,
    retries: int = 3,
) -> str:
    """Describe one image via Vision LLM. Returns description or empty string on failure."""
    if img.image_bytes is None:
        return ""

    image_base64 = base64.b64encode(img.image_bytes).decode("utf-8")
    mime = f"image/{img.image_format}" if img.image_format else "image/png"

    for attempt in range(1, retries + 1):
        try:
            description = visual_llm.describe_image(image_base64, mime, prompt=prompt)
            logger.info(
                "[ImageDescribe] img_id=%s attempt=%d/%d len=%d",
                img.image_id, attempt, retries, len(description),
            )
            return description.strip()
        except Exception:
            logger.warning(
                "[ImageDescribe] img_id=%s attempt=%d/%d failed",
                img.image_id, attempt, retries, exc_info=True,
            )

    logger.error("[ImageDescribe] img_id=%s all %d attempts failed, skipping", img.image_id, retries)
    return ""


def describe_images(
    images: list[ImageInfo],
    provider,
    model_id: str,
    prompt: str,
    max_workers: int = 20,
) -> list[ImageInfo]:
    """Concurrently describe images using a Vision LLM.

    Args:
        images: Images to describe (filtered list).
        provider: The LLM provider config object (has visual_model_ids).
        model_id: The specific vision model ID to use.
        prompt: The system/user prompt for image description.
        max_workers: Max concurrent Vision LLM calls.

    Returns:
        Images with ``description`` filled in. Only images that got a
        non-empty description are included.
    """
    if not images:
        return []

    from src.providers.llm import create_llm_for_provider

    try:
        visual_llm = create_llm_for_provider(provider, model=model_id)
    except Exception as e:
        logger.warning("[ImageDescribe] Failed to create vision LLM: %s", e)
        return []

    results: list[ImageInfo] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(images))) as executor:
        futures = {
            executor.submit(_describe_one, img, visual_llm, prompt): img
            for img in images
        }
        for future in as_completed(futures):
            img = futures[future]
            try:
                description = future.result()
                if description:
                    img.description = description
                    results.append(img)
                else:
                    logger.debug("[ImageDescribe] img_id=%s no description, skipped", img.image_id)
            except Exception:
                logger.exception("[ImageDescribe] img_id=%s unexpected error", img.image_id)

    logger.info("[ImageDescribe] %d/%d images described successfully", len(results), len(images))
    return results


# ── path resolution ──────────────────────────────────────────────────────

def resolve_image_path(file_id: str, image_id: str) -> Path | None:
    """Find image file on disk from file_id + image_id.

    Searches collections/files/{file_id}/images/.
    Images are saved as {image_id}.{ext} by process_document_images during ingest.
    """
    data_dir = Path("data").resolve()
    cols_dir = data_dir / "collections"
    if not cols_dir.is_dir():
        return None

    for col_dir in cols_dir.iterdir():
        if not col_dir.is_dir():
            continue
        img_dir = col_dir / "files" / file_id / "images"
        if not img_dir.is_dir():
            continue
        # Try common image extensions
        for ext in ("png", "jpg", "jpeg", "gif", "webp"):
            img_path = img_dir / f"{image_id}.{ext}"
            if img_path.is_file():
                return img_path
    return None


def _resolve_image_path_direct(file_id: str, image_id: str, collection: str) -> Path | None:
    """Direct path resolution when collection is known — no directory scanning."""
    _exts = ("png", "jpg", "jpeg", "gif", "webp")
    img_dir = Path("data").resolve() / "collections" / collection / "files" / file_id / "images"
    if not img_dir.is_dir():
        logger.warning("[ImageStitch] img_dir not found: %s", img_dir)
        return None
    for ext in _exts:
        p = img_dir / f"{image_id}.{ext}"
        if p.is_file():
            return p
    logger.warning("[ImageStitch] image file not found in %s (tried %s)", img_dir, _exts)
    return None


def encode_image_base64(image_id: str, file_id: str) -> tuple[str, str] | None:
    """Encode an image as base64. Returns (base64_string, mime_type) or None."""
    path = resolve_image_path(file_id, image_id)


def _encode_image_base64_direct(image_id: str, file_id: str, collection: str) -> tuple[str, str] | None:
    """Encode image using direct path (collection known) — no directory scan."""
    path = _resolve_image_path_direct(file_id, image_id, collection)
    if path is None:
        # Fallback to full scan
        path = resolve_image_path(file_id, image_id)
    if path is None:
        logger.warning("[ImageStitch] image not found: file_id=%s image_id=%s col=%s", file_id, image_id, collection)
        return None

    import mimetypes
    mime, _ = mimetypes.guess_type(str(path))
    mime = mime or "image/png"

    try:
        data = path.read_bytes()
        b64 = base64.b64encode(data).decode("utf-8")
        logger.debug("[ImageStitch] encoded %s (%d bytes) from %s", image_id[:12], len(data), path)
        return b64, mime
    except Exception:
        logger.exception("[ImageStitch] failed to encode: %s", path)
        return None


# ── block helpers ─────────────────────────────────────────────────────────

IMAGE_BLOCK_PATTERN = ":::image"

# Matches a :::image block. Uses negative-lookahead to avoid crossing ::: boundaries.
# Note: no \s* before \n — when value is empty, \s* would consume the newline
# and break the match. Each line ends with the value (possibly empty) then \n.
_IMAGE_BLOCK_RE = re.compile(
    r":::image[ \t]*\n"
    r"image_id:\s*([a-f0-9]+)\n"
    r"file_id:\s*([^\n]*)\n"
    r"(?:ocr_text:\s*((?:(?!:::).)*?)\n)?"        # optional — absent when ocr_text is empty
    r"(?:description:\s*((?:(?!:::).)*?)\n)?"      # optional — absent when description is empty
    r":::",
    re.DOTALL,
)


def build_image_block(img: ImageInfo) -> str:
    """Build a :::image fenced block string.
    Empty ocr_text / description lines are omitted to keep the block clean.
    """
    lines = [
        ":::image",
        f"image_id: {img.image_id}",
        f"file_id: {img.file_id}",
    ]
    if img.ocr_text:
        lines.append(f"ocr_text: {img.ocr_text}")
    if img.description:
        lines.append(f"description: {img.description}")
    lines.append(":::")
    return "\n".join(lines) + "\n"


# ── document-level image processing ─────────────────────────────────────

def _save_image_to_disk(file_dir: Path, img: ImageInfo) -> bool:
    """Save image bytes to disk. Returns True on success."""
    if img.image_bytes is None:
        return False
    images_dir = file_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    img_path = images_dir / f"{img.image_id}.{img.image_format}"
    try:
        img_path.write_bytes(img.image_bytes)
        return True
    except Exception:
        logger.exception("[ImageSave] Failed to save %s", img_path)
        return False


def _remove_image_block_from_content(content: str, image_id: str) -> str:
    """Remove a :::image block for a specific image_id from content."""
    pattern = re.compile(
        rf":::image[ \t]*\n"
        rf"image_id:\s*{re.escape(image_id)}\n"
        rf"file_id:\s*[^\n]*\n"
        rf"(?:ocr_text:\s*(?:(?!:::).)*?\n)?"
        rf"(?:description:\s*(?:(?!:::).)*?\n)?"
        rf":::",
        re.DOTALL,
    )
    return pattern.sub("", content)


def _update_description_in_content(content: str, img: ImageInfo) -> str:
    """Rebuild a :::image block from current ImageInfo values.
    Empty ocr_text / description are omitted.
    """
    pattern = re.compile(
        rf":::image[ \t]*\n"
        rf"image_id:\s*{re.escape(img.image_id)}\n"
        rf"(?:file_id:\s*[^\n]*\n)"
        rf"(?:ocr_text:\s*(?:(?!:::).)*?\n)?"
        rf"(?:description:\s*(?:(?!:::).)*?\n)?"
        rf":::",
        re.DOTALL,
    )
    block = build_image_block(img)
    return pattern.sub(block, content)


def process_document_images(
    doc: ParsedDocument,
    file_id: str,
    file_dir: Path,
    *,
    vision_provider=None,
    vision_model_id: str = "",
    vision_prompt: str = "",
) -> ParsedDocument:
    """Post-process a ParsedDocument's images: filter, save, describe, update content.

    Modifies ``doc`` in-place and returns it.

    1. Sets ``file_id`` on all images
    2. Filters out small / repeated images → removes their :::image blocks from content
    3. Saves remaining images to disk
    4. If Vision LLM configured, describes images concurrently
    5. Updates :::image blocks in content with descriptions

    Args:
        doc: Parsed document with images.
        file_id: The file's unique ID (used for storage path).
        file_dir: Directory where the file's data lives (parent of images/ dir).
        vision_provider: LLM provider config with visual_model_ids.
        vision_model_id: The vision model to use. If empty, skip description.
        vision_prompt: Prompt for image description.
    """
    if not doc.images:
        return doc

    # 1. Set file_id on all images
    for img in doc.images:
        img.file_id = file_id

    # 2. Filter
    before_count = len(doc.images)
    kept = filter_images(list(doc.images))

    # Remove filtered-out images' blocks from content
    kept_ids = {img.image_id for img in kept}
    for img in doc.images:
        if img.image_id not in kept_ids:
            doc.content = _remove_image_block_from_content(doc.content, img.image_id)

    doc.images = kept
    logger.info(
        "[ImageProcess] Filter: %d → %d images kept for %s",
        before_count, len(kept), file_id,
    )

    if not doc.images:
        return doc

    # 3. OCR all kept images → classify as text / mixed / visual
    # Table source images skip OCR + Vision LLM — they are the original
    # images MinerU already converted to tables, kept for recall only.
    table_source_images: list[ImageInfo] = []
    text_images: list[ImageInfo] = []      # OCR only, skip Vision LLM
    mixed_images: list[ImageInfo] = []     # OCR + Vision LLM
    visual_images: list[ImageInfo] = []    # Vision LLM only
    for img in kept:
        if img.is_table_source:
            table_source_images.append(img)
            continue
        if img.image_bytes is None:
            visual_images.append(img)
            continue
        img_type, ocr_text = _classify_image(img.image_bytes)
        img.ocr_text = ocr_text
        if img_type == "text":
            text_images.append(img)
        elif img_type == "mixed":
            mixed_images.append(img)
        else:
            img.ocr_text = ""  # visual: OCR below threshold — clear garbage
            visual_images.append(img)

    logger.info(
        "[ImageProcess] OCR classification: %d text, %d mixed, %d visual, %d table-source (of %d total)",
        len(text_images), len(mixed_images), len(visual_images), len(table_source_images), len(kept),
    )

    # 4. Describe MIXED + VISUAL images with Vision LLM (text-only images skip this)
    needs_description = mixed_images + visual_images
    if vision_provider and vision_model_id and needs_description:
        logger.info("[ImageProcess] Describing %d images (mixed+visual) with %s",
                    len(needs_description), vision_model_id)
        described = describe_images(
            needs_description, vision_provider, vision_model_id, vision_prompt,
        )
    else:
        described = []

    described_ids = {img.image_id for img in described}

    # Text images (always keep) + successfully described mixed/visual images
    # + table source images (always keep, no OCR/description)
    final_images = text_images + described + table_source_images

    # Remove blocks for images that needed but failed description
    needs_ids = {img.image_id for img in needs_description}
    for image_id in (needs_ids - described_ids):
        doc.content = _remove_image_block_from_content(doc.content, image_id)
        logger.warning("[ImageProcess] Failed to describe image %s, block removed", image_id[:16])

    # If no Vision LLM, remove mixed+visual image blocks; keep text images with OCR
    if not vision_provider or not vision_model_id:
        if needs_description:
            for img in needs_description:
                doc.content = _remove_image_block_from_content(doc.content, img.image_id)
            logger.info("[ImageProcess] No Vision LLM — removed %d mixed+visual image blocks",
                        len(needs_description))
        final_images = text_images + table_source_images

    doc.images = final_images

    # Update content blocks with OCR text and descriptions
    for img in final_images:
        doc.content = _update_description_in_content(doc.content, img)

    n_text = len([i for i in final_images if i.ocr_text and not i.description])
    n_mixed = len([i for i in final_images if i.ocr_text and i.description])
    n_visual = len([i for i in final_images if i.description and not i.ocr_text])
    n_table_src = len([i for i in final_images if i.is_table_source])
    logger.info(
        "[ImageProcess] %d images final: %d text-only, %d mixed, %d visual-only, %d table-source for %s",
        len(final_images), n_text, n_mixed, n_visual, n_table_src, file_id,
    )

    # Save images to disk, then clear bytes from memory
    saved_count = 0
    for img in doc.images:
        if _save_image_to_disk(file_dir, img):
            saved_count += 1
        img.image_bytes = None  # free memory

    if saved_count:
        logger.info("[ImageProcess] Saved %d/%d images to disk for %s",
                    saved_count, len(doc.images), file_id)

    return doc


# ── chunk annotation ──────────────────────────────────────────────────────

def annotate_chunks_with_images(chunks: list, doc_images: list[ImageInfo]) -> list:
    """Scan chunk text for :::image blocks and add image refs to chunk metadata.

    For each chunk, extracts image_id and file_id from :::image blocks in
    its text, then looks up the matching ImageInfo to add page/slide info.

    Modifies chunks in-place. Returns the modified list.
    """
    if not doc_images:
        return chunks

    # Build lookup: image_id → ImageInfo
    img_map: dict[str, ImageInfo] = {img.image_id: img for img in doc_images if img.image_id}

    for chunk in chunks:
        chunk_text = chunk.text if hasattr(chunk, "text") else ""
        meta = chunk.metadata if hasattr(chunk, "metadata") else {}

        # Find all :::image blocks in this chunk's text
        image_refs: list[dict] = []
        for m in _IMAGE_BLOCK_RE.finditer(chunk_text):
            img_id = m.group(1)
            file_id = m.group(2)
            ocr_text = m.group(3) or ""  # optional capture group — None in old-format blocks
            img = img_map.get(img_id)
            if img:
                image_refs.append({
                    "image_id": img_id,
                    "file_id": img.file_id or file_id,  # prefer ImageInfo's file_id
                    "page_number": img.page_number,
                    "slide_number": img.slide_number,
                    "description": img.description,
                    "ocr_text": img.ocr_text,
                })
            elif file_id:
                image_refs.append({
                    "image_id": img_id,
                    "file_id": file_id,
                    "ocr_text": ocr_text,
                })

        if image_refs:
            meta["images"] = image_refs

    return chunks
