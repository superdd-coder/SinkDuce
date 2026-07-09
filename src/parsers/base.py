from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ImageInfo:
    """One image extracted from a document."""
    image_id: str
    file_id: str = ""
    page_number: int | None = None
    slide_number: int | None = None
    bbox: tuple[float, float, float, float] | None = None  # (x0, y0, x1, y1)
    image_bytes: bytes | None = None
    image_format: str = "png"
    alt_text: str = ""
    description: str = ""
    ocr_text: str = ""
    is_table_source: bool = False  # skip OCR + Vision LLM; insert before table


@dataclass
class ParsedDocument:
    content: str
    metadata: dict = field(default_factory=dict)
    source_path: str = ""
    file_type: str = ""
    # Maps char offsets in `content` to structural positions (page/slide/paragraph).
    # Each entry: {"char_offset": int, "label": str, "type": "page"|"slide"|"section",
    #              "page_number": int, "slide_number": int, ...}
    position_map: list[dict] = field(default_factory=list)
    images: list[ImageInfo] = field(default_factory=list)


class DocumentParser(ABC):
    @abstractmethod
    def parse(self, path: Path) -> ParsedDocument: ...
