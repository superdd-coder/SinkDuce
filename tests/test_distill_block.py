"""Unit tests for distill-block regex parsing and replacement."""

import pytest
from src.notes.service import parse_injection_blocks, replace_injection_block

NOTE_1 = "bf932c482e274051afdeec43bbcab784"


class TestParseInjectionBlocks:
    def test_empty_block(self):
        """Empty distill-block (no content between fences) should be parsed."""
        content = f"""一些文字

:::distill-block{{"id":"ref-1","source":"{NOTE_1}","source-title":"标题"}}
:::
"""
        blocks = parse_injection_blocks(content)
        assert len(blocks) == 1
        assert blocks[0]["block_id"] == "ref-1"
        assert blocks[0]["source_note_id"] == NOTE_1

    def test_nonempty_block(self):
        """Distill-block with content should be parsed."""
        content = f"""前文

:::distill-block{{"id":"ref-2","source":"{NOTE_1}"}}
已有蒸馏内容
:::
"""
        blocks = parse_injection_blocks(content)
        assert len(blocks) == 1
        assert blocks[0]["block_id"] == "ref-2"

    def test_multiple_blocks(self):
        """Multiple blocks, some empty, some not."""
        content = f"""开头

:::distill-block{{"id":"a","source":"{NOTE_1}"}}
已有内容
:::

中间文字

:::distill-block{{"id":"b","source":"note_999"}}
:::
"""
        blocks = parse_injection_blocks(content)
        assert len(blocks) == 2
        assert {b["block_id"] for b in blocks} == {"a", "b"}

    def test_no_block(self):
        blocks = parse_injection_blocks("普通文字，没有 block")
        assert blocks == []


class TestReplaceInjectionBlock:
    def test_replace_empty_block(self):
        """Replacing content into an empty distill-block."""
        content = f"""前文

:::distill-block{{"id":"ref-1","source":"{NOTE_1}","source-title":"标题"}}
:::
"""
        new_content = replace_injection_block(
            content, NOTE_1, "新蒸馏的## 内容", "标题",
        )
        assert "新蒸馏的## 内容" in new_content
        assert ':::distill-block{' in new_content
        assert ':::' in new_content

    def test_replace_nonempty_block(self):
        """Replacing existing content in a distill-block."""
        content = f"""开头

:::distill-block{{"id":"old","source":"{NOTE_1}"}}
旧内容
:::
"""
        new_content = replace_injection_block(
            content, NOTE_1, "更新的内容", "新标题",
        )
        assert "更新的内容" in new_content
        assert "旧内容" not in new_content

    def test_preserves_block_id(self):
        """Block ID should be preserved after replacement."""
        content = f':::distill-block{{"id":"my-block-123","source":"{NOTE_1}"}}\n:::'
        new_content = replace_injection_block(content, NOTE_1, "内容", "标题")
        assert '"id":"my-block-123"' in new_content or '"id": "my-block-123"' in new_content

    def test_no_matching_source(self):
        content = ":::distill-block{}\n:::\n"
        result = replace_injection_block(content, "non_existent_id", "x", "t")
        assert result == content  # unchanged
