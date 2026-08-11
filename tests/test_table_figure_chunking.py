"""Table-source :::image must stay with the following table when chunking."""

from __future__ import annotations

from src.rag.markdown_chunker import (
    MarkdownChunker,
    _merge_table_source_images,
    _parse_blocks,
    _split_table_block,
    MarkdownBlock,
)


def _sample_doc() -> str:
    return (
        "Intro paragraph before the table.\n\n"
        ":::image\n"
        "image_id: aabbccddeeff00112233445566778899\n"
        "file_id: \n"
        ":::\n\n"
        "| Parameter | Unit |\n"
        "| --- | --- |\n"
        "| Flow | m3/d |\n"
        "| COD | ppm |\n\n"
        "Trailing paragraph after the table.\n"
    )


def test_merge_glues_image_fence_to_following_gfm_table():
    blocks = _parse_blocks(_sample_doc())
    types = [b.block_type for b in blocks]
    # image is no longer a standalone fenced_div before the table
    assert "table" in types
    table_blocks = [b for b in blocks if b.block_type == "table"]
    assert len(table_blocks) == 1
    assert ":::image" in table_blocks[0].content
    assert "| Parameter |" in table_blocks[0].content
    # image fence must not appear as its own block
    for b in blocks:
        if b.block_type == "fenced_div":
            assert not b.content.lstrip().startswith(":::image")


def test_chunker_never_splits_image_from_table():
    """Even with a tiny token budget, image and table share a chunk boundary."""
    doc = _sample_doc()
    # Small max_tokens forces frequent flushes between blocks
    chunker = MarkdownChunker(max_tokens=40, buffer_ratio=0.2)
    chunks = chunker.chunk(doc)
    assert chunks

    # Every chunk that contains the image fence must also contain the table header
    for c in chunks:
        if ":::image" in c:
            assert "| Parameter |" in c or "<table" in c.lower()
        # Conversely: if table header is present without figure, only allowed
        # when the figure lived in a prior oversized-split first piece —
        # for this small table, figure+table stay together always.
    with_img = [c for c in chunks if ":::image" in c]
    assert len(with_img) == 1
    assert "| Flow |" in with_img[0]


def test_oversized_table_split_keeps_figure_on_first_piece_only():
    rows = "\n".join(f"| r{i} | v{i} |" for i in range(40))
    content = (
        ":::image\n"
        "image_id: aabbccddeeff00112233445566778899\n"
        "file_id: \n"
        ":::\n\n"
        "| ColA | ColB |\n"
        "| --- | --- |\n"
        f"{rows}\n"
    )
    block = MarkdownBlock(
        block_type="table",
        content=content,
        start_offset=0,
        end_offset=len(content),
    )
    parts = _split_table_block(block, max_tokens=30)
    assert len(parts) >= 2
    assert ":::image" in parts[0].content
    assert "| ColA |" in parts[0].content
    for p in parts[1:]:
        assert ":::image" not in p.content
        assert "| ColA |" in p.content  # header repeated


def test_merge_html_table_source_image():
    text = (
        ":::image\n"
        "image_id: aabbccddeeff00112233445566778899\n"
        "file_id: \n"
        ":::\n"
        "<table><tr><td>a</td></tr></table>\n"
    )
    blocks = _parse_blocks(text)
    assert len([b for b in blocks if b.block_type == "html_table"]) == 1
    assert ":::image" in blocks[0].content
    assert "<table>" in blocks[0].content


def test_merge_idempotent_helper():
    blocks = _parse_blocks(_sample_doc())
    again = _merge_table_source_images(blocks)
    assert [b.content for b in again] == [b.content for b in blocks]
