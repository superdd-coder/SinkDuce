"""Unit tests for MCP file-management L1 tools (mocked service layer)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _unwrap(out):
    """Tools may return CallToolResult or plain dict."""
    sc = getattr(out, "structuredContent", None) or getattr(out, "structured_content", None)
    if sc is not None:
        return sc
    return out


class TestMcpServerAgentGuide:
    def test_server_has_instructions_and_tool_map(self):
        from src.mcp.server import mcp

        text = mcp.instructions or ""
        assert "list_library_tree" in text
        assert "get_timeline" in text
        assert "get_document_text" in text
        assert "file_id" in text
        assert "blob_available" in text or "blob" in text
        # Routing should prefer search for content, not default to tree browse
        assert "search_direct_chunks" in text or "search" in text.lower()
        assert "do not default" in text.lower() or "Route by goal" in text
        # FastMCP may expose description or only instructions depending on version
        desc = getattr(mcp, "description", None) or text
        assert "list_library_tree" in desc


class TestFileMgmtMcpTools:
    def test_list_folders_returns_dict(self):
        from src.mcp.tools import file_mgmt as mod

        folder = SimpleNamespace(
            model_dump=lambda: {
                "folder_id": "f1",
                "name": "Docs",
                "children": [],
                "file_count": 0,
            }
        )
        with patch("src.file_mgmt.views.require_fm_collection", return_value=None), \
             patch("src.file_mgmt.service.get_folder_tree", return_value=[folder]):
            out = _unwrap(_run(mod.list_folders("col_1")))
        assert isinstance(out, dict)
        assert "error" not in out
        assert out["collection"] == "col_1"
        assert out["folders"][0]["folder_id"] == "f1"

    def test_list_files_defaults_to_all_with_mounts(self):
        from src.mcp.tools import file_mgmt as mod

        files = [
            {
                "file_id": "a",
                "filename": "x.pdf",
                "doc_kind": "file",
                "mounts": [{"folder_id": "fld1", "folder_name": "Docs"}],
                "folder_ids": ["fld1"],
            }
        ]
        # 批次 3：信封/mounts/未分配过滤/聚合下沉进 service.list_files_lean
        #（REST ?lean=true 与工具共用），工具不再直调 list_files_with_mounts。
        envelope = {
            "collection": "col_1", "scope": "all", "folder_id": None,
            "files": files, "total": 1,
            "summary": {"unique_file_count": 1, "by_doc_kind": {"file": 1},
                        "files": 1, "notes": 0, "meetings": 0},
        }
        with patch("src.file_mgmt.views.require_fm_collection", return_value=None), \
             patch("src.file_mgmt.service.list_files_lean", return_value=envelope) as m:
            out = _unwrap(_run(mod.list_files("col_1")))
        assert out["total"] == 1
        assert out["scope"] == "all"
        assert out["files"][0]["folder_ids"] == ["fld1"]
        assert out["summary"]["files"] == 1
        m.assert_called_once()
        assert m.call_args.kwargs.get("scope") == "all"

    def test_list_library_tree(self):
        from src.mcp.tools import file_mgmt as mod

        tree = {
            "folders": [{"folder_id": "f1", "name": "Docs", "files": [], "children": []}],
            "orphans": [{"file_id": "o1", "filename": "root.md", "folder_ids": []}],
            "summary": {
                "folder_count": 1,
                "unique_file_count": 1,
                "orphan_count": 1,
                "by_doc_kind": {"file": 1},
                "files": 1,
                "notes": 0,
                "meetings": 0,
            },
        }
        with patch("src.file_mgmt.views.require_fm_collection", return_value=None), \
             patch("src.file_mgmt.service.build_library_tree", return_value=tree):
            out = _unwrap(_run(mod.list_library_tree("col_1")))
        assert out["summary"]["unique_file_count"] == 1
        assert out["orphans"][0]["file_id"] == "o1"

    def test_build_library_tree_max_depth_keeps_real_counts(self):
        """Truncated nodes keep real file_count; files=[] means payload omitted.

        Tree: root -> mid -> deep(测试23 with 1 file). max_depth=1 expands
        files only at root (depth 0). mid/deep stay truncated stubs with
        real counts so agents do not treat them as empty folders.
        """
        from src.file_mgmt import service as fm

        deep_leaf = SimpleNamespace(
            model_dump=lambda: {
                "folder_id": "deep",
                "name": "测试23",
                "file_count": 1,
            },
            children=[],
        )
        mid = SimpleNamespace(
            model_dump=lambda: {
                "folder_id": "mid",
                "name": "Mid",
                "file_count": 0,
            },
            children=[deep_leaf],
        )
        root = SimpleNamespace(
            model_dump=lambda: {
                "folder_id": "root",
                "name": "Root",
                "file_count": 0,
            },
            children=[mid],
        )
        deep_file = {
            "file_id": "doc1",
            "filename": "Investment_Memo.docx",
            "doc_kind": "file",
            "folder_ids": ["deep"],
            "mounts": [{"folder_id": "deep"}],
            "archived": False,
        }

        with patch("src.file_mgmt.store._migrate_files_json_import"), \
             patch("src.file_mgmt.files.get_folder_tree", return_value=[root]), \
             patch(
                 "src.file_mgmt.files.list_files_with_mounts",
                 side_effect=lambda *a, **kw: (
                     [deep_file] if kw.get("scope") == "all" else []
                 ),
             ):
            tree = fm.build_library_tree("col_1", max_depth=1)

        root_node = tree["folders"][0]
        assert root_node["truncated"] is False
        assert root_node["files"] == []  # no files mounted on root itself
        assert root_node["files_omitted"] == 0

        mid_node = root_node["children"][0]
        assert mid_node["folder_id"] == "mid"
        assert mid_node["truncated"] is True
        assert mid_node["files"] == []
        # mid has no direct mounts
        assert mid_node["file_count"] == 0
        assert mid_node["files_omitted"] == 0

        deep = mid_node["children"][0]
        assert deep["folder_id"] == "deep"
        assert deep["truncated"] is True
        assert deep["files"] == []
        assert deep["file_count"] == 1
        assert deep["unique_file_count"] == 1
        assert deep["files_omitted"] == 1


    def test_get_timeline(self):
        from src.mcp.tools import file_mgmt as mod

        data = {
            "timeline": {
                "chain_id": "main",
                "title": "Main",
                "is_main": True,
                "nodes": [
                    {
                        "node_id": "n1",
                        "title": "Kickoff",
                        "group_name": "Meeting",
                        "attachment_count": 1,
                        "child_branches": [],
                    }
                ],
                "branches": [],
            },
            "detached_branches": [
                {
                    "chain_id": "det1",
                    "title": "测试",
                    "detached": True,
                    "detach_reason": "parent_node_missing",
                    "nodes": [{"node_id": "x1"}],
                    "branches": [],
                }
            ],
            "chains": [{"chain_id": "main", "is_main": True, "title": "Main"}],
            "groups": [{"group_id": "g1", "name": "Meeting", "folder_id": None}],
            "depth": "summary",
            "node_order_rule": "ORDER BY order ASC, created_at ASC",
            "warnings": ["Detached branch"],
            "summary": {
                "chain_count": 2,
                "branch_count": 1,
                "detached_branch_count": 1,
                "node_count": 2,
                "group_count": 1,
            },
            "read_hint": "also read detached_branches",
        }
        with patch("src.file_mgmt.views.require_fm_collection", return_value=None), \
             patch("src.file_mgmt.service.build_timeline", return_value=data):
            out = _unwrap(_run(mod.get_timeline("col_1")))
        assert out["timeline"]["nodes"][0]["group_name"] == "Meeting"
        assert out["summary"]["node_count"] == 2
        assert out["detached_branches"][0]["detached"] is True

    def test_get_file_requires_id(self):
        from src.mcp.tools import file_mgmt as mod

        with patch("src.file_mgmt.views.require_fm_collection", return_value=None):
            out = _unwrap(_run(mod.get_file("col_1", "")))
        assert "error" in out

    def test_list_file_versions_includes_blob_available(self):
        from src.mcp.tools import file_mgmt as mod

        detail = SimpleNamespace(
            model_dump=lambda: {
                "file_id": "f1",
                "filename": "proposal.pdf",
                "display_name": "proposal.pdf",
                "current_version_id": "v8",
                "version": 7,
                "versions": [
                    {
                        "version_id": "v1",
                        "file_id": "f1",
                        "version_no": 1,
                        "storage_file_id": "old.docx",
                        "blob_available": True,
                    },
                    {
                        "version_id": "v5",
                        "file_id": "f1",
                        "version_no": 5,
                        "storage_file_id": "proposal.pdf",
                        "blob_available": False,
                    },
                    {
                        "version_id": "v8",
                        "file_id": "f1",
                        "version_no": 8,
                        "storage_file_id": "proposal.pdf",
                        "blob_available": True,
                    },
                ],
            }
        )
        with patch("src.file_mgmt.views.require_fm_collection", return_value=None), \
             patch("src.file_mgmt.service.get_file_detail", return_value=detail):
            out = _unwrap(_run(mod.list_file_versions("col_1", "f1")))

        assert out["version_count"] == 3
        assert out["gaps"] == [2, 3, 4, 6, 7]
        assert out["current_version_no"] == 8
        assert out["versions"][1]["blob_available"] is False
        assert out["versions"][2]["blob_available"] is True
        assert out["summary"]["blob_available_count"] == 2
        assert out["summary"]["blob_missing_count"] == 1
        assert "blob_available" in out["notes"]

    def test_get_chain_with_nodes(self):
        from src.mcp.tools import file_mgmt as mod

        data = {
            "timeline": {
                "chain_id": "c1",
                "title": "Main",
                "nodes": [{"node_id": "n1", "title": "Kickoff", "group_name": "G"}],
                "branches": [],
            },
            "detached_branches": [],
            "chains": [{"chain_id": "c1", "title": "Main"}],
            "warnings": [],
            "node_order_rule": "ORDER BY order ASC, created_at ASC",
        }
        with patch("src.file_mgmt.views.require_fm_collection", return_value=None), \
             patch("src.file_mgmt.service.build_timeline", return_value=data):
            out = _unwrap(_run(mod.get_chain("col_1", "c1")))
        assert out["node_count"] == 1
        assert out["nodes"][0]["group_name"] == "G"

    def test_set_file_definitive_uses_version(self):
        from src.mcp.tools import file_mgmt as mod

        detail = SimpleNamespace(version=3)
        updated = SimpleNamespace(model_dump=lambda: {"file_id": "f1", "is_definitive": True})
        with patch("src.file_mgmt.views.require_fm_collection", return_value=None), \
             patch("src.file_mgmt.service.get_file_detail", return_value=detail), \
             patch("src.file_mgmt.service.update_file", return_value=updated) as m:
            out = _unwrap(_run(mod.set_file_definitive("col_1", "f1", True)))
        assert out["is_definitive"] is True
        m.assert_called_once_with(
            "col_1", "f1", {"is_definitive": True, "version": 3}
        )

    def test_http_exception_mapped(self):
        from src.mcp.tools import file_mgmt as mod

        with patch("src.file_mgmt.views.require_fm_collection", return_value=None), \
             patch(
                 "src.file_mgmt.service.get_file_detail",
                 side_effect=HTTPException(404, "File 'x' not found"),
             ):
            out = _unwrap(_run(mod.get_file("col_1", "x")))
        assert "error" in out
        assert "not found" in out["error"].lower()

    def test_upload_rejects_missing_source(self):
        from src.mcp.tools import file_mgmt as mod

        out = _unwrap(_run(mod.upload_file_from_staging(collection="col_1")))
        assert "error" in out
        assert isinstance(out, dict)



def _tree_file(
    fid="doc1",
    name="memo.pdf",
    display=None,
    kind="file",
    definitive=False,
    archived=False,
    version_count=1,
    mounts=None,
):
    mounts = mounts or [{"folder_id": "root"}]
    return {
        "file_id": fid,
        "filename": name,
        "display_name": display or name,
        "doc_kind": kind,
        "is_definitive": definitive,
        "archived": archived,
        "current_version_no": version_count,
        "version_count": version_count,
        "lock_version": version_count,
        "folder_ids": [m["folder_id"] for m in mounts],
        "mounts": mounts,
    }


def _build_tree(files, fields="summary", folders=None):
    """Run build_library_tree against a mocked store (no db)."""
    from src.file_mgmt import service as fm

    nodes = folders or [
        SimpleNamespace(
            model_dump=lambda: {"folder_id": "root", "name": "Root", "file_count": 0},
            children=[],
        )
    ]
    with patch("src.file_mgmt.store._migrate_files_json_import"), \
         patch("src.file_mgmt.files.get_folder_tree", return_value=nodes), \
         patch(
             "src.file_mgmt.files.list_files_with_mounts",
             side_effect=lambda *a, **kw: files if kw.get("scope") == "all" else [],
         ):
        return fm.build_library_tree("col_1", fields=fields)


class TestLibraryTreePayloadShape:
    """Tree refs carry one name + one id per file; bookkeeping is gone."""

    def test_summary_ref_is_slim_and_files_index_gone(self):
        tree = _build_tree([_tree_file()])
        assert "files_index" not in tree
        refs = [r for f in tree["folders"] for r in f["files"]]
        assert refs == [
            {
                "file_id": "doc1",
                "name": "memo.pdf",
                "doc_kind": "file",
            }
        ]

    def test_sparse_flags_only_when_informative(self):
        tree = _build_tree(
            [
                _tree_file(fid="d1", definitive=True, version_count=3),
                _tree_file(fid="d2", archived=True),
                _tree_file(fid="d3"),
            ]
        )
        refs = {r["file_id"]: r for f in tree["folders"] for r in f["files"]}
        assert refs["d1"]["is_definitive"] is True
        assert refs["d1"]["version_count"] == 3
        assert refs["d2"]["archived"] is True
        for noisy in ("is_definitive", "archived", "version_count", "mount_count"):
            assert noisy not in refs["d3"]

    def test_multi_mount_file_under_both_folders(self):
        folders = [
            SimpleNamespace(
                model_dump=lambda i=i: {
                    "folder_id": f"fold{i}",
                    "name": f"F{i}",
                    "file_count": 0,
                },
                children=[],
            )
            for i in (0, 1)
        ]
        shared = _tree_file(
            fid="shared",
            display="Meeting: Kickoff",
            kind="meeting",
            mounts=[{"folder_id": "fold0"}, {"folder_id": "fold1"}],
        )
        tree = _build_tree([shared], folders=folders)
        assert "files_index" not in tree
        per_folder = {f["folder_id"]: f["files"] for f in tree["folders"]}
        assert [r["file_id"] for r in per_folder["fold0"]] == ["shared"]
        assert [r["file_id"] for r in per_folder["fold1"]] == ["shared"]
        ref = per_folder["fold0"][0]
        assert ref["name"] == "Meeting: Kickoff"  # display_name wins
        assert ref["mount_count"] == 2

    def test_minimal_mode_stays_slim(self):
        tree = _build_tree([_tree_file()], fields="minimal")
        assert "files_index" not in tree
        ref = tree["folders"][0]["files"][0]
        assert ref["name"] == "memo.pdf"
        assert "folder_ids" not in ref and "current_version_no" not in ref

    def test_full_mode_keeps_mounts(self):
        tree = _build_tree([_tree_file()], fields="full")
        ref = tree["folders"][0]["files"][0]
        assert ref["mounts"] == [{"folder_id": "root"}]
