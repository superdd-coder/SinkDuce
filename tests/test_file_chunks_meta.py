from src.api.routes.documents import serialize_chunk_point


def test_serialize_chunk_point_includes_sheet_and_skips_internals():
    out = serialize_chunk_point(
        "pt-1",
        {
            "text": "row body",
            "chunk_index": 2,
            "sheet_name": "Opex",
            "context": "Cost sheet for the plant.",
            "summary": "Workbook about opex.",
            "file_id": "fid-9",
            "_embed_idx": 2,
            "_structured_summary": "===DATA===",
        },
    )
    assert out["id"] == "pt-1"
    assert out["text"] == "row body"
    assert out["sheet_name"] == "Opex"
    assert out["context"] == "Cost sheet for the plant."
    assert out["file_id"] == "fid-9"
    assert "_embed_idx" not in out
    assert "_structured_summary" not in out


def test_serialize_chunk_point_passthrough_unknown_fields():
    out = serialize_chunk_point("pt-2", {"text": "x", "custom_tag": "keep-me", "chunk_index": 0})
    assert out["custom_tag"] == "keep-me"
    assert out["chunk_index"] == 0
