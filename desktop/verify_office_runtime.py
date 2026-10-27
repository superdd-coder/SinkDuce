#!/usr/bin/env python3
"""Fail a desktop build if its own Python and Writer cannot export a report."""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

import pdfplumber

from src.chat_attachments.rendering import office_binary
from src.reports.rendering import render_pdf


def main() -> None:
    expected = Path(os.environ["SINKDUCE_LIBREOFFICE"]).resolve()
    resolved = office_binary()
    if not resolved or Path(resolved).resolve() != expected:
        raise RuntimeError("Desktop export selected an external Office installation")
    root = Path(os.environ["SINKDUCE_DATA"])
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="writer-check-", dir=root) as work:
        tempfile.tempdir = work
        report = {"title": "Export verification", "citations": [],
                  "markdown": "## Meeting notes / 会议纪要\n\nIndependent synthetic content.\n\n| Item | Count |\n|---|---|\n|样品|17|"}
        pdf = render_pdf(report)
        if not pdf.startswith(b"%PDF-"):
            raise RuntimeError("Desktop export did not produce a PDF")
        with pdfplumber.open(io.BytesIO(pdf)) as document:
            text = "\n".join(page.extract_text() or "" for page in document.pages)
        if any(value not in text for value in ("Export verification", "17", "会议纪要", "样品")):
            raise RuntimeError("Desktop PDF content verification failed")
        print(f"Bundled Writer PDF check passed ({len(pdf)} bytes)")


if __name__ == "__main__":
    main()
