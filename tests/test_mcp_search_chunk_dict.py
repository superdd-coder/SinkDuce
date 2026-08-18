"""MCP search hit shape: file_id + filename alongside canonical source."""

from __future__ import annotations

from types import SimpleNamespace

from src.mcp.tools.search import _chunk_to_dict, _file_id_from_source


def test_file_id_from_canonical_source():
    assert _file_id_from_source("__file__:abc123") == "abc123"
    assert _file_id_from_source("file:abc123") == "abc123"
    assert _file_id_from_source("report.pdf") == ""


def test_chunk_to_dict_includes_file_id_and_payload_filename():
    chunk = SimpleNamespace(
        text="hello",
        score=0.9,
        metadata={
            "source": "__file__:deadbeefdeadbeefdeadbeefdeadbeef",
            "source_label": "季度报告.pdf",
            "collection": "",
            "chunk_type": "normal",
            "id": "pt-1",
            "images": [],
        },
    )
    out = _chunk_to_dict(chunk)
    assert out["source"] == "__file__:deadbeefdeadbeefdeadbeefdeadbeef"
    assert out["file_id"] == "deadbeefdeadbeefdeadbeefdeadbeef"
    assert out["filename"] == "季度报告.pdf"


def test_chunk_to_dict_uses_metadata_file_id():
    chunk = SimpleNamespace(
        text="x",
        score=0.1,
        metadata={
            "source": "legacy-name.docx",
            "file_id": "fid001",
            "source_label": "legacy-name.docx",
            "collection": "",
        },
    )
    out = _chunk_to_dict(chunk)
    assert out["file_id"] == "fid001"
    assert out["filename"] == "legacy-name.docx"


def test_chunk_to_dict_does_not_echo_opaque_source_as_filename():
    chunk = SimpleNamespace(
        text="x",
        score=0.2,
        metadata={
            "source": "__file__:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "collection": "",
        },
    )
    out = _chunk_to_dict(chunk)
    assert out["file_id"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert out["filename"] == ""
