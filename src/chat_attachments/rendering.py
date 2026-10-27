"""Local, OCR-free page inspection and deferred rasterization for Chat."""
from __future__ import annotations

import base64
import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from src.parsers.pdf import _PDFIUM_RENDER_LOCK


def office_binary() -> str | None:
    if configured := os.environ.get("SINKDUCE_LIBREOFFICE", "").strip():
        path = Path(configured)
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    for name in ("soffice", "libreoffice"):
        if path := shutil.which(name):
            return path
    path = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    return str(path) if path.is_file() else None


def office_pdf(source: Path) -> Path | None:
    """Use an existing renderer; never download or install an Office suite."""
    binary = office_binary()
    if binary is None:
        return None
    destination = source.parent / "pages.pdf"
    if destination.is_file():
        return destination
    with tempfile.TemporaryDirectory(prefix=".office-", dir=source.parent) as work:
        work = Path(work)
        profile = (work / "profile").as_uri()
        result = subprocess.run(
            [binary, f"-env:UserInstallation={profile}", "--headless", "--convert-to", "pdf",
             "--outdir", str(work), str(source.resolve())],
            capture_output=True, timeout=90,
        )
        rendered = work / f"{source.stem}.pdf"
        if result.returncode or not rendered.is_file():
            raise ValueError("The installed presentation renderer could not convert this file")
        # Session deletion during conversion must not recreate its directory.
        if not source.is_file():
            raise FileNotFoundError(source)
        rendered.replace(destination)
    return destination


def inspect_pdf(path: Path, *, render_source: str = "source") -> tuple[str, list[dict], list[dict]]:
    import pdfplumber

    text, positions, images, offset = [], [], [], 0
    with pdfplumber.open(path) as pdf:
        for index, page in enumerate(pdf.pages):
            content = page.extract_text() or ""
            positions.append({"char_offset": offset, "label": f"Page {index + 1}",
                              "type": "page", "page_number": index + 1})
            text.append(content)
            offset += len(content) + 2
            images.append({"id": f"page-{index + 1}", "page_number": index + 1,
                           "render_source": render_source, "ocr": "", "is_page_image": True,
                           "has_text_layer": bool(content.strip()),
                           "text_only": bool(content.strip()) and not any((page.images, page.curves, page.rects, page.lines))})
    return "\n\n".join(text), positions, images


def render_page(path: Path, page_number: int) -> str:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        if not 1 <= page_number <= len(pdf.pages):
            raise ValueError("Page number is out of range")
        page = pdf.pages[page_number - 1]
        resolution = min(150, 2048 * 72 / max(page.width, page.height))
        with _PDFIUM_RENDER_LOCK:
            image = page.to_image(resolution=resolution).original.convert("RGB")
        image.thumbnail((2048, 2048))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode()
