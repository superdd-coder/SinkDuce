"""file-mgmt 的 **Agent 视图函数**（批次 4：REST 新端点与 MCP 工具共用）。

每个函数返回**工具承诺的信封 dict**（成功或 ``{"error": …}``——沿用 collections
create/delete 的 200-with-error 约定），两侧共用一份，形状永不漂移。
``_require_fm_collection`` / ``_parse_bool`` 从 MCP 工具模块迁到这里（工具侧
改为反向 import——service 层不该依赖 mcp 包）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from src.mcp.common import err, ok, require_collection

logger = logging.getLogger("file_mgmt.views")



def _http_err(exc: HTTPException) -> dict[str, Any]:
    """HTTPException → 工具的 err dict（与 MCP 侧 _http_err 同语义；REST 端点
    200-with-error 约定——错误形状与工具侧逐字段一致）。"""
    detail = exc.detail
    if isinstance(detail, dict):
        return err(
            str(detail.get("message") or detail),
            **{k: v for k, v in detail.items() if k != "message"},
        )
    return err(str(detail), status_code=exc.status_code)


def _dump(obj: Any) -> Any:
    """Recursively convert pydantic models / Row objects to plain structures."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, dict):
        return {k: _dump(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_dump(v) for v in obj]
    return obj


def require_fm_collection(collection: str) -> dict[str, Any] | None:
    """Collection must exist and have file-mgmt DB initialized（原 MCP 工具侧
    的 ``_require_fm_collection``，迁到 service 层供两侧共用）。"""
    if e := require_collection(collection):
        return e
    try:
        from src.file_mgmt.store import get_db

        conn = get_db(collection)
        conn.close()
    except Exception as exc:
        return err(
            f"File-management DB not available for collection '{collection}': {exc}"
        )
    return None


def parse_bool(s: str) -> bool | None:
    v = (s or "").strip().lower()
    if not v:
        return None
    if v in ("true", "1", "yes"):
        return True
    if v in ("false", "0", "no"):
        return False
    return None


def _unassigned_gate(collection: str, file_id: str) -> dict[str, Any] | None:
    """v2 R6: Unassigned-only files are invisible to AI（读类工具的公共闸门）。"""
    from src.file_mgmt.service import unassigned_only_file_ids

    if file_id in unassigned_only_file_ids(collection, [file_id]):
        return err(
            "File not available — it is in the Unassigned folder",
            extract_status="unassigned",
        )
    return None


def library_tree_view(
    collection: str,
    max_depth: int = 0,
    include_archived_files: bool = True,
    fields: str = "summary",
) -> dict[str, Any]:
    from src.file_mgmt import service as fm

    if e := require_fm_collection(collection):
        return e
    depth = None if not max_depth or max_depth <= 0 else int(max_depth)
    fld = (fields or "summary").strip().lower() or "summary"
    tree = fm.build_library_tree(
        collection,
        max_depth=depth,
        include_archived_files=bool(include_archived_files),
        fields=fld,
    )
    return ok(collection=collection, **tree)


def timeline_view(collection: str, depth: str = "summary") -> dict[str, Any]:
    from src.file_mgmt import service as fm

    if e := require_fm_collection(collection):
        return e
    d = (depth or "summary").strip().lower() or "summary"
    data = fm.build_timeline(collection, depth=d)
    return ok(collection=collection, **data)


def chain_view(collection: str, chain_id: str) -> dict[str, Any]:
    """单链视图（get_chain 工具的信封：从完整 timeline 树里挑出目标链）。"""
    from src.file_mgmt import service as fm

    if e := require_fm_collection(collection):
        return e
    cid = (chain_id or "").strip()
    if not cid:
        return err("chain_id is required")
    data = fm.build_timeline(collection, depth="summary")
    flat = {c["chain_id"]: c for c in (data.get("chains") or [])}

    def find_nested(node_chain: dict | None, target: str) -> dict | None:
        if not node_chain:
            return None
        if node_chain.get("chain_id") == target:
            return node_chain
        for br in node_chain.get("branches") or []:
            hit = find_nested(br, target)
            if hit:
                return hit
        return None

    nested = find_nested(data.get("timeline"), cid)
    if nested is None and cid in flat:
        return ok(
            collection=collection,
            chain=flat[cid],
            nodes=[],
            node_count=0,
            warnings=data.get("warnings") or [],
            node_order_rule=data.get("node_order_rule"),
        )
    if nested is None:
        return err(f"Chain '{cid}' not found", status_code=404)
    nodes = nested.get("nodes") or []
    return ok(
        collection=collection,
        chain={k: v for k, v in nested.items() if k not in ("nodes", "branches")},
        nodes=nodes,
        node_count=len(nodes),
        branches=nested.get("branches") or [],
        warnings=data.get("warnings") or [],
        node_order_rule=data.get("node_order_rule"),
    )


def file_versions_view(collection: str, file_id: str) -> dict[str, Any]:
    from src.file_mgmt import service as fm

    # 校验顺序与原工具逐一对齐（顺序决定「坏库+空id」时先报哪个错）：
    # 未分配闸门 → fm 库校验 → 空 id 校验。
    if e := _unassigned_gate(collection, (file_id or "").strip()):
        return e
    if e := require_fm_collection(collection):
        return e
    fid = (file_id or "").strip()
    if not fid:
        return err("file_id is required")
    try:
        detail = fm.get_file_detail(collection, fid)
    except HTTPException as exc:
        return _http_err(exc)
    data = _dump(detail)
    versions = data.get("versions") or []
    nos = sorted(
        int(v["version_no"]) for v in versions if v.get("version_no") is not None
    )
    gaps: list[int] = []
    if nos:
        for n in range(nos[0], nos[-1] + 1):
            if n not in nos:
                gaps.append(n)
    cur_id = data.get("current_version_id")
    cur_no = None
    for v in versions:
        if v.get("version_id") == cur_id:
            cur_no = v.get("version_no")
            break
    blob_ok = sum(1 for v in versions if v.get("blob_available"))
    blob_missing = len(versions) - blob_ok
    return ok(
        collection=collection,
        file_id=fid,
        filename=data.get("filename"),
        display_name=data.get("display_name"),
        current_version_id=cur_id,
        current_version_no=cur_no,
        version_count=len(versions),
        optimistic_lock_version=data.get("version"),
        versions=versions,
        gaps=gaps,
        summary={
            "version_count": len(versions),
            "blob_available_count": blob_ok,
            "blob_missing_count": blob_missing,
            "gaps": gaps,
        },
        notes={
            "storage_file_id": "basename/filename stored for that version blob",
            "gaps": "version_no values missing in [min,max] — deleted or never created",
            "optimistic_lock_version": "files.version concurrency field, not version_no",
            "blob_available": (
                "per-version on-disk blob present; false → "
                "get_document_text(version_id=…) returns blob_missing; "
                "chunks only reflect the currently indexed version"
            ),
        },
    )


def set_file_definitive_view(
    collection: str, file_id: str, is_definitive: bool = True
) -> dict[str, Any]:
    from src.file_mgmt import service as fm

    if e := require_fm_collection(collection):
        return e
    fid = (file_id or "").strip()
    if not fid:
        return err("file_id is required")
    try:
        detail = fm.get_file_detail(collection, fid)
        result = fm.update_file(
            collection,
            fid,
            {
                "is_definitive": bool(is_definitive),
                "version": int(detail.version),
            },
        )
    except HTTPException as exc:
        return _http_err(exc)
    return ok(
        collection=collection,
        file=_dump(result),
        is_definitive=bool(is_definitive),
    )


__all__ = [
    "chain_view",
    "file_versions_view",
    "library_tree_view",
    "parse_bool",
    "require_fm_collection",
    "set_file_definitive_view",
    "timeline_view",
]


def file_detail_view(collection: str, file_id: str) -> dict[str, Any]:
    """`get_file` 工具的信封：未分配闸门 + 完整 FileDetail（paths/nodes/messages/versions）。"""
    from src.file_mgmt import service as fm

    if (file_id or "").strip() and file_id in fm.unassigned_only_file_ids(
        collection, [(file_id or "").strip()]
    ):
        return err(
            "File not available — it is in the Unassigned folder",
            extract_status="unassigned",
        )
    if e := require_fm_collection(collection):
        return e
    if not (file_id or "").strip():
        return err("file_id is required")
    try:
        detail = fm.get_file_detail(collection, file_id.strip())
    except HTTPException as exc:
        return _http_err(exc)
    except Exception as exc:
        # 与工具侧 except 同语义（REST 200-with-error 约定）——否则内部错误
        # 在 REST 变 500、转发成 hub_call_failed，与本地 err 形状分叉（审查 #4）
        logger.exception("file_detail_view failed")
        return err(str(exc))
    return ok(collection=collection, file=_dump(detail))


def node_view(collection: str, node_id: str) -> dict[str, Any]:
    """`get_node` 工具的信封。"""
    from src.file_mgmt import service as fm

    if e := require_fm_collection(collection):
        return e
    nid = (node_id or "").strip()
    if not nid:
        return err("node_id is required")
    try:
        detail = fm.get_node_detail(collection, nid)
    except HTTPException as exc:
        return _http_err(exc)
    except Exception as exc:
        logger.exception("node_view failed")
        return err(str(exc))
    return ok(collection=collection, node=_dump(detail))


def chains_view(collection: str) -> dict[str, Any]:
    """`list_chains` 工具的信封（链骨架 + Main 标题归一）。"""
    from src.file_mgmt import service as fm

    if e := require_fm_collection(collection):
        return e
    try:
        chains = fm.list_chains(collection)
    except HTTPException as exc:
        return _http_err(exc)
    except Exception as exc:
        logger.exception("chains_view failed")
        return err(str(exc))
    dumped = _dump(chains)
    for c in dumped:
        if c.get("is_main") and not c.get("title"):
            c["title"] = "Main"
    return ok(
        collection=collection,
        chains=dumped,
        total=len(dumped),
        hint="Use get_timeline for nested nodes in one call",
    )


__all__ += ["chains_view", "file_detail_view", "node_view"]
