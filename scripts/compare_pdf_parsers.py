"""Compare PyMuPDF vs pdfplumber PDF table extraction.

Usage:
    python scripts/compare_pdf_parsers.py <pdf_file>
"""

import sys
from pathlib import Path

import fitz  # PyMuPDF
import pdfplumber


def parse_with_pymupdf(path: Path) -> list[dict]:
    """Current SinkDuce approach: PyMuPDF text + find_tables()."""
    doc = fitz.open(str(path))
    pages = []

    for i, page in enumerate(doc):
        tables_result = page.find_tables()
        table_list = tables_result.tables

        elements = []  # [(y_top, type, content)]

        # Tables
        for table in table_list:
            data = table.extract()
            if not data or not data[0]:
                continue
            cleaned = []
            for row in data:
                cleaned.append(
                    [str(c).replace("\n", " ").strip() if c else "" for c in row]
                )
            num_cols = max(len(r) for r in cleaned)
            header = cleaned[0]
            lines = ["| " + " | ".join(header) + " |"]
            lines.append("| " + " | ".join("---" for _ in header) + " |")
            for row in cleaned[1:]:
                padded = [row[i] if i < len(row) else "" for i in range(num_cols)]
                lines.append("| " + " | ".join(padded) + " |")
            elements.append((table.bbox[1], "table", "\n".join(lines)))

        # Text blocks (excluding those inside tables)
        table_bboxes = [t.bbox for t in table_list]
        blocks = page.get_text("dict")["blocks"]
        for b in blocks:
            if b["type"] != 0:
                continue
            # Check if block bbox is inside any table bbox
            bx0, by0, bx1, by1 = b["bbox"]
            in_table = False
            for tx0, ty0, tx1, ty1 in table_bboxes:
                if ty0 - 5 <= by0 <= ty1 + 5 and tx0 - 5 <= bx0 <= tx1 + 5:
                    in_table = True
                    break
            if in_table:
                continue
            lines = []
            for line in b["lines"]:
                spans_text = "".join(s["text"] for s in line["spans"])
                lines.append(spans_text)
            if lines:
                elements.append((b["bbox"][1], "text", "\n".join(lines)))

        elements.sort(key=lambda e: e[0])
        pages.append({"page": i + 1, "tables_found": len(table_list), "elements": elements})

    doc.close()
    return pages


def parse_with_pdfplumber(path: Path) -> list[dict]:
    """pdfplumber approach: extract_tables() + extract_text()."""
    pages = []

    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            # Extract tables
            tables = page.extract_tables()

            # Extract text (pdfplumber automatically excludes table regions)
            text = page.extract_text() or ""

            page_tables = []
            for table in tables:
                if not table or not table[0]:
                    continue
                cleaned = [
                    [str(c).replace("\n", " ").strip() if c else "" for c in row]
                    for row in table
                ]
                num_cols = max(len(r) for r in cleaned)
                header = cleaned[0]
                lines = ["| " + " | ".join(header) + " |"]
                lines.append("| " + " | ".join("---" for _ in header) + " |")
                for row in cleaned[1:]:
                    padded = [row[k] if k < len(row) else "" for k in range(num_cols)]
                    lines.append("| " + " | ".join(padded) + " |")
                page_tables.append("\n".join(lines))

            pages.append({
                "page": i + 1,
                "tables_found": len(tables),
                "text": text,
                "tables": page_tables,
            })

    return pages


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/compare_pdf_parsers.py <pdf_file>")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    sep = "=" * 70

    # --- PyMuPDF ---
    print(f"\n{sep}")
    print("  PyMuPDF (current)")
    print(sep)
    pymupdf_pages = parse_with_pymupdf(path)
    for p in pymupdf_pages:
        print(f"\n--- Page {p['page']} (tables found: {p['tables_found']}) ---")
        for y, elem_type, content in p["elements"]:
            if elem_type == "table":
                print("[TABLE]")
                print(content)
                print()
            else:
                print(content[:500])
                if len(content) > 500:
                    print(f"... ({len(content)} chars total)")
                print()

    # --- pdfplumber ---
    print(f"\n{sep}")
    print("  pdfplumber")
    print(sep)
    pdfplumber_pages = parse_with_pdfplumber(path)
    for p in pdfplumber_pages:
        print(f"\n--- Page {p['page']} (tables found: {p['tables_found']}) ---")
        for j, table_md in enumerate(p["tables"]):
            print(f"[TABLE {j + 1}]")
            print(table_md)
            print()
        if p["text"]:
            print("[TEXT]")
            # Show text with table regions removed by pdfplumber
            print(p["text"][:1000])
            if len(p["text"]) > 1000:
                print(f"... ({len(p['text'])} chars total)")
            print()


if __name__ == "__main__":
    main()
