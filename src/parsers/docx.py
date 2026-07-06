"""DOCX parser using mammoth for Markdown output.

Converts .docx files to Markdown via mammoth, preserving formatting
(bold, italic, lists, headings, tables) that python-docx's para.text drops.
Output uses file_type="markdown" so it routes to MarkdownChunker.

Images are extracted via python-docx and appended as :::image fenced blocks
at the end of the document content.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import mammoth

from src.parsers.base import DocumentParser, ImageInfo, ParsedDocument


def clean_mammoth_markdown(text: str) -> str:
    """Clean mammoth's Markdown output.

    mammoth produces valid but noisy Markdown: unnecessary backslash escaping,
    __ for bold instead of **, and Word hidden bookmark anchors.
    """
    # 1. Remove Word hidden bookmark anchors (e.g. <a id="_Hlk12345"></a>)
    text = re.sub(r'<a id="[^"]*"></a>', "", text)

    # 2. Remove unnecessary backslash escaping mammoth adds for punctuation/brackets.
    #    Do NOT remove \_ (valid Markdown for literal underscore) or \* (literal asterisk).
    text = re.sub(r"\\([()[\].,:;!\"#&=<>|~`{}\-+])", r"\1", text)

    # 3. Convert mammoth's __bold__ to **bold** (only real paired markers, not __ in \_)
    text = re.sub(r"__(.+?)__", r"**\1**", text)

    # 4. Clean up empty/trailing-whitespace bold markers (e.g. "** " or "** **")
    text = re.sub(r"\*\*\s+\*\*", "", text)

    # 5. Remove mammoth's default base64-embedded images — we handle images separately
    text = re.sub(r"!\[[^\]]*\]\(data:image/[^)]+\)", "", text)

    return text


def _extract_images_from_docx(path: Path) -> list[ImageInfo]:
    """Extract embedded images from a .docx file.

    Walks the OPC package relationships to find image parts.
    """
    from docx import Document

    images: list[ImageInfo] = []
    try:
        doc = Document(str(path))
    except Exception:
        return images

    # Walk all relationships in the document part looking for images
    for rel in doc.part.rels.values():
        if "image" in (rel.reltype or ""):
            try:
                image_bytes = rel.target_part.blob
                content_type = rel.target_part.content_type
                fmt = content_type.split("/")[-1] if "/" in content_type else "png"
                img_id = uuid.uuid4().hex
                images.append(ImageInfo(
                    image_id=img_id,
                    image_bytes=image_bytes,
                    image_format=fmt,
                ))
            except Exception:
                pass

    return images


class DocxParser(DocumentParser):
    def parse(self, path: Path) -> ParsedDocument:
        with open(str(path), "rb") as f:
            result = mammoth.convert_to_markdown(f)

        text = clean_mammoth_markdown(result.value)

        # Extract images via python-docx
        images = _extract_images_from_docx(path)

        # Append :::image blocks at the end of the document
        if images:
            blocks = []
            for img in images:
                blocks.append(
                    f":::image\n"
                    f"image_id: {img.image_id}\n"
                    f"file_id: \n"
                    f"description: \n"
                    f":::"
                )
            text = text.rstrip() + "\n\n" + "\n\n".join(blocks)

        # Build position_map from heading positions
        position_map: list[dict] = []
        for m in re.finditer(r"^(#{1,6})\s+(.+)$", text, re.MULTILINE):
            position_map.append({
                "char_offset": m.start(),
                "label": m.group(0).strip(),
                "type": "section",
            })

        messages = [str(m) for m in result.messages] if result.messages else []

        return ParsedDocument(
            content=text,
            metadata={"format": "markdown", "messages": messages},
            source_path=str(path),
            file_type="markdown",
            position_map=position_map,
            images=images,
        )
