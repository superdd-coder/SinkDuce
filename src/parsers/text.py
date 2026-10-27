from pathlib import Path
import chardet
from src.parsers.base import DocumentParser, ParsedDocument


class TextParser(DocumentParser):
    def parse(self, path: Path) -> ParsedDocument:
        raw = path.read_bytes()
        detected = chardet.detect(raw)
        encoding = detected.get("encoding", "utf-8") or "utf-8"
        text = raw.decode(encoding, errors="replace")
        return ParsedDocument(
            content=text,
            metadata={"encoding": encoding},
            source_path=str(path),
            file_type="text",
        )
