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


def test_image_fence_always_counts_as_160():
    from src.rag.chunker import IMAGE_DESC_RESERVE_TOKENS, estimate_chunking_tokens

    fence = (
        ":::image\n"
        "image_id: aabbccddeeff00112233445566778899\n"
        "file_id: \n"
        ":::"
    )
    described = (
        ":::image\n"
        "image_id: aabbccddeeff00112233445566778899\n"
        "file_id: \n"
        "description: A process flow of the pretreatment unit\n"
        "ocr_text: Q3 42M\n"
        ":::"
    )
    assert estimate_chunking_tokens(fence) == IMAGE_DESC_RESERVE_TOKENS
    assert estimate_chunking_tokens(described) == IMAGE_DESC_RESERVE_TOKENS
    assert estimate_chunking_tokens("前言\n\n" + fence) == (
        estimate_chunking_tokens("前言\n\n") + IMAGE_DESC_RESERVE_TOKENS
    )

    para = "字" * 650
    chunker = MarkdownChunker(max_tokens=512, buffer_ratio=0.5)
    chunks = chunker.chunk(para + "\n\n" + fence)
    assert len(chunks) >= 2
    image_chunks = [c for c in chunks if ":::image" in c]
    assert image_chunks
    # 650 CJK + 160 figure > 768 hard limit, so the figure starts the next chunk
    assert "字" * 50 not in image_chunks[0]


def test_buffer_allows_next_paragraph_up_to_hard_limit():
    """~380 + ~146 = ~526 is over 512 but under 768 — keep one chunk."""
    from src.rag.chunker import _estimate_tokens
    first = "word " * 152
    second = "next " * 58
    total = _estimate_tokens(first) + _estimate_tokens(second)
    assert 512 < total < 768
    chunks = MarkdownChunker(max_tokens=512, buffer_ratio=0.5).chunk(first.strip() + "\n\n" + second.strip())
    assert len(chunks) == 1


def test_buffer_seals_after_one_overflow_even_if_room_remains():
    """After one overflow past 512, do not pack more even if still under 768."""
    from src.rag.chunker import _estimate_tokens
    first = "word " * 152
    second = "next " * 58
    third = "tail " * 40
    two = _estimate_tokens(first) + _estimate_tokens(second)
    three = two + _estimate_tokens(third)
    assert two > 512
    assert three < 768
    text = "\n\n".join([first.strip(), second.strip(), third.strip()])
    chunks = MarkdownChunker(max_tokens=512, buffer_ratio=0.5).chunk(text)
    assert len(chunks) == 2
    assert "tail" not in chunks[0]
    assert "tail" in chunks[1]


def test_independent_image_packs_with_neighbors_when_budget_allows():
    fence = (
        ":::image\n"
        "image_id: aabbccddeeff00112233445566778899\n"
        "file_id: \n"
        ":::\n"
    )
    text = "前言短句。\n\n" + fence + "\n后记也短。\n"
    chunks = MarkdownChunker(max_tokens=512, buffer_ratio=0.5).chunk(text)
    assert len(chunks) == 1
    assert "前言短句" in chunks[0]
    assert ":::image" in chunks[0]
    assert "后记也短" in chunks[0]


def test_independent_image_starts_next_chunk_when_current_is_full():
    fence = (
        ":::image\n"
        "image_id: aabbccddeeff00112233445566778899\n"
        "file_id: \n"
        ":::\n"
    )
    # 650 CJK leaves 118 of a 768 hard limit — not enough for a 160 figure
    text = ("字" * 650) + "\n\n" + fence + "\n尾句。\n"
    chunks = MarkdownChunker(max_tokens=512, buffer_ratio=0.5).chunk(text)
    assert len(chunks) == 2
    assert ":::image" not in chunks[0]
    assert ":::image" in chunks[1]
    assert "尾句" in chunks[1]


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


def test_oversized_table_split_copies_figure_onto_every_piece():
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
    for p in parts:
        assert ":::image" in p.content
        assert "aabbccddeeff00112233445566778899" in p.content
        assert "| ColA |" in p.content  # header repeated


def test_table_source_fence_does_not_count_as_160():
    from src.rag.chunker import estimate_chunking_tokens, strip_table_source_fences

    table = "| ColA | ColB |\n| --- | --- |\n| a | b |\n"
    fence = (
        ":::image\n"
        "image_id: aabbccddeeff00112233445566778899\n"
        "file_id: \n"
        ":::\n\n"
    )
    glued = fence + table
    assert estimate_chunking_tokens(glued) == estimate_chunking_tokens(table)
    stripped = strip_table_source_fences(glued)
    assert ":::image" not in stripped
    assert "| ColA |" in stripped


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
