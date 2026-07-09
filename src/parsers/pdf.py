from __future__ import annotations

import io
import logging
import re
import uuid
from pathlib import Path

from src.parsers.base import DocumentParser, ImageInfo, ParsedDocument

logger = logging.getLogger(__name__)


def _table_data_to_markdown(data: list[list[str | None]]) -> str:
    """Convert pdfplumber table data (list of lists) to markdown.

    Strips entirely empty rows and columns before rendering.
    """
    if not data or not data[0]:
        return ""

    cleaned: list[list[str]] = []
    for row in data:
        cells = [str(c).replace("\n", " ").strip() if c else "" for c in row]
        # Skip entirely empty rows
        if any(c for c in cells):
            cleaned.append(cells)

    if not cleaned:
        return ""

    # Pad to uniform column count
    num_cols = max(len(row) for row in cleaned)
    for row in cleaned:
        while len(row) < num_cols:
            row.append("")

    # Remove entirely empty columns
    keep_cols: list[int] = []
    for ci in range(num_cols):
        if any(row[ci] for row in cleaned):
            keep_cols.append(ci)

    if not keep_cols:
        return ""

    pruned = [[row[ci] for ci in keep_cols] for row in cleaned]

    header = pruned[0]
    lines = ["| " + " | ".join(header) + " |"]
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for row in pruned[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _ocr_page(page_image, lang: str = "chi_sim+eng") -> str:
    """Extract text from a page image using Tesseract OCR."""
    try:
        import pytesseract
        text = pytesseract.image_to_string(page_image, lang=lang)
        return text.strip()
    except Exception:
        return ""


# ── Image extraction ──────────────────────────────────────────────────

def _extract_page_images(page, page_number: int) -> list[ImageInfo]:
    """Extract images from a pdfplumber page.

    Uses ``page.images`` for metadata (bbox), renders the image region
    via pdfplumber's internal renderer for the raw bytes.
    """
    images: list[ImageInfo] = []
    try:
        img_list = page.images
    except Exception:
        return images

    for i, img_meta in enumerate(img_list):
        x0 = float(img_meta.get("x0", 0))
        top = float(img_meta.get("top", 0))
        x1 = float(img_meta.get("x1", 0))
        bottom = float(img_meta.get("bottom", 0))
        bbox = (x0, top, x1, bottom)

        # Render image region
        try:
            cropped = page.within_bbox(bbox)
            pil_img = cropped.to_image(resolution=150).original
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            image_bytes = buf.getvalue()
        except Exception:
            image_bytes = None

        img_id = uuid.uuid4().hex
        images.append(ImageInfo(
            image_id=img_id,
            page_number=page_number,
            bbox=bbox,
            image_bytes=image_bytes,
            image_format="png",
        ))

    return images


# ── Page-level parsing with interleaved images ─────────────────────────

_PageElement = tuple[float, str, str]  # (y, kind, content)  kind ∈ {"text", "table", "image"}


def _parse_page_elements(
    page, page_number: int,
) -> tuple[list[_PageElement], bool, list[ImageInfo]]:
    """Parse a single page into ordered elements (text, tables, images).

    Returns (elements, has_table, images).
    Elements are sorted by y-coordinate.
    """
    elements: list[_PageElement] = []
    page_images: list[ImageInfo] = []
    has_table = False

    _page_w = float(getattr(page, "width", 612))
    _page_h = float(getattr(page, "height", 792))

    # ── 1. Images ──
    page_images = _extract_page_images(page, page_number)

    # Merge adjacent images (same x-range, similar width, touching/nearly
    # touching) into one — e.g. a flowchart split into two PDF objects.
    _merged_ids: set[str] = set()
    for i in range(len(page_images)):
        if page_images[i].image_id in _merged_ids:
            continue
        for j in range(i + 1, len(page_images)):
            if page_images[j].image_id in _merged_ids:
                continue
            a, b = page_images[i], page_images[j]
            if not a.bbox or not b.bbox or not a.image_bytes or not b.image_bytes:
                continue
            # Same x-range and similar width
            x_overlap = min(a.bbox[2], b.bbox[2]) - max(a.bbox[0], b.bbox[0])
            w_a, w_b = a.bbox[2] - a.bbox[0], b.bbox[2] - b.bbox[0]
            if x_overlap < 0.8 * min(w_a, w_b):
                continue
            # Adjacent vertically (gap ≤ 5 px, no overlap)
            a_top, b_top = a.bbox[1], b.bbox[1]
            top_img, bot_img = (a, b) if a_top < b_top else (b, a)
            gap = bot_img.bbox[1] - top_img.bbox[3]
            if not (0 <= gap <= 5):
                continue
            # Merge: render combined region
            try:
                merged_bbox = (
                    min(a.bbox[0], b.bbox[0]), min(a.bbox[1], b.bbox[1]),
                    max(a.bbox[2], b.bbox[2]), max(a.bbox[3], b.bbox[3]),
                )
                cropped = page.within_bbox(merged_bbox)
                pil_img = cropped.to_image(resolution=150).original
                buf = io.BytesIO()
                pil_img.save(buf, format="PNG")
                merged = ImageInfo(
                    image_id=uuid.uuid4().hex,
                    page_number=page_number,
                    bbox=merged_bbox,
                    image_bytes=buf.getvalue(),
                    image_format="png",
                )
                page_images.append(merged)
                _merged_ids.add(a.image_id)
                _merged_ids.add(b.image_id)
                logger.info(
                    "[PDFParser] Page %d: merged adjacent images (%.0f,%.0f)+(%.0f,%.0f) → (%.0f,%.0f,%.0f,%.0f)",
                    page_number,
                    a.bbox[1], a.bbox[3], b.bbox[1], b.bbox[3],
                    merged_bbox[0], merged_bbox[1], merged_bbox[2], merged_bbox[3],
                )
            except Exception:
                pass
    # Remove individual images that were merged
    page_images = [img for img in page_images if img.image_id not in _merged_ids]

    # ── 2. Tables: extract → markdown → crop screenshot ──
    try:
        tables = page.find_tables()
    except Exception:
        tables = []

    _table_source_ids: set[str] = set()
    if tables:
        has_table = True
        for table in tables:
            try:
                data = table.extract()
                if not data or not data[0] or len(data[0]) < 2:
                    continue
                md = _table_data_to_markdown(data)
                if not md:
                    continue

                # Crop table region as source image
                _img_y = table.bbox[1] - 0.1
                try:
                    x0, y0, x1, y1 = table.bbox
                    mx = (x1 - x0) * 0.05
                    my = (y1 - y0) * 0.05
                    padded = (
                        max(0, x0 - mx),
                        max(0, y0 - my),
                        min(_page_w, x1 + mx),
                        min(_page_h, y1 + my),
                    )
                    cropped = page.within_bbox(padded)
                    pil_img = cropped.to_image(resolution=300).original
                    buf = io.BytesIO()
                    pil_img.save(buf, format="PNG")
                    img = ImageInfo(
                        image_id=uuid.uuid4().hex,
                        page_number=page_number,
                        bbox=table.bbox,
                        image_bytes=buf.getvalue(),
                        image_format="png",
                        is_table_source=True,
                    )
                    page_images.append(img)
                    _table_source_ids.add(img.image_id)
                    elements.append((_img_y, "image",
                        f":::image\n"
                        f"image_id: {img.image_id}\n"
                        f"file_id: \n"
                        f":::\n"))
                except Exception:
                    pass

                elements.append((table.bbox[1], "table", md))
            except Exception:
                pass

    # ── 3. Text ──
    try:
        full_text = page.extract_text() or ""
    except Exception:
        full_text = ""

    if full_text.strip():
        elements.append((0, "text", full_text))

    # ── 4. Remaining images (not used as table source) ──
    for img in page_images:
        if img.is_table_source and img.image_id in _table_source_ids:
            continue
        block = (
            f":::image\n"
            f"image_id: {img.image_id}\n"
            f"file_id: \n"
            f"description: \n"
            f":::"
        )
        y_pos = img.bbox[1] if img.bbox else 0
        elements.append((y_pos, "image", block))

    # Fallback to OCR if no text extracted (scanned page)
    if not full_text.strip():
        try:
            ocr_text = _ocr_page(page.to_image(resolution=300).original)
            if ocr_text:
                elements.append((0, "text", ocr_text))
                full_text = ocr_text
        except Exception:
            pass

    return elements, has_table, page_images


class PDFParser(DocumentParser):
    def parse(self, path: Path) -> ParsedDocument:
        import pdfplumber

        pdf = pdfplumber.open(str(path))
        all_images: list[ImageInfo] = []
        pages_text: list[str] = []
        has_any_table = False

        try:
            for page_idx, page in enumerate(pdf.pages):
                page_number = page_idx + 1
                elements, page_has_table, page_images = _parse_page_elements(
                    page, page_number,
                )

                if page_has_table:
                    has_any_table = True

                all_images.extend(page_images)

                # Build page text with images interleaved at their y-position
                elements.sort(key=lambda e: (e[0], 0 if e[1] == "text" else 1 if e[1] == "table" else 2))
                page_parts: list[str] = []
                for y, kind, content in elements:
                    if kind == "image":
                        page_parts.append(content)
                    else:
                        page_parts.append(content)
                pages_text.append("\n\n".join(page_parts))
        finally:
            pdf.close()

        # ── Build content with position_map ──
        # Note: images are NOT filtered here — process_document_images in
        # upload_handler handles filtering + block cleanup atomically.
        cleaned = []
        for page_text in pages_text:
            page_text = page_text.strip()
            # Remove standalone page numbers at start of page
            page_text = re.sub(
                r'^\d{1,3}\s*$', '', page_text, count=1, flags=re.MULTILINE
            )
            cleaned.append(page_text)

        position_map: list[dict] = []
        parts: list[str] = []
        offset = 0
        for i, page_text in enumerate(cleaned):
            position_map.append({
                "char_offset": offset,
                "label": f"Page {i + 1}",
                "type": "page",
                "page_number": i + 1,
            })
            parts.append(page_text)
            offset += len(page_text) + 2

        return ParsedDocument(
            content="\n\n".join(parts),
            metadata={"pages": len(cleaned), "tables_found": has_any_table},
            source_path=str(path),
            file_type="pdf",
            position_map=position_map,
            images=all_images,
        )
