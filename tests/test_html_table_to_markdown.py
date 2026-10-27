from src.rag.html_tables import html_table_to_markdown
from src.rag.markdown_chunker import MarkdownChunker, _html_tables_to_gfm, _parse_blocks
from src.rag.chunker import _estimate_tokens


def test_html_table_to_markdown_strips_tags():
    html = """
    <table>
      <tr><th><p>Item</p></th><th><p>IRR</p></th></tr>
      <tr><td><p>140</p></td><td><p>0.22</p></td></tr>
    </table>
    """
    md = html_table_to_markdown(html)
    assert "<th>" not in md
    assert "<p>" not in md
    assert md.splitlines()[0] == "| Item | IRR |"
    assert "| --- | --- |" in md
    assert "| 140 | 0.22 |" in md


def test_html_table_repeats_colspan_and_unescapes():
    html = (
        "<table><tr>"
        '<th colspan="2">SG&amp;A</th><th>Year</th>'
        "</tr><tr><td>a</td><td>b</td><td>2027</td></tr></table>"
    )
    md = html_table_to_markdown(html)
    assert md.splitlines()[0] == "| SG&A | SG&A | Year |"
    assert "| a | b | 2027 |" in md


def test_html_table_conversion_failure_keeps_html():
    assert html_table_to_markdown("<table></table>").strip().startswith("<table")


def test_parse_blocks_still_keeps_html_until_chunk_normalize():
    text = "<table><tr><td>a</td></tr></table>\n"
    blocks = _parse_blocks(text)
    assert blocks[0].block_type == "html_table"
    converted = _html_tables_to_gfm(blocks)
    assert converted[0].block_type == "table"
    assert "<table>" not in converted[0].content
    assert "| a |" in converted[0].content


def test_chunker_stores_gfm_not_html_and_prunes_empty_columns():
    rows = ["<tr>" + "".join(f"<td><p></p></td>" for _ in range(8)) + "<td><p>x{i}</p></td></tr>".replace("{i}", str(i)) for i in range(6)]
    header = "<tr>" + "".join("<th><p></p></th>" for _ in range(8)) + "<th><p>Value</p></th></tr>"
    html = "<table>" + header + "".join(rows) + "</table>"
    chunks = MarkdownChunker(max_tokens=512, buffer_ratio=0.5).chunk(html)
    assert chunks
    joined = "\n".join(chunks)
    assert "<th>" not in joined
    assert "<p>" not in joined
    assert "| Value |" in joined
    # 8 empty columns must not survive into the stored chunk
    assert joined.splitlines()[0].count("|") == 2  # '| Value |'


def test_sparse_html_table_packs_more_rows_than_raw_html():
    """Empty <th><p> cells used to blow the 512 budget to ~1 row/chunk."""
    header_cells = "".join(f"<th><p></p></th>" for _ in range(20))
    header = f"<tr><th><p>Name</p></th>{header_cells}<th><p>N</p></th></tr>"
    rows = []
    for i in range(30):
        empties = "".join("<td><p></p></td>" for _ in range(20))
        rows.append(f"<tr><td><p>row{i}</p></td>{empties}<td><p>{i}</p></td></tr>")
    html = "<table>" + header + "".join(rows) + "</table>"
    raw_tok = _estimate_tokens(html)
    chunks = MarkdownChunker(max_tokens=512, buffer_ratio=0.5).chunk(html)
    assert raw_tok > 2000
    assert 1 <= len(chunks) <= 8
    assert all("<table" not in c for c in chunks)
    assert "row0" in chunks[0]
    assert "row29" in chunks[-1]
