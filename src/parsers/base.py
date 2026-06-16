from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


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


class DocumentParser(ABC):
    @abstractmethod
    def parse(self, path: Path) -> ParsedDocument: ...
