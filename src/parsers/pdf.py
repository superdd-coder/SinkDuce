from __future__ import annotations

import re
from pathlib import Path

from src.parsers.base import DocumentParser, ParsedDocument


def _bbox_in_table(block_bbox: tuple, table_bboxes: list[tuple]) -> bool:
    """Check if a text block bbox falls inside any table region."""
    bx0, by0, bx1, by1 = block_bbox
    for tx0, ty0, tx1, ty1 in table_bboxes:
        if ty0 - 5 <= by0 <= ty1 + 5 and tx0 - 5 <= bx0 <= tx1 + 5:
            return True
    return False


def _table_to_markdown(table) -> str:
    """Convert a PyMuPDF table to markdown format."""
    data = table.extract()
    if not data or not data[0]:
        return ""

    # Clean cells: replace newlines with spaces
    cleaned = []
    for row in data:
        cleaned.append([str(c).replace("\n", " ").strip() if c else "" for c in row])

    num_cols = max(len(row) for row in cleaned)
    header = cleaned[0]

    lines = ["| " + " | ".join(header) + " |"]
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for row in cleaned[1:]:
        padded = [row[i] if i < len(row) else "" for i in range(num_cols)]
        lines.append("| " + " | ".join(padded) + " |")
    return "\n".join(lines)


def _ocr_page(page) -> str:
    """Extract text from a page image using Tesseract OCR."""
    try:
        import pytesseract
        from PIL import Image
        import io

        # Render page to image at 300 DPI for good OCR quality
        mat = page.get_pixmap(dpi=300)
        img = Image.open(io.BytesIO(mat.tobytes("png")))
        # Use chi_sim+eng for mixed Chinese/English documents
        text = pytesseract.image_to_string(img, lang="chi_sim+eng")
        return text.strip()
    except Exception:
        return ""


def _parse_with_pymupdf(path: Path) -> tuple[list[str], bool]:
    """Parse PDF using PyMuPDF: text extraction + table detection.

    Maintains document layout order by sorting text blocks and tables
    by their vertical position (y-coordinate) on the page.
    Falls back to OCR for image-based (scanned) pages.
    """
    import fitz

    doc = fitz.open(str(path))
    pages = []
    has_any_table = False

    for page in doc:
        tables_result = page.find_tables()
        table_list = tables_result.tables

        if table_list:
            has_any_table = True
            table_bboxes = [t.bbox for t in table_list]

            # Collect elements with their y-positions for ordering
            elements = []  # [(y_top, type, content)]

            # Add table markdown blocks
            for table in table_list:
                try:
                    md = _table_to_markdown(table)
                    if md:
                        elements.append((table.bbox[1], "table", md))
                except Exception:
                    pass

            # Get text blocks, filter out those inside tables
            blocks = page.get_text("dict")["blocks"]
            for b in blocks:
                if b["type"] != 0:
                    continue
                if _bbox_in_table(b["bbox"], table_bboxes):
                    continue
                lines = []
                for line in b["lines"]:
                    spans_text = "".join(s["text"] for s in line["spans"])
                    lines.append(spans_text)
                if lines:
                    elements.append((b["bbox"][1], "text", "\n".join(lines)))

            # Sort by vertical position
            elements.sort(key=lambda e: e[0])

            text = "\n\n".join(e[2] for e in elements)
        else:
            text = page.get_text("text")

        # Fallback to OCR if no text extracted (scanned/image-based page)
        if not text.strip():
            text = _ocr_page(page)

        pages.append(text)

    doc.close()
    return pages, has_any_table


class PDFParser(DocumentParser):
    def parse(self, path: Path) -> ParsedDocument:
        text_pages, tables_found = _parse_with_pymupdf(path)

        cleaned = []
        for page_text in text_pages:
            page_text = page_text.strip()
            # Remove standalone page numbers at start of page (e.g., "3\n" or "4\n")
            # Only match if the entire first line is just a number (not content starting with numbers)
            page_text = re.sub(r'^\d{1,3}\s*$', '', page_text, count=1, flags=re.MULTILINE)
            cleaned.append(page_text)

        # Build content with position_map tracking page boundaries
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
            offset += len(page_text) + 2  # +2 for "\n\n" separator

        return ParsedDocument(
            content="\n\n".join(parts),
            metadata={"pages": len(cleaned), "tables_found": tables_found},
            source_path=str(path),
            file_type="pdf",
            position_map=position_map,
        )
