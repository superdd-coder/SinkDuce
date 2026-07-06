from __future__ import annotations

import re
import uuid
from pathlib import Path

import yaml

from src.parsers.base import DocumentParser, ParsedDocument, ImageInfo
from src.parsers.image_utils import build_image_block

_FRONTMATTER_DELIMITER = "---"

# Matches ![alt](url) — but NOT already inside a ::: fenced block
_MD_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')


def _extract_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML front-matter between '---' delimiters.

    Returns (frontmatter_dict, body_text). If no front-matter is found,
    returns ({}, original_text).
    """
    if not text.startswith(_FRONTMATTER_DELIMITER + "\n"):
        return {}, text

    # Find the closing delimiter
    end_idx = text.find("\n" + _FRONTMATTER_DELIMITER, len(_FRONTMATTER_DELIMITER) + 1)
    if end_idx == -1:
        return {}, text

    yaml_block = text[len(_FRONTMATTER_DELIMITER) + 1 : end_idx]
    body = text[end_idx + len("\n" + _FRONTMATTER_DELIMITER) :].lstrip("\n")

    try:
        metadata = yaml.safe_load(yaml_block) or {}
    except yaml.YAMLError:
        metadata = {}

    return metadata, body


def _guess_image_format(url: str) -> str:
    """Guess image format from URL/file extension."""
    url_lower = url.lower().split("?")[0]
    for ext in ("png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff", "svg"):
        if url_lower.endswith("." + ext):
            return "jpeg" if ext == "jpg" else ext
    return "png"


def extract_markdown_images(
    content: str,
    *,
    base_dir: Path | None = None,
    url_resolver=None,
) -> tuple[str, list[ImageInfo]]:
    """Find ![alt](url) in markdown, resolve local images, replace with :::image blocks.

    Args:
        content: Markdown text.
        base_dir: For relative URLs, resolve against this directory.
        url_resolver: Optional callable(url) -> bytes | None for absolute URLs
            (e.g. Note image URLs → local file read).

    Returns:
        (updated_content, list_of_ImageInfo).
    """
    images: list[ImageInfo] = []

    def _repl(m: re.Match) -> str:
        alt = m.group(1) or ""
        url = m.group(2)

        # Skip URLs that are already data URIs or inside ::: blocks (shouldn't happen,
        # but guard against edge cases)
        if url.startswith("data:"):
            return m.group(0)

        img_bytes: bytes | None = None

        # 1. Try custom resolver (e.g. Note image URLs)
        if url_resolver is not None:
            try:
                img_bytes = url_resolver(url)
            except Exception:
                pass

        # 2. Try relative path from base_dir
        if img_bytes is None and base_dir is not None and not url.startswith(("http://", "https://", "/")):
            img_path = (base_dir / url).resolve()
            if img_path.is_file():
                try:
                    img_bytes = img_path.read_bytes()
                except Exception:
                    pass

        if img_bytes is None:
            return m.group(0)  # can't resolve → keep as-is

        image_id = uuid.uuid4().hex
        fmt = _guess_image_format(url)
        img = ImageInfo(
            image_id=image_id,
            alt_text=alt,
            image_bytes=img_bytes,
            image_format=fmt,
        )
        images.append(img)
        return build_image_block(img)

    # Only process content outside ::: fenced blocks
    # Split on ::: blocks, process text segments, preserve blocks
    parts = re.split(r'(^:::[\s\S]*?^:::$)', content, flags=re.MULTILINE)
    result_parts: list[str] = []
    for part in parts:
        if part.startswith(":::") and part.rstrip().endswith(":::"):
            result_parts.append(part)  # preserve existing ::: blocks
        else:
            result_parts.append(_MD_IMAGE_RE.sub(_repl, part))

    return "".join(result_parts), images


class MarkdownParser(DocumentParser):
    def parse(self, path: Path) -> ParsedDocument:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, body = _extract_frontmatter(text)

        # Extract local images (relative paths)
        base_dir = path.parent
        body, images = extract_markdown_images(body, base_dir=base_dir)

        meta: dict = {"format": "markdown"}
        if frontmatter:
            meta["frontmatter"] = frontmatter

        return ParsedDocument(
            content=body,
            metadata=meta,
            source_path=str(path),
            file_type="markdown",
            images=images,
        )
