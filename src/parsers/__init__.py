from __future__ import annotations

from pathlib import Path

from src.parsers.base import DocumentParser, ImageInfo, ParsedDocument
from src.parsers.docx import DocxParser
from src.parsers.excel import ExcelParser
from src.parsers.html import HTMLParser
from src.parsers.markdown import MarkdownParser
from src.parsers.pdf import PDFParser
from src.parsers.pptx import PptxParser
from src.parsers.csv import CSVParser
from src.parsers.text import TextParser
from src.parsers.json_parser import JSONParser, JSONLParser

PARSERS: dict[str, DocumentParser] = {
    ".pdf": PDFParser(),
    ".docx": DocxParser(),
    ".xlsx": ExcelParser(),
    ".xls": ExcelParser(),
    ".pptx": PptxParser(),
    ".md": MarkdownParser(),
    ".txt": TextParser(),
    ".csv": CSVParser(),
    ".tsv": CSVParser(),
    ".html": HTMLParser(),
    ".htm": HTMLParser(),
    ".json": JSONParser(),
    ".jsonl": JSONLParser(),
}


def parse_file(path: Path) -> ParsedDocument:
    ext = path.suffix.lower()
    parser = PARSERS.get(ext)
    if parser is None:
        supported = ", ".join(sorted(PARSERS.keys()))
        raise ValueError(f"Unsupported file format: {ext}. Supported: {supported}")
    return parser.parse(path)


def parse_directory(path: Path) -> list[ParsedDocument]:
    import logging
    logger = logging.getLogger(__name__)
    docs = []
    for file in sorted(path.rglob("*")):
        if file.is_file() and file.suffix.lower() in PARSERS:
            try:
                docs.append(parse_file(file))
            except Exception as e:
                logger.warning("Failed to parse %s: %s", file, e)
    return docs


__all__ = ["parse_file", "parse_directory", "ParsedDocument"]
