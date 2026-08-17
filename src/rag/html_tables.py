"""HTML table → GitHub-Flavored Markdown (cell text only).

Used at chunk time so embeddings / the LLM see rows and headers, not
``<th><p>`` wrappers. Parse/display can keep the original HTML (Tiptap
still needs it for colspan/rowspan).
"""

from __future__ import annotations

import html as html_lib
import re

_TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
_CELL_RE = re.compile(
    r"<t([dh])\b([^>]*)>(.*?)</t\1>",
    re.DOTALL | re.IGNORECASE,
)
_SPAN_RE = re.compile(
    r"""(colspan|rowspan)\s*=\s*(?:["'](\d+)["']|(\d+))""",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)


def _cell_spans(attrs: str) -> tuple[int, int]:
    colspan = rowspan = 1
    for match in _SPAN_RE.finditer(attrs or ""):
        n = int(match.group(2) or match.group(3) or 1)
        if match.group(1).lower() == "colspan":
            colspan = max(1, n)
        else:
            rowspan = max(1, n)
    return colspan, rowspan


def _cell_text(inner: str) -> str:
    text = _TAG_RE.sub(" ", inner or "")
    text = html_lib.unescape(text)
    text = " ".join(text.split())
    return text.replace("|", "\\|")


def html_table_to_markdown(html: str) -> str:
    """Convert one ``<table>…</table>`` blob to a GFM table.

    Colspan repeats the cell; rowspan copies the text downward. On failure
    (no rows) the original *html* is returned so callers can keep HTML.
    """
    if not html or "<table" not in html.lower():
        return html

    rows: list[list[str]] = []
    # (row, col) → (text, remaining rowspan including this row)
    carry: dict[tuple[int, int], tuple[str, int]] = {}

    for ri, tr in enumerate(_TR_RE.findall(html)):
        cells: list[str] = []
        ci = 0
        while (ri, ci) in carry:
            text, rem = carry[(ri, ci)]
            cells.append(text)
            if rem > 1:
                carry[(ri + 1, ci)] = (text, rem - 1)
            ci += 1

        for _kind, attrs, inner in _CELL_RE.findall(tr):
            while (ri, ci) in carry:
                text, rem = carry[(ri, ci)]
                cells.append(text)
                if rem > 1:
                    carry[(ri + 1, ci)] = (text, rem - 1)
                ci += 1
            colspan, rowspan = _cell_spans(attrs)
            text = _cell_text(inner)
            for _ in range(colspan):
                cells.append(text)
                if rowspan > 1:
                    carry[(ri + 1, ci)] = (text, rowspan - 1)
                ci += 1

        while (ri, ci) in carry:
            text, rem = carry[(ri, ci)]
            cells.append(text)
            if rem > 1:
                carry[(ri + 1, ci)] = (text, rem - 1)
            ci += 1

        if cells:
            rows.append(cells)

    if not rows:
        return html

    width = max(len(r) for r in rows)
    for row in rows:
        if len(row) < width:
            row.extend([""] * (width - len(row)))

    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join("---" for _ in range(width)) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(lines)
