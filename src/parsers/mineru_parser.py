"""MinerU cloud document parser.

Uploads documents to MinerU's Precision Parsing API, which produces
high-quality Markdown output with preserved tables, formulas, and layout.
"""

from __future__ import annotations

import io
import json
import logging
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx

import re as _re_module

from src.parsers.base import ParsedDocument

logger = logging.getLogger(__name__)


def _html_table_to_markdown(html: str) -> str:
    """Convert an HTML <table> to a GitHub-Flavored Markdown table.

    Handles colspan by repeating cells and rowspan via placeholder.
    Returns the original HTML string if parsing fails.
    """
    try:
        # Parse rows
        rows: list[list[str]] = []
        rowspan_tracker: dict[tuple[int, int], tuple[str, int]] = {}  # (row, col) → (text, remaining)

        tr_matches = _re_module.findall(r"<tr[^>]*>(.*?)</tr>", html, _re_module.DOTALL | _re_module.IGNORECASE)
        for ri, tr in enumerate(tr_matches):
            cells: list[str] = []
            ci = 0
            # Add carry-over rowspan cells
            while (ri, ci) in rowspan_tracker:
                text, rem = rowspan_tracker[(ri, ci)]
                cells.append(text)
                if rem > 1:
                    rowspan_tracker[(ri + 1, ci)] = (text, rem - 1)
                ci += 1

            td_matches = _re_module.findall(
                r"<t[dh][^>]*?(?:colspan\s*=\s*[\"'](\d+)[\"'][^>]*?)?(?:rowspan\s*=\s*[\"'](\d+)[\"'][^>]*?)?>(.*?)</t[dh]>",
                tr, _re_module.DOTALL | _re_module.IGNORECASE,
            )
            for m in td_matches:
                colspan = int(m[0]) if m[0] else 1
                rowspan = int(m[1]) if m[1] else 1
                text = _re_module.sub(r"<[^>]+>", "", m[2]).strip().replace("|", "\\|").replace("\n", " ")
                # Handle LaTeX math delimiters — keep them intact
                for _ in range(colspan):
                    cells.append(text)
                    ci += 1
                # Register rowspan
                if rowspan > 1:
                    for d in range(colspan):
                        rowspan_tracker[(ri + 1, ci - colspan + d)] = (text, rowspan - 1)

            if cells:
                rows.append(cells)

        if not rows:
            return html

        # Normalize column count
        max_cols = max(len(r) for r in rows)
        for r in rows:
            while len(r) < max_cols:
                r.append("")

        # Build Markdown table
        lines: list[str] = []
        for ri, row in enumerate(rows):
            lines.append("| " + " | ".join(row) + " |")
            if ri == 0:
                lines.append("| " + " | ".join(["---"] * max_cols) + " |")

        return "\n".join(lines)
    except Exception:
        return html


def _clean_markdown_for_tiptap(md: str) -> str:
    """Convert MinerU-produced Markdown to Tiptap-compatible format.

    Transformations:
    1. HTML tables with colspan/rowspan → kept as-is (preserve merged cells)
       Flat HTML tables → converted to GFM (cleaner, more compatible)
    2. PDF font-encoding artifacts → standard Markdown characters
       (PUA bullets, circle marks, misrecognised "o " bullets, etc.)
    """
    # ── 1. HTML tables: keep with merged cells, convert flat tables to GFM ──
    _HAS_SPAN_RE = _re_module.compile(r"(?:colspan|rowspan)\s*=", _re_module.IGNORECASE)

    def _replace_if_flat(match: _re_module.Match) -> str:
        html = match.group(0)
        if _HAS_SPAN_RE.search(html):
            return html  # preserve merged cells
        return _html_table_to_markdown(html)  # convert flat table

    md = _re_module.sub(
        r"<table[^>]*>.*?</table>",
        _replace_if_flat,
        md,
        flags=_re_module.DOTALL | _re_module.IGNORECASE,
    )

    # ── 2. PDF font-encoding artifacts → standard Markdown ──
    _BULLET_CHARS = (
        ""   # PUA: ZapfDingbats/Symbol remapping
        "○●◯"       # ○ ● ◯ (circle bullets)
        "•‣◦"         # • ‣ ◦ (bullet, triangular bullet, white bullet)
        "·"                      # · middle-dot
        "°"                      # ° degree (misused as bullet)
        "¤"                      # ¤ currency sign (sometimes garbled bullet)
        "§"                      # § section sign (misused as bullet)
    )

    # a) Line-start bullets → "- " (real markdown list items), preserving indent
    md = _re_module.sub(
        rf"^(\s*)[{_BULLET_CHARS}]\s*",
        r"\1- ",
        md,
        flags=_re_module.MULTILINE,
    )

    # "o " at line start followed by a capital letter — misrecognised bullet
    md = _re_module.sub(r"^(\s*)o (?=[A-Z])", r"\1- ", md, flags=_re_module.MULTILINE)

    # b) Bullet chars inside table cells (not at line start — preceded by "|")
    #    → remove the bullet char, it's only decorative in the original PDF.
    #    Table-cell text can't be a real markdown list item anyway.
    for ch in _BULLET_CHARS:
        md = md.replace(f"| {ch} ", "| ").replace(f"|{ch} ", "| ")
        md = md.replace(f"| {ch}", "| ").replace(f"|{ch}", "| ")
    # Also handle "o " inside table cells
    md = _re_module.sub(r"\| o (?=[A-Z])", "| ", md)

    return md

# File extensions supported by MinerU's Precision Parsing API.
# .docx excluded — always uses local mammoth parser for better Markdown output.
MINERU_SUPPORTED_EXTENSIONS = {
    ".pdf", ".doc", ".ppt", ".pptx",
    ".xls", ".xlsx", ".html",
    ".png", ".jpg", ".jpeg", ".jp2", ".webp", ".gif", ".bmp",
}


class MinerUError(Exception):
    """Raised when MinerU API returns an error."""

    def __init__(self, message: str, code: str | int | None = None):
        super().__init__(message)
        self.code = code


class MinerUParser:
    """Parse documents via MinerU's Precision Parsing cloud API.

    Flow:
    1. POST /file-urls/batch → get signed upload URL + batch_id
    2. PUT file binary to signed URL
    3. Poll /extract-results/batch/{batch_id} until done
    4. Download zip → extract full.md + layout.json
    5. Return ParsedDocument with Markdown content and position_map
    """

    def __init__(
        self,
        api_token: str,
        base_url: str = "https://mineru.net/api/v4",
        model_version: str = "pipeline",
        is_ocr: bool = False,
        enable_formula: bool = True,
        enable_table: bool = True,
        language: str = "ch",
        poll_interval: float = 3.0,
        poll_timeout: float = 300.0,
    ):
        self.api_token = api_token
        self.base_url = base_url.rstrip("/")
        self.model_version = model_version
        self.is_ocr = is_ocr
        self.enable_formula = enable_formula
        self.enable_table = enable_table
        self.language = language
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    def parse(self, path: Path) -> ParsedDocument:
        """Parse a file via MinerU API and return a ParsedDocument."""
        ext = path.suffix.lower()
        if ext not in MINERU_SUPPORTED_EXTENSIONS:
            raise MinerUError(f"Unsupported file type for MinerU: {ext}")

        file_size = path.stat().st_size
        if file_size > 200 * 1024 * 1024:  # 200MB
            raise MinerUError("File exceeds MinerU's 200MB limit")
        if file_size == 0:
            raise MinerUError("File is empty")

        filename = path.name
        file_bytes = path.read_bytes()

        # Step 1: Get signed upload URL
        batch_id, upload_url = self._request_upload_url(filename)

        # Step 2: Upload file to signed URL
        self._upload_file(upload_url, file_bytes)

        # Step 3: Poll for results
        result = self._poll_batch(batch_id, filename)

        # Step 4: Download and parse result zip
        return self._download_result(result, path)

    def _request_upload_url(self, filename: str) -> tuple[str, str]:
        """Request signed upload URL via batch endpoint. Returns (batch_id, upload_url)."""
        url = f"{self.base_url}/file-urls/batch"
        payload = {
            "files": [
                {
                    "name": filename,
                    "is_ocr": self.is_ocr,
                    "enable_formula": self.enable_formula,
                    "enable_table": self.enable_table,
                    "language": self.language,
                    "model_version": self.model_version,
                }
            ],
            "enable_formula": self.enable_formula,
            "enable_table": self.enable_table,
            "language": self.language,
            "model_version": self.model_version,
        }

        with httpx.Client(timeout=30) as client:
            resp = client.post(url, json=payload, headers=self._headers())
            self._check_response(resp)

        data = resp.json().get("data", {})
        batch_id = data.get("batch_id", "")
        file_urls = data.get("file_urls", [])
        if not batch_id or not file_urls:
            raise MinerUError("MinerU did not return upload URL", code="NO_UPLOAD_URL")

        logger.info("[MinerU] Got upload URL for '%s', batch_id=%s", filename, batch_id[:16])
        return batch_id, file_urls[0]

    def _upload_file(self, upload_url: str, file_bytes: bytes) -> None:
        """Upload file binary to the signed OSS URL."""
        with httpx.Client(timeout=120) as client:
            resp = client.put(
                upload_url,
                content=file_bytes,
                headers={"Content-Length": str(len(file_bytes))},
            )
            if resp.status_code >= 400:
                raise MinerUError(
                    f"File upload failed: HTTP {resp.status_code}",
                    code="UPLOAD_FAILED",
                )
        logger.info("[MinerU] File uploaded (%d bytes)", len(file_bytes))

    def _poll_batch(self, batch_id: str, filename: str) -> dict[str, Any]:
        """Poll batch results until the file is done or timeout/failure."""
        url = f"{self.base_url}/extract-results/batch/{batch_id}"
        deadline = time.monotonic() + self.poll_timeout

        with httpx.Client(timeout=30) as client:
            while time.monotonic() < deadline:
                resp = client.get(url, headers=self._headers())
                self._check_response(resp)

                data = resp.json().get("data", {})
                results = data.get("extract_result", [])

                for item in results:
                    if item.get("file_name") == filename or len(results) == 1:
                        state = item.get("state", "")
                        if state == "done":
                            logger.info("[MinerU] Parsing done for '%s'", filename)
                            return item
                        elif state in ("failed",):
                            err_msg = item.get("err_msg", "Unknown error")
                            raise MinerUError(
                                f"MinerU parsing failed: {err_msg}",
                                code="PARSE_FAILED",
                            )
                        else:
                            progress = item.get("extract_progress", {})
                            extracted = progress.get("extracted_pages", 0)
                            total = progress.get("total_pages", 0)
                            logger.debug(
                                "[MinerU] '%s' state=%s, pages=%d/%d",
                                filename, state, extracted, total,
                            )

                time.sleep(self.poll_interval)

        raise MinerUError(
            f"MinerU parsing timed out after {self.poll_timeout}s",
            code="TIMEOUT",
        )

    def _download_result(self, result: dict[str, Any], source_path: Path) -> ParsedDocument:
        """Download the result zip and extract Markdown content + position_map + images."""
        zip_url = result.get("full_zip_url", "")
        if not zip_url:
            raise MinerUError("No result zip URL returned", code="NO_RESULT")

        with httpx.Client(timeout=60) as client:
            resp = client.get(zip_url)
            resp.raise_for_status()

        zip_bytes = resp.content
        markdown_content = ""
        position_map: list[dict] = []
        layout_data: dict[str, Any] = {}
        images_dir_files: dict[str, bytes] = {}  # filename → bytes from images/ dir

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            logger.info("[MinerU] Zip contents: %d files", len(names))

            # Extract full.md
            for name in names:
                if name.endswith("full.md"):
                    markdown_content = zf.read(name).decode("utf-8")
                    break

            if not markdown_content:
                raise MinerUError("No full.md found in result zip", code="NO_MARKDOWN")

            # Extract layout.json for position mapping
            for name in names:
                if name.endswith("layout.json") or name.endswith("middle.json"):
                    try:
                        layout_data = json.loads(zf.read(name).decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        logger.warning("[MinerU] Failed to parse layout file '%s': %s", name, e)
                    break

            # Extract images from images/ directory
            for name in names:
                if "/images/" in name or name.startswith("images/"):
                    try:
                        file_data = zf.read(name)
                        filename = name.rsplit("/", 1)[-1]
                        images_dir_files[filename] = file_data
                    except Exception as e:
                        logger.warning("[MinerU] Failed to read image '%s': %s", name, e)

        # ── Extract image metadata from layout.json ──
        from src.parsers.base import ImageInfo
        import uuid as _uuid

        all_images: list[ImageInfo] = []
        pages = layout_data if isinstance(layout_data, list) else layout_data.get("pdf_info", [])

        # Collect image references from all block types (recursively).
        # Table blocks have nested sub-blocks (table_body, table_footnote)
        # where image_path lives inside blocks[].lines[].spans[].
        def _collect_image_paths(
            block: dict, page_number: int, bbox, block_type: str,
            result: dict[str, dict],
        ) -> None:
            for line in (block.get("lines", []) if isinstance(block, dict) else []):
                for span in (line.get("spans", []) if isinstance(line, dict) else []):
                    img_path = span.get("image_path", "")
                    if img_path:
                        fn = img_path.rsplit("/", 1)[-1]
                        result[fn] = {
                            "page_number": page_number,
                            "bbox": bbox,
                            "block_type": block_type,
                            "table_html": span.get("html", ""),
                        }
            for sub in (block.get("blocks", []) if isinstance(block, dict) else []):
                _collect_image_paths(sub, page_number, bbox, block_type, result)

        layout_image_map: dict[str, dict] = {}  # image_filename → {page, bbox, block_type}
        for page in (pages if isinstance(pages, list) else []):
            page_idx = page.get("page_idx", 0)
            for block in (page.get("para_blocks", page.get("blocks", [])) or []):
                block_type = (block.get("type") or "").lower()
                bbox = block.get("bbox")
                _collect_image_paths(block, page_idx + 1, bbox, block_type, layout_image_map)

        # ── Find which images are actually referenced in full.md ──
        # MinerU may include images in the zip that it already converted to
        # tables/formulas in the markdown — those must NOT become :::image blocks
        # or go through OCR / Vision LLM.
        _REF_RE = _re_module.compile(r"!\[[^\]]*\]\(images/([^)]+)\)")
        _referenced_filenames: set[str] = set()
        for _m in _REF_RE.finditer(markdown_content):
            _ref = _m.group(1)
            _fn = _ref.rsplit("/", 1)[-1] if "/" in _ref else _ref
            _referenced_filenames.add(_fn)

        logger.info("[MinerU] %d images in zip, %d referenced in markdown",
                    len(images_dir_files), len(_referenced_filenames))

        # Build ImageInfo ONLY for referenced images, keyed by filename.
        img_by_filename: dict[str, ImageInfo] = {}
        for filename, file_data in images_dir_files.items():
            if not filename or filename not in _referenced_filenames:
                continue
            img_id = _uuid.uuid4().hex
            fmt = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
            layout_info = layout_image_map.get(filename, {})
            img_by_filename[filename] = ImageInfo(
                image_id=img_id,
                page_number=layout_info.get("page_number"),
                bbox=layout_info.get("bbox"),
                image_bytes=file_data,
                image_format=fmt,
            )

        all_images = list(img_by_filename.values())
        _skipped = len(images_dir_files) - len(all_images)
        if _skipped:
            logger.info("[MinerU] Skipped %d zip images (already converted to tables/formulas)", _skipped)
        logger.info("[MinerU] Extracted %d images from zip", len(all_images))

        # ── Replace ![](images/xxx.png) with :::image blocks ──
        def _replace_image_ref(match: _re_module.Match) -> str:
            ref = match.group(1) or ""
            filename = ref.rsplit("/", 1)[-1] if "/" in ref else ref
            img = img_by_filename.get(filename)
            if img is None:
                return match.group(0)
            return (
                f":::image\n"
                f"image_id: {img.image_id}\n"
                f"file_id: \n"
                f"description: \n"
                f":::"
            )

        markdown_content = _REF_RE.sub(_replace_image_ref, markdown_content)

        # ── Insert table source images before <table> blocks ──
        # MinerU puts both `html` and `image_path` in the same span — use the
        # HTML content as the lookup key for 100% accurate matching.
        _TABLE_RE = _re_module.compile(r"<table[\s>][\s\S]*?</table>", _re_module.IGNORECASE)

        # Build normalized-html → (filename, data, info) map from layout.json.
        # MinerU uses <eq>...</eq> in span.html but $...$ in full.md — normalize
        # both sides to $...$ + collapsed whitespace for reliable matching.
        _EQ_RE = _re_module.compile(r"<eq>(.*?)</eq>", _re_module.DOTALL)
        # MinerU also changes spacing around $...$ between span.html and full.md
        _MATH_SPACE_RE = _re_module.compile(r"\s*([$][^$]+[$])\s*")
        _norm_html = lambda s: _re_module.sub(
            r"\s+", " ", _MATH_SPACE_RE.sub(r"\1", _EQ_RE.sub(r"$\1$", s))
        ).strip()

        _html_to_img: dict[str, tuple[str, bytes, dict]] = {}
        for fn, info in layout_image_map.items():
            if info.get("block_type") != "table":
                continue
            if fn in _referenced_filenames:
                continue
            data = images_dir_files.get(fn)
            html = info.get("table_html", "")
            if data and html:
                _html_to_img[_norm_html(html)] = (fn, data, info)

        if _html_to_img:
            _insert_count = [0]

            def _insert_table_source(match: _re_module.Match) -> str:
                key = _norm_html(match.group(0))
                entry = _html_to_img.pop(key, None)
                if entry is None:
                    return match.group(0)

                fn, data, info = entry
                _insert_count[0] += 1
                img_id = _uuid.uuid4().hex
                fmt = fn.rsplit(".", 1)[-1].lower() if "." in fn else "png"
                logger.info(
                    "[MinerU] Table #%d matched → image %s (page=%s)",
                    _insert_count[0], fn[:24], info.get("page_number"),
                )
                img = ImageInfo(
                    image_id=img_id,
                    page_number=info.get("page_number"),
                    bbox=info.get("bbox"),
                    image_bytes=data,
                    image_format=fmt,
                    is_table_source=True,
                    ocr_text="",
                    description="",
                )
                img_by_filename[fn] = img
                return (
                    f":::image\n"
                    f"image_id: {img.image_id}\n"
                    f"file_id: \n"
                    f":::\n"
                    f"{match.group(0)}"
                )

            markdown_content = _TABLE_RE.sub(_insert_table_source, markdown_content)
            all_images = list(img_by_filename.values())
            logger.info(
                "[MinerU] Inserted %d table source images via HTML-content matching",
                _insert_count[0],
            )

        # Build position_map from layout data
        # Clean MinerU Markdown for Tiptap compatibility (HTML tables → GFM tables)
        original_len = len(markdown_content)
        markdown_content = _clean_markdown_for_tiptap(markdown_content)
        if len(markdown_content) != original_len:
            logger.info("[MinerU] Cleaned markdown for Tiptap: %d → %d chars", original_len, len(markdown_content))

        position_map = self._build_position_map(layout_data, markdown_content)
        logger.info("[MinerU] Built position_map with %d entries (layout keys: %s)",
                    len(position_map), list(layout_data.keys()) if isinstance(layout_data, dict) else f"list[{len(layout_data)}]")

        return ParsedDocument(
            content=markdown_content,
            metadata={
                "source": "mineru",
                "original_file_type": source_path.suffix.lstrip(".").lower(),
                "original_filename": source_path.name,
                "model_version": self.model_version,
            },
            source_path=str(source_path),
            file_type="markdown",
            position_map=position_map,
            images=all_images,
        )

    def _build_position_map(
        self, layout_data: dict[str, Any], markdown_content: str
    ) -> list[dict]:
        """Build position_map from MinerU's layout.json.

        The layout data typically contains page-level block information.
        We extract page boundaries mapped to character offsets in the Markdown.
        """
        position_map: list[dict] = []

        # MinerU layout.json contains an array of page objects.
        # Each page has a "page_idx" and blocks with type/position info.
        pages = layout_data if isinstance(layout_data, list) else layout_data.get("pdf_info", [])

        if not pages:
            # No layout data — build position_map from markdown headings as fallback
            logger.info("[MinerU] No page-level layout data, building position_map from markdown headings")
            import re as _re
            offset = 0
            for m in _re.finditer(r"^(#{1,6})\s+(.+)$", markdown_content, _re.MULTILINE):
                position_map.append({
                    "char_offset": m.start(),
                    "label": m.group(0).strip(),
                    "type": "section",
                })
            return position_map

        # Strategy: scan markdown for page markers or heading patterns
        # and map them to positions in the text.
        current_offset = 0
        for i, page in enumerate(pages):
            page_idx = page.get("page_idx", i)
            # Each page contributes to the markdown; we estimate its offset
            # by looking for the next chunk of content
            position_map.append({
                "char_offset": current_offset,
                "label": f"Page {page_idx + 1}",
                "type": "page",
                "page_number": page_idx + 1,
            })

            # Estimate the content length contributed by this page
            # by looking at the blocks within it
            blocks = page.get("para_blocks", [])
            page_text_len = 0
            for block in blocks:
                # Each block has lines with spans containing text
                lines = block.get("lines", [])
                for line in lines:
                    spans = line.get("spans", [])
                    for span in spans:
                        page_text_len += len(span.get("content", ""))
            current_offset += page_text_len

        return position_map

    @staticmethod
    def _check_response(resp: httpx.Response) -> None:
        """Check MinerU API response for errors."""
        if resp.status_code == 429:
            raise MinerUError("MinerU rate limit exceeded. Please retry later.", code=429)
        if resp.status_code >= 400:
            try:
                body = resp.json()
                msg = body.get("msg", resp.text)
                code = body.get("code", resp.status_code)
            except Exception:
                msg = resp.text
                code = resp.status_code
            raise MinerUError(f"MinerU API error: {msg}", code=code)

        # Check business-level error codes
        try:
            body = resp.json()
        except Exception:
            return

        api_code = body.get("code")
        if api_code is not None and api_code != 0:
            msg = body.get("msg", "Unknown error")
            raise MinerUError(f"MinerU error: {msg}", code=api_code)


def parse_with_mineru(path: Path, mineru_config: Any) -> ParsedDocument:
    """Convenience function to parse a file with MinerU using app config."""
    parser = MinerUParser(
        api_token=mineru_config.api_token,
        base_url=mineru_config.base_url,
        model_version=mineru_config.model_version,
        is_ocr=mineru_config.is_ocr,
        enable_formula=mineru_config.enable_formula,
        enable_table=mineru_config.enable_table,
        language=mineru_config.language,
        poll_interval=mineru_config.poll_interval,
        poll_timeout=mineru_config.poll_timeout,
    )
    return parser.parse(path)
