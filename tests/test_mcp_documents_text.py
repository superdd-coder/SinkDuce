"""Unit tests for MCP get_document_text / get_file_chunks (file-mgmt path)."""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import patch

from fastapi import HTTPException


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _unwrap(out):
    sc = getattr(out, "structured_content", None)
    if sc is not None:
        return sc
    if isinstance(out, str):
        import json
        return json.loads(out)
    return out


class TestGetDocumentText:
    def test_file_id_uses_extracted_text_api(self):
        from src.mcp.tools import documents as mod

        with patch.object(mod, "require_collection", return_value=None), \
             patch(
                 "src.api.routes.documents.get_extracted_text",
                 return_value={"text": "hello world" * 100, "format": "text"},
             ) as m, \
             patch.object(mod, "_lookup_file_names", return_value=(
                 "Investment_Memo.docx", "投资备忘录"
             )):
            out = _unwrap(
                _run(mod.get_document_text("col_1", file_id="abc123", limit=20))
            )

        assert "error" not in out
        assert out["extract_status"] == "ok"
        assert out["file_id"] == "abc123"
        assert out["source"] == "__file__:abc123"
        assert out["filename"] == "Investment_Memo.docx"
        assert out["display_name"] == "投资备忘录"
        assert out["content"] == ("hello world" * 100)[:20]
        assert out.get("text") == out["content"]
        assert out["total_chars"] == 1100
        assert out["truncated"] is True
        assert out["has_more"] is True
        assert out["next_offset"] == 20
        assert out["returned_chars"] == 20
        assert out["offset"] == 0
        hint = out.get("hint") or ""
        assert "has_more=true" in hint
        assert "offset=20" in hint
        assert "char_offset" in hint
        # Encourage paging when the current window is insufficient
        assert "not enough" in hint.lower() or "page" in hint.lower()
        m.assert_called_once()
        assert m.call_args.kwargs.get("collection") == "col_1"
        assert m.call_args.kwargs.get("filename") == "__file__:abc123" or (
            m.call_args.args and m.call_args.args[0] == "__file__:abc123"
        )

    def test_source_file_alias(self):
        from src.mcp.tools import documents as mod

        with patch.object(mod, "require_collection", return_value=None), \
             patch(
                 "src.api.routes.documents.get_extracted_text",
                 return_value={"text": "body", "format": "markdown"},
             ) as m:
            out = _unwrap(
                _run(mod.get_document_text("col_1", source="file:xyz"))
            )

        assert out["file_id"] == "xyz"
        assert out["source"] == "__file__:xyz"
        assert out["content"] == "body"
        assert out["has_more"] is False
        assert out["next_offset"] is None
        assert out["truncated"] is False
        assert m.call_args.kwargs.get("filename") == "__file__:xyz" or (
            m.call_args.args and m.call_args.args[0] == "__file__:xyz"
        )

    def test_version_id_passed_through(self):
        from src.mcp.tools import documents as mod

        with patch.object(mod, "require_collection", return_value=None), \
             patch(
                 "src.api.routes.documents.get_extracted_text",
                 return_value={"text": "v2", "format": "text"},
             ) as m:
            out = _unwrap(
                _run(
                    mod.get_document_text(
                        "col_1", file_id="f1", version_id="ver_99"
                    )
                )
            )

        assert out["version_id"] == "ver_99"
        assert m.call_args.kwargs.get("version_id") == "ver_99"

    def test_blob_missing_structured_error(self):
        from src.mcp.tools import documents as mod

        with patch.object(mod, "require_collection", return_value=None), \
             patch(
                 "src.api.routes.documents.get_extracted_text",
                 side_effect=HTTPException(
                     status_code=404, detail="Version blob not found: ver_x"
                 ),
             ):
            raw = _run(
                mod.get_document_text("col_1", file_id="f1", version_id="ver_x")
            )
            out = _unwrap(raw)

        assert "error" in out
        assert out["extract_status"] == "blob_missing"
        assert out["file_id"] == "f1"
        assert "get_file_chunks" in (out.get("hint") or "")
        assert getattr(raw, "is_error", True) is True

    def test_missing_args(self):
        from src.mcp.tools import documents as mod

        with patch.object(mod, "require_collection", return_value=None):
            out = _unwrap(_run(mod.get_document_text("col_1")))
        assert out["extract_status"] == "missing_args"

    def test_signature_exposes_file_id_and_version_id(self):
        """Hermes / MCP tools/list must see these params in inputSchema."""
        from src.mcp.tools import documents as mod

        gdt = inspect.signature(mod.get_document_text)
        assert "file_id" in gdt.parameters
        assert "version_id" in gdt.parameters
        # Preferred kwargs appear before legacy source
        names = list(gdt.parameters)
        assert names.index("file_id") < names.index("source")

        gfc = inspect.signature(mod.get_file_chunks)
        assert "file_id" in gfc.parameters
        names_gfc = list(gfc.parameters)
        assert names_gfc.index("file_id") < names_gfc.index("source")


class TestGetFileChunks:
    def test_file_id_normalizes_source(self):
        from src.mcp.tools import documents as mod

        fake_services = type("S", (), {})()
        fake_db = type("DB", (), {})()
        calls = {"n": 0}

        def scroll_points(**kwargs):
            calls["n"] += 1
            filt = kwargs.get("scroll_filter")
            # first call only
            assert filt is not None
            return (
                [
                    {
                        "payload": {
                            "text": "chunk-a",
                            "chunk_type": "normal",
                            "chunk_index": 0,
                            "char_offset": 0,
                            "source": "__file__:fid1",
                        }
                    }
                ],
                None,
            )

        fake_db.scroll_points = scroll_points
        fake_services.db = fake_db

        with patch.object(mod, "require_collection", return_value=None), \
             patch("src.services.services", fake_services):
            out = _unwrap(
                _run(mod.get_file_chunks("col_1", file_id="fid1", limit=10))
            )

        assert out["source"] == "__file__:fid1"
        assert out["file_id"] == "fid1"
        assert out["total"] == 1
        assert out["chunks"][0]["text"] == "chunk-a"
        # structuredContent path
        raw = None
        with patch.object(mod, "require_collection", return_value=None), \
             patch("src.services.services", fake_services):
            raw = _run(mod.get_file_chunks("col_1", file_id="fid1"))
        assert getattr(raw, "structured_content", None) is not None
