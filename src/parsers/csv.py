from __future__ import annotations

import csv
from pathlib import Path

from src.parsers.base import DocumentParser, ParsedDocument

MAX_ROWS = 10_000


class CSVParser(DocumentParser):
    def parse(self, path: Path) -> ParsedDocument:
        content, delimiter, encoding = _parse_csv(path)
        return ParsedDocument(
            content=content,
            metadata={"delimiter": delimiter, "encoding": encoding},
            source_path=str(path),
            file_type="csv",
        )


def _detect_encoding(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            raw.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return "latin-1"


def _detect_delimiter(sample: str) -> str:
    sniffer = csv.Sniffer()
    try:
        dialect = sniffer.sniff(sample, delimiters=",\t;|")
        return dialect.delimiter
    except csv.Error:
        return ","


def _parse_csv(path: Path) -> tuple[str, str, str]:
    encoding = _detect_encoding(path)
    text = path.read_text(encoding=encoding)
    delimiter = _detect_delimiter(text[:8192])

    reader = csv.reader(text.splitlines(), delimiter=delimiter)
    rows: list[list[str]] = []
    for i, row in enumerate(reader):
        if i >= MAX_ROWS:
            break
        rows.append(row)

    if not rows:
        return "", delimiter, encoding

    # Build markdown table
    header = rows[0]
    separator = ["---"] * len(header)
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    for row in rows[1:]:
        # Pad short rows to match header width
        padded = row + [""] * (len(header) - len(row))
        lines.append("| " + " | ".join(padded[: len(header)]) + " |")

    return "\n".join(lines), delimiter, encoding
