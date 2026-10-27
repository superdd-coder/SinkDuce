from src.parsers.mineru_parser import MinerUParser
from src.rag.chunker import ParagraphChunker, chunk_with_sheet_boundaries, iter_sheet_segments
from src.tasks.handlers import _build_enriched_text, _is_tabular_document
from src.rag.chunker import Chunk


def test_two_small_sheets_do_not_share_a_chunk():
    sheet_a = "## Costs\n| item | amt |\n| --- | --- |\n| a | 1 |"
    sheet_b = "## Revenue\n| item | amt |\n| --- | --- |\n| b | 2 |"
    text = sheet_a + "\n\n" + sheet_b
    position_map = [
        {"char_offset": 0, "sheet_name": "Costs", "label": "Sheet: Costs"},
        {"char_offset": len(sheet_a) + 2, "sheet_name": "Revenue", "label": "Sheet: Revenue"},
    ]
    chunker = ParagraphChunker(max_tokens=2000, buffer_ratio=0.5, chunk_overlap=0)
    # Without the wrapper these two tiny sheets fit in one chunk.
    merged = chunker.chunk_with_metadata(text, source="book.xlsx", extra_metadata={
        "position_map": list(position_map),
        "file_type": "excel",
    })
    assert len(merged) == 1

    split = chunk_with_sheet_boundaries(
        chunker,
        text,
        source="book.xlsx",
        extra_metadata={"position_map": position_map, "file_type": "excel"},
    )
    assert len(split) == 2
    assert split[0].metadata["sheet_name"] == "Costs"
    assert split[1].metadata["sheet_name"] == "Revenue"
    assert "Revenue" not in split[0].text
    assert "Costs" not in split[1].text


def test_build_enriched_text_adds_one_sheet_line():
    chunk = Chunk(
        text="| a | 1 |",
        metadata={"source": "book.xlsx", "sheet_name": "Costs", "label": "Sheet: Costs"},
    )
    text = _build_enriched_text(chunk)
    assert text.count("Sheet: Costs") == 1
    assert text.startswith("Source: book.xlsx\nSheet: Costs\n")


def test_build_enriched_text_omits_document_summary():
    chunk = Chunk(
        text="body",
        metadata={
            "source": "a.xlsx",
            "summary": "A workbook about plant opex.",
            "context": "Opex table for the plant.",
        },
    )
    text = _build_enriched_text(chunk)
    assert "Document:" not in text
    assert "A workbook about plant opex." not in text
    assert "Context: Opex table for the plant." in text
    assert text.endswith("body")


def test_build_enriched_text_uses_source_label_not_file_id():
    chunk = Chunk(
        text="body",
        metadata={
            "source": "__file__:abc123",
            "source_label": "技术说明.xlsx",
        },
    )
    text = _build_enriched_text(chunk)
    assert text.startswith("Source: 技术说明.xlsx\n")
    assert "abc123" not in text
    assert "__file__" not in text


def test_is_tabular_document_local_and_mineru():
    local = type("Doc", (), {"file_type": "excel", "metadata": {}})()
    csv_doc = type("Doc", (), {"file_type": "csv", "metadata": {}})()
    mineru = type("Doc", (), {
        "file_type": "markdown",
        "metadata": {"original_file_type": "xlsx"},
    })()
    note = type("Doc", (), {"file_type": "note", "metadata": {}})()
    dummy = type("Doc", (), {"content": "x"})()
    assert _is_tabular_document(local)
    assert _is_tabular_document(csv_doc)
    assert _is_tabular_document(mineru)
    assert not _is_tabular_document(note)
    assert not _is_tabular_document(dummy)


def test_iter_sheet_segments_attaches_prefix_to_first_sheet():
    text = "intro\n\n## A\nbody\n\n## B\nmore"
    segs = iter_sheet_segments(text, [
        {"char_offset": text.index("## A"), "sheet_name": "A"},
        {"char_offset": text.index("## B"), "sheet_name": "B"},
    ])
    assert segs[0][0] == 0
    assert segs[0][2] == "A"
    assert segs[1][2] == "B"


def _title_block(text: str) -> dict:
    return {
        "type": "title",
        "lines": [{"spans": [{"content": text}]}],
    }


def test_mineru_excel_first_title_becomes_sheet_name():
    markdown = "# 技术说明\n\ntable a\n\n# Opex\n\ntable b\n"
    layout = {
        "pdf_info": [
            {
                "page_idx": 0,
                "para_blocks": [
                    _title_block("技术说明"),
                    {"type": "table", "lines": [{"spans": [{"content": "table a"}]}]},
                ],
            },
            {
                "page_idx": 1,
                "para_blocks": [
                    _title_block("Opex"),
                    {"type": "table", "lines": [{"spans": [{"content": "table b"}]}]},
                ],
            },
        ]
    }
    parser = MinerUParser(api_token="test")
    pdf_map = parser._build_position_map(layout, markdown, original_file_type="pdf")
    assert all("sheet_name" not in e for e in pdf_map)

    excel_map = parser._build_position_map(layout, markdown, original_file_type="xlsx")
    assert [e["sheet_name"] for e in excel_map] == ["技术说明", "Opex"]
    assert excel_map[0]["char_offset"] == markdown.index("技术说明")
    assert excel_map[1]["char_offset"] == markdown.index("Opex")
    assert excel_map[0]["label"] == "Sheet: 技术说明"


def test_mineru_excel_heading_fallback_without_layout():
    markdown = "## Costs\n\nrows\n\n## Revenue\n\nrows"
    parser = MinerUParser(api_token="test")
    mapped = parser._build_position_map({}, markdown, original_file_type="xlsx")
    assert [e["sheet_name"] for e in mapped] == ["Costs", "Revenue"]
