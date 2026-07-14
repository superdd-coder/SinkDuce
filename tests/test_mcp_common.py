"""Unit tests for ``src.mcp.common`` shared utilities."""

from __future__ import annotations

import base64
import json

import pytest

from src.mcp.common import (
    DESTRUCTIVE_COLLECTION_FIELDS,
    decode_base64_content,
    err,
    filter_destructive_fields,
    ok,
    safe_filename,
    to_json,
)


# ── to_json ────────────────────────────────────────────────────


class TestToJson:
    def test_basic_types(self):
        assert json.loads(to_json({"a": 1, "b": "two"})) == {"a": 1, "b": "two"}

    def test_handles_datetime(self):
        from datetime import datetime
        dt = datetime(2025, 1, 1, 12, 0, 0)
        result = json.loads(to_json({"ts": dt}))
        assert result["ts"] == "2025-01-01 12:00:00"

    def test_handles_path(self):
        from pathlib import Path
        result = json.loads(to_json({"p": Path("/tmp/foo")}))
        assert "foo" in result["p"]


# ── ok / err ───────────────────────────────────────────────────


class TestOkErr:
    def test_ok_returns_dict(self):
        result = ok(message="hi", count=3)
        assert result == {"message": "hi", "count": 3}
        assert "error" not in result

    def test_err_includes_error_key(self):
        result = err("bad")
        assert result == {"error": "bad"}

    def test_err_can_include_extra(self):
        result = err("bad", code=400, fields=["x"])
        assert result == {"error": "bad", "code": 400, "fields": ["x"]}


# ── safe_filename ──────────────────────────────────────────────


class TestSafeFilename:
    def test_accepts_normal_name(self):
        assert safe_filename("report.pdf") == "report.pdf"

    def test_accepts_chinese(self):
        assert safe_filename("报告.pdf") == "报告.pdf"

    def test_accepts_spaces_and_parens(self):
        assert safe_filename("My Report (v2).md") == "My Report (v2).md"

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="must not be empty"):
            safe_filename("")

    def test_rejects_too_long(self):
        long_name = "a" * 300
        with pytest.raises(ValueError, match="too long"):
            safe_filename(long_name, max_len=255)

    def test_rejects_path_separator_forward(self):
        with pytest.raises(ValueError, match="Invalid filename"):
            safe_filename("../etc/passwd")

    def test_rejects_path_separator_back(self):
        with pytest.raises(ValueError, match="Invalid filename"):
            safe_filename("..\\windows\\system32")

    def test_rejects_nul_byte(self):
        with pytest.raises(ValueError, match="Invalid filename"):
            safe_filename("foo\x00bar")


# ── decode_base64_content ──────────────────────────────────────


class TestDecodeBase64Content:
    def test_basic_round_trip(self):
        data = b"hello world"
        b64 = base64.b64encode(data).decode("ascii")
        assert decode_base64_content(b64) == data

    def test_strips_data_url_prefix(self):
        data = b"binary\x00bytes"
        b64 = base64.b64encode(data).decode("ascii")
        prefixed = f"data:application/octet-stream;base64,{b64}"
        assert decode_base64_content(prefixed) == data

    def test_tolerates_whitespace(self):
        data = b"spaces in base64"
        b64 = base64.b64encode(data).decode("ascii")
        padded = "\n".join([b64[i:i+20] for i in range(0, len(b64), 20)])
        assert decode_base64_content(padded) == data

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="Empty content"):
            decode_base64_content("")

    def test_rejects_invalid_base64(self):
        with pytest.raises(ValueError, match="Invalid base64"):
            decode_base64_content("!!!not-base64!!!")


# ── filter_destructive_fields ──────────────────────────────────


class TestFilterDestructiveFields:
    def test_passes_through_safe_fields(self):
        safe, rejected = filter_destructive_fields({"chunk_size": 512})
        assert safe == {"chunk_size": 512}
        assert rejected == []

    def test_rejects_destructive_fields(self):
        dangerous = {"chunk_mode": "parent_child", "embedding_model": "foo"}
        safe, rejected = filter_destructive_fields(dangerous)
        assert safe == {}
        assert set(rejected) == {"chunk_mode", "embedding_model"}

    def test_keeps_safe_alongside_destructive(self):
        mixed = {"chunk_size": 512, "chunk_mode": "parent_child"}
        safe, rejected = filter_destructive_fields(mixed)
        assert safe == {"chunk_size": 512}
        assert rejected == ["chunk_mode"]

    def test_destructive_set_includes_expected_fields(self):
        expected = {
            "chunk_mode",
            "embedding_provider",
            "embedding_model",
            "embedding_dimensions",
            "embedding_base_url",
            "embedding_api_key",
            "embedding_batch_size",
        }
        assert expected.issubset(DESTRUCTIVE_COLLECTION_FIELDS)

    def test_embedding_provider_id_is_NOT_destructive(self):
        safe, rejected = filter_destructive_fields({"embedding_provider_id": "new-provider"})
        assert safe == {"embedding_provider_id": "new-provider"}
        assert rejected == []
