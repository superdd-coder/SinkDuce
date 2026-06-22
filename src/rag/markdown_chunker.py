"""Markdown-aware chunker that preserves block structure.

Parses Markdown into semantic blocks (headings, tables, code fences, lists,
paragraphs, etc.) then aggregates blocks into chunks respecting token budgets.
Blocks are never split inappropriately — tables, code blocks, and lists are
treated as atomic units unless they exceed the hard limit, in which case
safe sub-splitting rules apply (header-row preservation for tables, fence
preservation for code blocks, item-boundary splitting for lists).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from src.rag.chunker import Chunk, _annotate_position, _estimate_tokens, _split_sentences


# ── Block Types ──────────────────────────────────────────────

@dataclass
class MarkdownBlock:
    """A semantic block parsed from Markdown."""
    block_type: str  # heading, paragraph, code, table, list, blockquote, hr, html
    content: str
    start_offset: int
    end_offset: int
    heading_level: int = 0  # 0 for non-heading blocks
    heading_path: list[str] = field(default_factory=list)  # breadcrumb, e.g. ["# Title", "## Sec"]


# ── Block Parser ─────────────────────────────────────────────

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\|.+\|\s*$", re.MULTILINE)
_HTML_TABLE_OPEN_RE = re.compile(r"^<table\b", re.IGNORECASE)
_HTML_TABLE_CLOSE_RE = re.compile(r"</table>\s*$", re.IGNORECASE)
_LIST_RE = re.compile(r"^(\s*[-*+]|\s*\d+\.)\s", re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"^>\s?", re.MULTILINE)
_HR_RE = re.compile(r"^[-*_]{3,}\s*$", re.MULTILINE)


def _parse_blocks(text: str) -> list[MarkdownBlock]:
    """Parse Markdown text into a list of semantic blocks.

    Uses a line-by-line state machine. Each line is classified and grouped
    into the appropriate block type.
    """
    if not text or not text.strip():
        return []

    lines = text.split("\n")
    blocks: list[MarkdownBlock] = []
    current_lines: list[str] = []
    current_type = "paragraph"
    current_start = 0
    in_fence = False
    fence_marker = ""

    def flush(offset: int) -> None:
        nonlocal current_lines, current_type, current_start
        if not current_lines:
            return
        content = "\n".join(current_lines).strip()
        if content:
            blocks.append(MarkdownBlock(
                block_type=current_type,
                content=content,
                start_offset=current_start,
                end_offset=offset,
            ))
        current_lines = []
        current_type = "paragraph"

    char_offset = 0
    for line in lines:
        line_len = len(line) + 1  # +1 for \n

        # Inside a fenced code block — keep collecting until closing fence
        if in_fence:
            current_lines.append(line)
            stripped = line.strip()
            if stripped.startswith(fence_marker[:3]) and len(stripped.replace(fence_marker[0], "").strip()) == 0:
                # Closing fence
                in_fence = False
                flush(char_offset + line_len)
                char_offset += line_len
                continue
            char_offset += line_len
            continue

        stripped = line.strip()

        # Empty line — flush current block
        if not stripped:
            flush(char_offset)
            char_offset += line_len
            continue

        # Horizontal rule
        if _HR_RE.match(stripped) and not stripped.startswith("|"):
            flush(char_offset)
            blocks.append(MarkdownBlock(
                block_type="hr", content=stripped,
                start_offset=char_offset, end_offset=char_offset + line_len,
            ))
            char_offset += line_len
            continue

        # Heading
        heading_match = _HEADING_RE.match(stripped)
        if heading_match:
            flush(char_offset)
            level = len(heading_match.group(1))
            blocks.append(MarkdownBlock(
                block_type="heading",
                content=stripped,
                start_offset=char_offset,
                end_offset=char_offset + line_len,
                heading_level=level,
            ))
            char_offset += line_len
            continue

        # Fenced code block opening
        fence_match = _FENCE_RE.match(stripped)
        if fence_match:
            flush(char_offset)
            in_fence = True
            fence_marker = fence_match.group(1)[:3]  # ``` or ~~~
            current_type = "code"
            current_lines = [line]
            current_start = char_offset
            char_offset += line_len
            continue

        # HTML table (<table>...</table> — single or multi-line)
        if _HTML_TABLE_OPEN_RE.match(stripped) or current_type == "html_table":
            if current_type != "html_table":
                flush(char_offset)
                current_type = "html_table"
                current_start = char_offset
            current_lines.append(line)
            char_offset += line_len
            if _HTML_TABLE_CLOSE_RE.search(stripped):
                flush(char_offset)
            continue

        # GFM table row
        if _TABLE_ROW_RE.match(stripped):
            if current_type != "table":
                flush(char_offset)
                current_type = "table"
                current_start = char_offset
            current_lines.append(line)
            char_offset += line_len
            continue

        # List item
        if _LIST_RE.match(stripped):
            if current_type != "list":
                flush(char_offset)
                current_type = "list"
                current_start = char_offset
            current_lines.append(line)
            char_offset += line_len
            continue

        # Blockquote
        if _BLOCKQUOTE_RE.match(stripped):
            if current_type != "blockquote":
                flush(char_offset)
                current_type = "blockquote"
                current_start = char_offset
            current_lines.append(line)
            char_offset += line_len
            continue

        # Default: paragraph
        if current_type != "paragraph":
            flush(char_offset)
            current_type = "paragraph"
            current_start = char_offset
        if not current_lines:
            current_start = char_offset
        current_lines.append(line)
        char_offset += line_len

    # Flush remaining
    flush(char_offset)

    # Build heading paths
    _assign_heading_paths(blocks)
    return blocks


def _assign_heading_paths(blocks: list[MarkdownBlock]) -> None:
    """Assign heading_path breadcrumbs to all blocks based on heading hierarchy."""
    path: list[str] = []  # e.g. ["# Title", "## Section"]
    for block in blocks:
        if block.block_type == "heading":
            level = block.heading_level
            # Pop deeper or same-level headings
            while path and _heading_level(path[-1]) >= level:
                path.pop()
            path.append(block.content)
        block.heading_path = list(path)


def _heading_level(heading_text: str) -> int:
    """Extract heading level from a heading string like '## Title'."""
    m = re.match(r"^(#{1,6})\s", heading_text)
    return len(m.group(1)) if m else 0


# ── Block Overflow Splitting ─────────────────────────────────


def _prune_empty_table_columns(table_text: str) -> str:
    """Remove columns that have no data (only header) from a Markdown table.

    When a large table is split into sub-tables, some columns may end up with
    empty cells across all data rows in that sub-table.  Pruning them keeps
    the chunk compact and avoids confusing the LLM with header-only columns.
    Returns the original text if pruning would leave zero columns.
    """
    lines = table_text.split("\n")
    if len(lines) < 3:
        return table_text

    def _parse_cells(row: str) -> list[str]:
        """Split a Markdown table row into cells, stripping surrounding whitespace."""
        stripped = row.strip()
        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|"):
            stripped = stripped[:-1]
        return [c.strip() for c in stripped.split("|")]

    header_cells = _parse_cells(lines[0])
    data_rows = lines[2:]  # skip header + separator
    col_count = len(header_cells)
    if col_count == 0:
        return table_text

    # Determine which columns have at least one non-empty data cell
    has_data = [False] * col_count
    for row in data_rows:
        cells = _parse_cells(row)
        for ci in range(min(len(cells), col_count)):
            if cells[ci]:
                has_data[ci] = True

    # If all columns have data, nothing to prune
    if all(has_data):
        return table_text

    # If no column has data (degenerate), keep original
    if not any(has_data):
        return table_text

    keep_indices = [i for i, ok in enumerate(has_data) if ok]

    def _rebuild_row(row: str, is_separator: bool = False) -> str:
        cells = _parse_cells(row)
        # Pad with empty strings if row has fewer cells than header
        padded = cells + [""] * max(0, col_count - len(cells))
        kept = [padded[i] for i in keep_indices]
        sep_cell = "---" if is_separator else ""
        return "| " + " | ".join(kept) + " |"

    rebuilt_lines = [
        _rebuild_row(lines[0]),
        _rebuild_row(lines[1], is_separator=True),
    ] + [_rebuild_row(r) for r in data_rows]

    return "\n".join(rebuilt_lines)


def _split_table_block(block: MarkdownBlock, max_tokens: int) -> list[MarkdownBlock]:
    """Split an oversized table by row groups, preserving the header row.

    Empty columns (no data across the *entire* table) are pruned globally
    before splitting, so they don't waste token budget.  Per-sub-table
    pruning is then applied as a safety net for columns that only have
    data in some sub-tables.

    The header row + separator are always included in output but do NOT
    count toward ``max_tokens`` — only data rows participate in the budget.
    This keeps sub-tables compact while letting each one carry more data.

    For the *first* sub-block ``start_offset`` points to the real header in
    the original text.  For *subsequent* sub-blocks ``start_offset`` points
    to the first data row in the original text (the repeated header is
    injected and doesn't exist there).
    """
    # ── Global prune: remove columns empty across ALL data rows ─────
    full_table = _prune_empty_table_columns(block.content)
    lines = full_table.split("\n")
    if len(lines) < 3:
        return [block]

    header_line = lines[0]
    separator_line = lines[1]
    data_lines = lines[2:]

    # Pre-compute position of each data line within the *original* block.content
    _orig_lines = block.content.split("\n")
    _data_start_positions: list[int] = []
    _pos = len(_orig_lines[0]) + 1 + len(_orig_lines[1]) + 1  # +1 for each \n
    for row_text in _orig_lines[2:]:
        _data_start_positions.append(_pos)
        _pos += len(row_text) + 1

    parts: list[MarkdownBlock] = []
    current_rows: list[str] = []
    current_tokens = 0  # header does NOT count toward max_tokens
    _data_start_idx = 0  # index into _data_start_positions for the current group

    for i, row in enumerate(data_lines):
        row_tokens = _estimate_tokens(row)
        if current_rows and current_tokens + row_tokens > max_tokens:
            # Flush current group
            part_content = _prune_empty_table_columns(
                header_line + "\n" + separator_line + "\n" + "\n".join(current_rows)
            )
            # First sub-block: start_offset = block.start_offset (real header)
            # Subsequent: start_offset = first data row's position in original text
            tbl_start = block.start_offset if _data_start_idx == 0 else (
                block.start_offset + _data_start_positions[_data_start_idx]
            )
            parts.append(MarkdownBlock(
                block_type="table", content=part_content,
                start_offset=tbl_start,
                end_offset=block.end_offset,
                heading_path=list(block.heading_path),
            ))
            current_rows = []
            current_tokens = 0  # header does NOT count
            _data_start_idx = i

        current_rows.append(row)
        current_tokens += row_tokens

    if current_rows:
        part_content = _prune_empty_table_columns(
            header_line + "\n" + separator_line + "\n" + "\n".join(current_rows)
        )
        tbl_start = block.start_offset if _data_start_idx == 0 else (
            block.start_offset + _data_start_positions[_data_start_idx]
        )
        parts.append(MarkdownBlock(
            block_type="table", content=part_content,
            start_offset=tbl_start,
            end_offset=block.end_offset,
            heading_path=list(block.heading_path),
        ))

    return parts if parts else [block]


def _split_code_block(block: MarkdownBlock, max_tokens: int) -> list[MarkdownBlock]:
    """Split an oversized code block at blank lines, preserving the fence.

    Each sub-block repeats the opening/closing fences so it remains valid code.
    For the *first* sub-block ``start_offset`` points to the real opening fence
    in the original text.  For *subsequent* sub-blocks ``start_offset`` points
    to the first body line in the original text (the repeated fences are
    injected and don't exist there).
    """
    lines = block.content.split("\n")
    if not lines:
        return [block]

    # Extract fence markers
    opening_fence = lines[0]
    closing_fence = lines[-1] if lines[-1].strip().startswith("```") or lines[-1].strip().startswith("~~~") else ""

    body_lines = lines[1:-1] if closing_fence else lines[1:]

    # Pre-compute position of each body line within block.content
    _body_positions: list[int] = []
    _pos = len(opening_fence) + 1  # +1 for \n after opening fence
    for bl in body_lines:
        _body_positions.append(_pos)
        _pos += len(bl) + 1

    parts: list[MarkdownBlock] = []
    current_lines: list[str] = []
    current_tokens = _estimate_tokens(opening_fence)
    body_start_idx = 0

    for i, line in enumerate(body_lines):
        line_tokens = _estimate_tokens(line)
        if current_lines and current_tokens + line_tokens > max_tokens:
            part_content = opening_fence + "\n" + "\n".join(current_lines) + "\n" + closing_fence
            code_start = block.start_offset if body_start_idx == 0 else (
                block.start_offset + _body_positions[body_start_idx]
            )
            parts.append(MarkdownBlock(
                block_type="code", content=part_content,
                start_offset=code_start,
                end_offset=block.end_offset,
                heading_path=list(block.heading_path),
            ))
            current_lines = []
            current_tokens = _estimate_tokens(opening_fence)
            body_start_idx = i

        current_lines.append(line)
        current_tokens += line_tokens

    if current_lines:
        part_content = opening_fence + "\n" + "\n".join(current_lines)
        if closing_fence:
            part_content += "\n" + closing_fence
        code_start = block.start_offset if body_start_idx == 0 else (
            block.start_offset + _body_positions[body_start_idx]
        )
        parts.append(MarkdownBlock(
            block_type="code", content=part_content,
            start_offset=code_start,
            end_offset=block.end_offset,
            heading_path=list(block.heading_path),
        ))

    return parts if parts else [block]


def _split_list_block(block: MarkdownBlock, max_tokens: int) -> list[MarkdownBlock]:
    """Split an oversized list at top-level item boundaries."""
    lines = block.content.split("\n")
    if len(lines) <= 1:
        return [block]

    # Pre-compute line start positions within block.content
    _line_positions: list[int] = [0]
    for ln in lines[:-1]:
        _line_positions.append(_line_positions[-1] + len(ln) + 1)  # +1 for \n

    # Group lines by top-level list items, tracking line index
    items: list[tuple[list[str], int]] = []  # (lines, start_line_index)
    current_item: list[str] = []
    cur_start_line = 0
    item_re = re.compile(r"^(\s*[-*+]|\s*\d+\.)\s")

    for i, line in enumerate(lines):
        if item_re.match(line.strip()) and not line.startswith("  "):
            if current_item:
                items.append((current_item, cur_start_line))
            current_item = [line]
            cur_start_line = i
        else:
            current_item.append(line)
    if current_item:
        items.append((current_item, cur_start_line))

    parts: list[MarkdownBlock] = []
    current_items: list[str] = []
    current_tokens = 0
    part_start_line = 0

    for item_lines, start_line in items:
        item_text = "\n".join(item_lines)
        item_tokens = _estimate_tokens(item_text)
        if current_items and current_tokens + item_tokens > max_tokens:
            content = "\n".join(current_items)
            actual_start = block.start_offset + _line_positions[part_start_line]
            parts.append(MarkdownBlock(
                block_type="list", content=content,
                start_offset=actual_start, end_offset=actual_start + len(content),
                heading_path=list(block.heading_path),
            ))
            current_items = []
            current_tokens = 0
            part_start_line = start_line
        if not current_items:
            part_start_line = start_line
        current_items.append(item_text)
        current_tokens += item_tokens

    if current_items:
        content = "\n".join(current_items)
        actual_start = block.start_offset + _line_positions[part_start_line]
        parts.append(MarkdownBlock(
            block_type="list", content=content,
            start_offset=actual_start, end_offset=actual_start + len(content),
            heading_path=list(block.heading_path),
        ))

    return parts if parts else [block]


def _split_paragraph_block(block: MarkdownBlock, max_tokens: int) -> list[MarkdownBlock]:
    """Split an oversized paragraph using sentence boundaries."""
    sentences = _split_sentences(block.content)
    if len(sentences) <= 1:
        # No sentence boundaries — hard split
        return [block]

    # Pre-compute sentence start positions within block.content
    _sent_positions: list[int] = []
    _pos = 0
    for s in sentences:
        idx = block.content.find(s, _pos)
        _sent_positions.append(idx if idx >= 0 else _pos)
        _pos = _sent_positions[-1] + len(s)

    parts: list[MarkdownBlock] = []
    current_parts: list[str] = []
    current_tokens = 0
    part_start_idx = 0

    for i, sent in enumerate(sentences):
        sent_tokens = _estimate_tokens(sent)
        if current_parts and current_tokens + sent_tokens > max_tokens:
            actual_start = block.start_offset + _sent_positions[part_start_idx]
            parts.append(MarkdownBlock(
                block_type="paragraph", content=" ".join(current_parts),
                start_offset=actual_start, end_offset=block.end_offset,
                heading_path=list(block.heading_path),
            ))
            current_parts = []
            current_tokens = 0
            part_start_idx = i
        current_parts.append(sent)
        current_tokens += sent_tokens

    if current_parts:
        actual_start = block.start_offset + _sent_positions[part_start_idx]
        parts.append(MarkdownBlock(
            block_type="paragraph", content=" ".join(current_parts),
            start_offset=actual_start, end_offset=block.end_offset,
            heading_path=list(block.heading_path),
        ))

    return parts if parts else [block]


def _split_html_table_block(block: MarkdownBlock, max_tokens: int) -> list[MarkdownBlock]:
    """Split an oversized HTML <table> by row groups, preserving headers.

    HTML tables can have ``<thead>``, ``<tbody>``, and ``<tfoot>`` sections.
    Each sub-block repeats ``<thead>`` (and optionally column-level ``<colgroup>``)
    so the table remains valid.  The opening ``<table ...>`` and closing
    ``</table>`` wrappers are also injected for every sub-block.

    ``start_offset`` for the first sub-block points to the original ``<table>``
    opening tag.  For subsequent sub-blocks it points to the first ``<tr>`` of
    the data rows in the original text (the repeated header/table-wrappers are
    injected — they don't exist at those positions).
    """
    import re as _re

    content = block.content

    # ── Extract opening <table ...> and closing </table> ──
    _table_open_m = _re.search(r"^<table[^>]*>", content, _re.IGNORECASE)
    _table_close_m = _re.search(r"</table>\s*$", content, _re.IGNORECASE)
    table_open = _table_open_m.group(0) if _table_open_m else "<table>"
    table_close = _table_close_m.group(0) if _table_close_m else "</table>"

    # Strip outer <table> wrappers — we'll re-wrap each sub-block
    inner = content
    if _table_open_m:
        inner = inner[_table_open_m.end():]
    if _table_close_m:
        inner = inner[:_table_close_m.start()]

    # ── Extract <thead> (header section, optional) ──
    _thead_m = _re.search(r"<thead[^>]*>(.*?)</thead>", inner, _re.DOTALL | _re.IGNORECASE)
    thead_content = _thead_m.group(0) if _thead_m else ""
    # If no explicit <thead>, the first <tr> might still be a header
    has_explicit_thead = bool(_thead_m)

    # ── Parse all <tr> groups (body rows) ──
    # Remove <thead> from inner before extracting body rows
    body_inner = inner
    if _thead_m:
        body_inner = inner[:_thead_m.start()] + inner[_thead_m.end():]

    # Extract remaining <tr> blocks (could be in <tbody>, <tfoot>, or bare)
    _tr_re = _re.compile(r"<tr[^>]*>(.*?)</tr>", _re.DOTALL | _re.IGNORECASE)
    all_tr_matches = list(_tr_re.finditer(body_inner))

    if not all_tr_matches:
        # No rows found — keep as single block
        return [block]

    # Determine which rows are "header" rows (to repeat for each sub-block)
    # First row(s) inside <thead> are header; if no <thead>, use the
    # first *data* row as the de-facto header template
    header_tr_content: list[str] = []  # raw text of each header <tr>
    data_tr_matches = all_tr_matches

    if _thead_m:
        header_trs = list(_tr_re.finditer(thead_content))
        header_tr_content = [m.group(0) for m in header_trs]
    else:
        # First data row serves as the header template
        if all_tr_matches:
            header_tr_content = [all_tr_matches[0].group(0)]
            data_tr_matches = all_tr_matches[1:]

    if not data_tr_matches:
        return [block]

    # ── Pre-compute each data <tr> position within block.content ──
    _tr_positions: list[int] = []
    for m in data_tr_matches:
        _tr_positions.append(m.start())

    # ── Group data rows into sub-blocks ──
    parts: list[MarkdownBlock] = []
    current_rows: list[str] = []
    current_tokens = _estimate_tokens(
        table_open + "\n" + "".join(header_tr_content) + table_close
    )
    _data_start_idx = 0

    for i, m in enumerate(data_tr_matches):
        row_text = m.group(0)
        row_tokens = _estimate_tokens(row_text)
        if current_rows and current_tokens + row_tokens > max_tokens:
            # Flush current group — rebuild a full HTML table
            part_inner = "".join(header_tr_content) + "\n" + "\n".join(current_rows)
            part_content = table_open + "\n" + part_inner + "\n" + table_close
            tbl_start = block.start_offset if _data_start_idx == 0 else (
                block.start_offset + _tr_positions[_data_start_idx]
            )
            parts.append(MarkdownBlock(
                block_type="html_table", content=part_content,
                start_offset=tbl_start,
                end_offset=block.end_offset,
                heading_path=list(block.heading_path),
            ))
            current_rows = []
            current_tokens = _estimate_tokens(
                table_open + "\n" + "".join(header_tr_content) + table_close
            )
            _data_start_idx = i

        current_rows.append(row_text)
        current_tokens += row_tokens

    if current_rows:
        part_inner = "".join(header_tr_content) + "\n" + "\n".join(current_rows)
        part_content = table_open + "\n" + part_inner + "\n" + table_close
        tbl_start = block.start_offset if _data_start_idx == 0 else (
            block.start_offset + _tr_positions[_data_start_idx]
        )
        parts.append(MarkdownBlock(
            block_type="html_table", content=part_content,
            start_offset=tbl_start,
            end_offset=block.end_offset,
            heading_path=list(block.heading_path),
        ))

    return parts if parts else [block]


def _split_block(block: MarkdownBlock, max_tokens: int) -> list[MarkdownBlock]:
    """Split an oversized block using type-appropriate strategy."""
    if block.block_type == "table":
        return _split_table_block(block, max_tokens)
    elif block.block_type == "html_table":
        return _split_html_table_block(block, max_tokens)
    elif block.block_type == "code":
        return _split_code_block(block, max_tokens)
    elif block.block_type == "list":
        return _split_list_block(block, max_tokens)
    elif block.block_type == "paragraph":
        return _split_paragraph_block(block, max_tokens)
    else:
        # blockquote, heading, hr — keep as-is
        return [block]


# ── MarkdownChunker ──────────────────────────────────────────

class MarkdownChunker:
    """Structure-aware chunker for Markdown documents.

    Parses the document into semantic blocks, then aggregates blocks into
    chunks respecting token budgets. Blocks are never split inappropriately.
    Each chunk includes heading_path metadata for context.
    """

    def __init__(self, max_tokens: int = 512, buffer_ratio: float = 0.5, chunk_overlap: int = 0):
        self.max_tokens = max(max_tokens, 1)
        self.buffer_ratio = max(buffer_ratio, 0.0)
        self.hard_limit = int(self.max_tokens * (1 + self.buffer_ratio))
        self.chunk_overlap = max(chunk_overlap, 0)

    def chunk(self, text: str) -> list[str]:
        """Chunk Markdown text, returning list of chunk strings."""
        blocks = _parse_blocks(text)
        if not blocks:
            return []

        chunks: list[str] = []
        current_parts: list[str] = []
        current_tokens = 0

        for block in blocks:
            block_text = block.content
            block_tokens = _estimate_tokens(block_text)

            # Oversized block — split it
            if block_tokens > self.hard_limit:
                if current_parts:
                    chunks.append("\n\n".join(current_parts))
                    current_parts = []
                    current_tokens = 0
                sub_blocks = _split_block(block, self.max_tokens)
                for sb in sub_blocks:
                    chunks.append(sb.content)
                continue

            # Adding this block would exceed max_tokens — flush
            if current_parts and current_tokens + block_tokens > self.max_tokens:
                chunks.append("\n\n".join(current_parts))
                current_parts = []
                current_tokens = 0

            current_parts.append(block_text)
            current_tokens += block_tokens

        if current_parts:
            chunks.append("\n\n".join(current_parts))

        return [c for c in chunks if c.strip()]

    def chunk_with_metadata(
        self, text: str, source: str = "", extra_metadata: dict | None = None
    ) -> list[Chunk]:
        """Chunk Markdown text with metadata, matching ParagraphChunker interface."""
        blocks = _parse_blocks(text)
        if not blocks:
            return []

        extra = {**(extra_metadata or {})}
        position_map = extra.pop("position_map", [])
        meta = {"source": source, **extra}

        # Build chunks from blocks
        raw_chunks: list[tuple[str, int, list[str]]] = []  # (text, offset, heading_path)
        current_parts: list[str] = []
        current_tokens = 0
        current_heading_path: list[str] = []
        chunk_start_offset = 0

        for block in blocks:
            block_text = block.content
            block_tokens = _estimate_tokens(block_text)

            # Oversized block — flush current and split
            if block_tokens > self.hard_limit:
                if current_parts:
                    raw_chunks.append((
                        "\n\n".join(current_parts),
                        chunk_start_offset,
                        list(current_heading_path),
                    ))
                    current_parts = []
                    current_tokens = 0
                sub_blocks = _split_block(block, self.max_tokens)
                for sb in sub_blocks:
                    raw_chunks.append((
                        sb.content,
                        sb.start_offset,
                        list(sb.heading_path),
                    ))
                continue

            # Adding this block would exceed max_tokens — flush
            if current_parts and current_tokens + block_tokens > self.max_tokens:
                raw_chunks.append((
                    "\n\n".join(current_parts),
                    chunk_start_offset,
                    list(current_heading_path),
                ))
                current_parts = []
                current_tokens = 0

            if not current_parts:
                chunk_start_offset = block.start_offset
                current_heading_path = list(block.heading_path)
            current_parts.append(block_text)
            current_tokens += block_tokens

        if current_parts:
            raw_chunks.append((
                "\n\n".join(current_parts),
                chunk_start_offset,
                list(current_heading_path),
            ))

        # Build Chunk objects
        raw_chunks = [(t, o, h) for t, o, h in raw_chunks if t.strip()]
        total = len(raw_chunks)
        chunks: list[Chunk] = []
        for i, (chunk_text, offset, heading_path) in enumerate(raw_chunks):
            chunk_meta = {
                **meta,
                "chunk_index": i,
                "total_chunks": total,
                "char_offset": offset,
            }
            if heading_path:
                chunk_meta["heading_path"] = " > ".join(heading_path)
            _annotate_position(chunk_meta, offset, position_map)
            chunks.append(Chunk(text=chunk_text, metadata=chunk_meta))

        return chunks


# ── MarkdownParentChildChunker ───────────────────────────────

class MarkdownParentChildChunker:
    """Markdown-aware parent-child chunker.

    Parents are complete sections (heading-based) or merged blocks (paragraph-based).
    Children are individual semantic blocks within each parent.
    """

    def __init__(
        self,
        parent_strategy: str = "heading",
        parent_chunk_size: int = 1024,
        parent_overlap: int = 128,
        parent_buffer_ratio: float = 0.5,
        child_chunk_size: int = 128,
        child_overlap: int = 32,
        child_buffer_ratio: float = 0.5,
    ):
        self.parent_strategy = parent_strategy
        self.parent_chunk_size = parent_chunk_size
        self.parent_overlap = parent_overlap
        self.parent_buffer_ratio = parent_buffer_ratio
        self.child_chunker = MarkdownChunker(
            max_tokens=child_chunk_size,
            buffer_ratio=child_buffer_ratio,
            chunk_overlap=child_overlap,
        )

    def _split_heading_sections(self, text: str, blocks: list[MarkdownBlock]) -> list[list[MarkdownBlock]]:
        """Split blocks into sections by top-level headings.

        Each section starts with a heading (level 1 or 2) and includes all
        blocks until the next same-or-higher-level heading.
        """
        sections: list[list[MarkdownBlock]] = []
        current: list[MarkdownBlock] = []
        top_level = 2  # split on ## and above

        for block in blocks:
            if block.block_type == "heading" and block.heading_level <= top_level:
                if current:
                    sections.append(current)
                current = [block]
            else:
                current.append(block)

        if current:
            sections.append(current)
        return sections

    def _merge_blocks_to_parents(
        self, blocks: list[MarkdownBlock]
    ) -> list[list[MarkdownBlock]]:
        """Merge consecutive blocks into parent-sized groups."""
        parents: list[list[MarkdownBlock]] = []
        current: list[MarkdownBlock] = []
        current_tokens = 0

        for block in blocks:
            block_tokens = _estimate_tokens(block.content)
            if current and current_tokens + block_tokens > self.parent_chunk_size:
                parents.append(current)
                current = []
                current_tokens = 0
            current.append(block)
            current_tokens += block_tokens

        if current:
            parents.append(current)
        return parents

    def chunk_with_metadata(
        self, text: str, source: str = "", extra_metadata: dict | None = None
    ) -> list[Chunk]:
        """Returns both parent and child chunks."""
        blocks = _parse_blocks(text)
        if not blocks:
            return []

        extra = {**(extra_metadata or {})}
        position_map = extra.pop("position_map", [])
        meta = {"source": source, **extra}

        # Split into parent groups based on strategy
        if self.parent_strategy == "heading":
            parent_groups = self._split_heading_sections(text, blocks)
        elif self.parent_strategy == "fixed_token":
            # For Markdown: treat each block as a unit, merge up to fixed token limit
            parent_groups = self._merge_blocks_to_parents(blocks)
        else:  # "paragraph" or unknown
            parent_groups = self._merge_blocks_to_parents(blocks)

        all_chunks: list[Chunk] = []

        for parent_idx, parent_blocks in enumerate(parent_groups):
            parent_id = str(uuid.uuid4())
            parent_text = "\n\n".join(b.content for b in parent_blocks)
            parent_offset = parent_blocks[0].start_offset if parent_blocks else 0
            heading_path = parent_blocks[0].heading_path if parent_blocks else []

            # Create parent chunk
            parent_meta = {
                **meta,
                "chunk_id": parent_id,
                "chunk_index": parent_idx,
                "char_offset": parent_offset,
            }
            if heading_path:
                parent_meta["heading_path"] = " > ".join(heading_path)
            _annotate_position(parent_meta, parent_offset, position_map)
            parent_chunk = Chunk(
                text=parent_text,
                metadata=parent_meta,
                parent_id=None,
                chunk_type="parent",
            )
            all_chunks.append(parent_chunk)

            # Create child chunks from individual blocks
            child_chunks = self.child_chunker.chunk_with_metadata(
                parent_text,
                source=source,
                extra_metadata={"file_type": extra.get("file_type", "markdown")},
            )
            for child_idx, child in enumerate(child_chunks):
                child.metadata["chunk_id"] = str(uuid.uuid4())
                child.metadata["chunk_index"] = child_idx
                child.parent_id = parent_id
                child.chunk_type = "child"
                # Adjust child's char_offset to be document-level (not parent-relative)
                child_co = child.metadata.get("char_offset")
                if child_co is not None:
                    child.metadata["char_offset"] = child_co + parent_offset
                # Propagate heading_path from parent
                if heading_path:
                    child.metadata["heading_path"] = " > ".join(heading_path)
                all_chunks.append(child)

        # Set total_chunks on parent chunks
        parent_count = len(parent_groups)
        for c in all_chunks:
            if c.chunk_type == "parent":
                c.metadata["total_chunks"] = parent_count

        return all_chunks
