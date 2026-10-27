from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup
from markdownify import markdownify as md

from src.parsers.base import DocumentParser, ParsedDocument

# Tags to strip entirely (content removed)
STRIP_TAGS = ["script", "style", "nav", "footer", "header", "noscript"]


class HTMLParser(DocumentParser):
    def parse(self, path: Path) -> ParsedDocument:
        raw = path.read_bytes()
        soup = BeautifulSoup(raw, "html.parser")

        # Extract title before stripping
        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else None

        # Remove unwanted tags
        for tag_name in STRIP_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        # Extract <body> or fall back to full document
        body = soup.find("body")
        source = body if body else soup

        # Convert HTML to Markdown
        content = md(str(source), heading_style="ATX", strip=["img"])

        metadata: dict = {"format": "html"}
        if title:
            metadata["title"] = title

        return ParsedDocument(
            content=content.strip(),
            metadata=metadata,
            source_path=str(path),
            file_type="html",
        )
