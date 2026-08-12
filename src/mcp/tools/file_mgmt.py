"""MCP tools for the file-management system (L1 + agent-efficiency fixes).

**Route by goal (read)** — do not default everything to list_library_tree:
- Content facts / unknown file → search tools (``search_*_chunks``), not these
- Folder/file **layout** map → :func:`list_library_tree` (not ``list_documents``)
- Flat files + mounts/filters → :func:`list_files`
- Timeline / events / nodes → :func:`get_timeline` (not N× list_chains/get_chain)
- Versions + blob_available → :func:`list_file_versions`
- One file graph (paths/nodes/messages) → :func:`get_file`
- One node detail → :func:`get_node`

**Secondary**
- :func:`list_folders` / :func:`list_chains` / :func:`list_groups`

**Write**
- :func:`upload_file_from_staging` / :func:`upload_file_version_from_staging`
- :func:`set_file_definitive`

Body text for a known ``file_id`` lives in documents tools:
``get_document_text`` / ``get_file_chunks`` (prefer file_id).

Return type is compact MCP CallToolResult (short text + structuredContent).

Not in L1: create/reorder nodes, branch create, end-chain, drag-drop.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from src.mcp.common import err, mcp_result, ok, require_collection, run_sync, safe_filename

logger = logging.getLogger(__name__)


def _mcp_result(data: dict[str, Any]) -> Any:
    """Alias — shared implementation lives in :func:`src.mcp.common.mcp_result`."""
    return mcp_result(data)


def _dump(obj: Any) -> Any:
    """Recursively convert Pydantic models to plain dicts/lists."""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, list):
        return [_dump(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _dump(v) for k, v in obj.items()}
    return obj


def _http_err(exc: HTTPException) -> dict[str, Any]:
    detail = exc.detail
    if isinstance(detail, dict):
        return err(
            str(detail.get("message") or detail),
            **{k: v for k, v in detail.items() if k != "message"},
        )
    return err(str(detail), status_code=exc.status_code)


def _require_fm_collection(collection: str) -> dict[str, Any] | None:
    """Collection must exist and have file-mgmt DB initialized."""
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


def _parse_bool(s: str) -> bool | None:
    v = (s or "").strip().lower()
    if not v:
        return None
    if v in ("true", "1", "yes"):
        return True
    if v in ("false", "0", "no"):
        return False
    return None


async def _await_result(fn) -> Any:
    """Run blocking work and wrap as compact MCP CallToolResult."""
    data = await run_sync(fn)
    if not isinstance(data, dict):
        data = {"result": data}
    return _mcp_result(data)


# ── list_library_tree (folder/file layout only) ────────────────


async def list_library_tree(
    collection: str,
    max_depth: int = 0,
    include_orphans: bool = True,
    include_archived_files: bool = True,
    fields: str = "summary",
) -> Any:
    """Folder + file **layout map** for one collection (where files live).

    **When to use**
    - User asks what files/folders exist, where a file is mounted, or wants a
      library directory map.
    - Prefer over ``list_folders`` + N× ``list_files`` for hierarchical browse.

    **When not to use**
    - Factual Q&A / "what does the document say" → ``search_direct_chunks`` /
      ``search_agentic_chunks`` (do **not** browse the tree first).
    - Project events / timeline nodes / branches → ``get_timeline``.
    - Flat unique file list with filters/mounts → ``list_files``.
    - Chunk counts / legacy source keys → ``list_documents``.
    - Known ``file_id`` body text → ``get_document_text``.

    ``collection`` is a collection **ID** (not display name).

    **Count vs payload (important for agents):**

    - ``file_count`` / ``unique_file_count`` / ``mount_count`` = **real** library
      counts for that folder (always).
    - ``files`` = file objects **included in this response only**.
    - When depth is capped: ``truncated=true``, ``files=[]``,
      ``files_omitted=<real unique count>`` — **not** an empty folder.
      Deeper folder nodes are still returned with the same semantics.
    - Use top-level ``files_index[file_id]`` for mounts when ``fields`` is
      minimal/summary.

    Args:
        max_depth: ``0`` = unlimited (expand files at every depth).
            ``1`` = expand files for **root folders only** (depth 0);
            descendants stay in the tree as truncated stubs with real counts.
        include_orphans: include root files with no folder mount
        include_archived_files: include file-level archived files
        fields: ``minimal`` | ``summary`` (default) | ``full``.
            minimal/summary embed compact file refs under folders and put
            full mounts in top-level ``files_index`` (avoids multi-mount bloat).

    Returns ``folders``, ``orphans``, optional ``files_index``, ``summary``.
    """
    from src.file_mgmt import service as fm

    def _run() -> dict[str, Any]:
        if e := _require_fm_collection(collection):
            return e
        try:
            depth = None if not max_depth or max_depth <= 0 else int(max_depth)
            fld = (fields or "summary").strip().lower() or "summary"
            tree = fm.build_library_tree(
                collection,
                max_depth=depth,
                include_orphans=bool(include_orphans),
                include_archived_files=bool(include_archived_files),
                fields=fld,
            )
            return ok(collection=collection, **tree)
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("list_library_tree failed")
            return err(str(exc))

    return await _await_result(_run)


# ── list_folders ───────────────────────────────────────────────


async def list_folders(collection: str) -> dict[str, Any]:
    """List the folder tree only (**no files**).

    **When to use:** rare — only folder names/ids without file payloads.
    **When not:** hierarchical library map with files → :func:`list_library_tree`;
    content Q&A → search tools; timeline → :func:`get_timeline`.

    ``collection`` is a collection **ID**. Each folder has ``file_count``
    (unique files mounted in that folder — not multi-mount inflated).
    """
    from src.file_mgmt import service as fm

    def _run() -> dict[str, Any]:
        if e := _require_fm_collection(collection):
            return e
        try:
            tree = fm.get_folder_tree(collection)
            return ok(collection=collection, folders=_dump(tree))
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("list_folders failed")
            return err(str(exc))

    return await _await_result(_run)


# ── list_files ─────────────────────────────────────────────────


async def list_files(
    collection: str,
    folder_id: str = "",
    archived: str = "",
    is_definitive: str = "",
    scope: str = "all",
) -> Any:
    """Flat **unique** file list with **mounts** (where each file is attached).

    **When to use**
    - Need a flat unique file list, filters (archived / definitive), multi-mount
      detail, or files under one ``folder_id`` without the full tree.
    - Prefer over legacy ``list_documents`` when you need mounts.

    **When not to use**
    - Hierarchical “show the library folders” map → ``list_library_tree``.
    - Content facts / unknown which file → ``search_*_chunks``.
    - Timeline / events → ``get_timeline``.

    ``collection`` is a collection **ID**.

    **Default ``scope=\"all\"`` returns the full unique file set** for the
    collection (not only root orphans). Each item includes:

    - ``mounts`` / ``folder_ids``
    - ``current_version_no``, ``version_count``
    - ``lock_version`` (optimistic concurrency; alias of legacy ``version``)

    Args:
        folder_id: if set, only files mounted in that folder
        archived / is_definitive: ``\"true\"`` / ``\"false\"`` / empty
        scope: when ``folder_id`` empty — ``all`` (default) or ``orphans``
    """
    from src.file_mgmt import service as fm

    def _run() -> dict[str, Any]:
        if e := _require_fm_collection(collection):
            return e
        try:
            fid = folder_id.strip() or None
            sc = (scope or "all").strip().lower() or "all"
            if sc not in ("all", "orphans"):
                return err("scope must be 'all' or 'orphans'")
            files = fm.list_files_with_mounts(
                collection,
                folder_id=fid,
                archived=_parse_bool(archived),
                is_definitive=_parse_bool(is_definitive),
                scope=sc,
            )
            by_kind: dict[str, int] = {}
            for f in files:
                k = f.get("doc_kind") or "file"
                by_kind[k] = by_kind.get(k, 0) + 1
            return ok(
                collection=collection,
                scope=sc if not fid else "folder",
                folder_id=fid,
                files=files,
                total=len(files),
                summary={
                    "unique_file_count": len(files),
                    "by_doc_kind": by_kind,
                    "files": by_kind.get("file", 0),
                    "notes": by_kind.get("note", 0),
                    "meetings": by_kind.get("meeting", 0),
                },
            )
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("list_files failed")
            return err(str(exc))

    return await _await_result(_run)


# ── get_file ───────────────────────────────────────────────────


async def get_file(collection: str, file_id: str) -> dict[str, Any]:
    """Get full file detail: metadata, paths, versions, linked nodes, messages.

    **When to use**
    - Need mounts/paths, linked timeline nodes, messages, **and** versions
      together for one file.

    **When not to use**
    - Only need version list / gaps / blob_available summary → prefer
      :func:`list_file_versions` (lighter, annotated notes).
    - Only need plain text → :func:`get_document_text` with ``file_id``.

    ``collection`` is a collection **ID**. ``file_id`` from :func:`list_files`
    or :func:`list_library_tree`. Each version includes ``blob_available``.
    """
    from src.file_mgmt import service as fm

    def _run() -> dict[str, Any]:
        if e := _require_fm_collection(collection):
            return e
        if not (file_id or "").strip():
            return err("file_id is required")
        try:
            detail = fm.get_file_detail(collection, file_id.strip())
            return ok(collection=collection, file=_dump(detail))
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("get_file failed")
            return err(str(exc))

    return await _await_result(_run)


# ── list_file_versions ─────────────────────────────────────────


async def list_file_versions(collection: str, file_id: str) -> dict[str, Any]:
    """List all versions of a file (current + historical) with readability flags.

    **When to use**
    - User asks version history, gaps, or whether an old version is still readable.
    - **Before** calling ``get_document_text(…, version_id=…)`` on non-current rows:
      only proceed when ``blob_available=true``.

    **When not to use**
    - Need paths/nodes/messages as well → :func:`get_file` (includes versions[]).
    - Only need current body → ``get_document_text(file_id=…)`` without version_id.

    Each version has:

    - ``version_no``, ``version_id``, ``storage_file_id`` (basename for that
      version blob), ``commit_message``, ``archived``
    - **``blob_available``**: ``true`` only when the on-disk blob exists.
      When ``false``, ``get_document_text(…, version_id=…)`` will fail with
      ``extract_status=blob_missing`` — do not invent history from chunks.

    Missing version numbers in the sequence mean that version row was deleted
    (not returned) — see ``gaps``. Summary includes ``blob_available_count`` /
    ``blob_missing_count``.

    File summary field ``version`` / ``optimistic_lock_version`` is the
    concurrency counter; use ``current_version_no`` for content version labels.
    """
    from src.file_mgmt import service as fm

    def _run() -> dict[str, Any]:
        if e := _require_fm_collection(collection):
            return e
        if not (file_id or "").strip():
            return err("file_id is required")
        try:
            detail = fm.get_file_detail(collection, file_id.strip())
            data = _dump(detail)
            versions = data.get("versions") or []
            # Annotate storage_file_id meaning + detect gaps
            nos = sorted(
                int(v["version_no"])
                for v in versions
                if v.get("version_no") is not None
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
                file_id=file_id.strip(),
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
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("list_file_versions failed")
            return err(str(exc))

    return await _await_result(_run)


# ── get_timeline (timeline/node graph only) ────────────────────


async def get_timeline(
    collection: str,
    depth: str = "summary",
) -> Any:
    """Project **timeline / node graph** (events, branches, groups) in one call.

    **When to use**
    - User asks about timeline, project events, meeting nodes, branches, or
      “what happened in this project”.
    - Prefer over ``list_chains`` + N× ``get_chain`` for a full graph.

    **When not to use**
    - Folder/file library layout → :func:`list_library_tree` / :func:`list_files`.
    - Document content facts → ``search_direct_chunks`` / ``search_agentic_chunks``.
    - One known node's full attachments/messages → :func:`get_node`.

    ``collection`` is a collection **ID**.

    Args:
        depth: ``minimal`` | ``summary`` (default) | ``full``.
            summary+ includes short ``attachments`` (file_id+filename) on nodes.

    Returns:
        - ``timeline``: nested main (nodes + branches[])
        - ``detached_branches``: **must read** — full chains not in the nested
          tree (broken parent). Marked ``detached: true`` + ``detach_reason``.
        - ``chains`` / ``groups`` / ``warnings`` / ``read_hint``
        - ``node_order_rule``: ORDER BY order ASC, created_at ASC

    Always merge ``detached_branches`` with the nested tree so no nodes are missed.
    """
    from src.file_mgmt import service as fm

    def _run() -> dict[str, Any]:
        if e := _require_fm_collection(collection):
            return e
        try:
            d = (depth or "summary").strip().lower() or "summary"
            data = fm.build_timeline(collection, depth=d)
            return ok(collection=collection, **data)
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("get_timeline failed")
            return err(str(exc))

    return await _await_result(_run)


# ── list_chains ────────────────────────────────────────────────


async def list_chains(collection: str) -> dict[str, Any]:
    """List timeline chain skeletons (**no nodes**).

    **When to use:** rare — only need chain ids/titles without node payloads.
    **When not:** prefer :func:`get_timeline` for a full nested graph in one call.

    Main chain with null title is shown as title=\"Main\" inside get_timeline.
    """
    from src.file_mgmt import service as fm

    def _run() -> dict[str, Any]:
        if e := _require_fm_collection(collection):
            return e
        try:
            chains = fm.list_chains(collection)
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
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("list_chains failed")
            return err(str(exc))

    return await _await_result(_run)


# ── get_chain ──────────────────────────────────────────────────


async def get_chain(collection: str, chain_id: str) -> dict[str, Any]:
    """Get **one** chain with enriched nodes (group_name, counts, child_branches).

    **When to use:** already know ``chain_id`` and only need that branch.
    **When not:** whole timeline → :func:`get_timeline` (avoids N calls).

    Nodes sorted by ``(order ASC, created_at ASC)``.
    """
    from src.file_mgmt import service as fm

    def _run() -> dict[str, Any]:
        if e := _require_fm_collection(collection):
            return e
        cid = (chain_id or "").strip()
        if not cid:
            return err("chain_id is required")
        try:
            # Reuse timeline builder for one chain via full tree, then pick
            data = fm.build_timeline(collection, depth="summary")
            # Find chain in flat list + nodes from nested walk
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
                # Branch with no nodes still ok — build minimal
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
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("get_chain failed")
            return err(str(exc))

    return await _await_result(_run)


# ── get_node ───────────────────────────────────────────────────


async def get_node(collection: str, node_id: str) -> dict[str, Any]:
    """Get one timeline node with **full** attachments and messages.

    **When to use:** after ``get_timeline`` / ``get_chain`` when you need the
    complete attachment list, message bodies, or node metadata for one node_id.
    **When not:** surveying the whole graph — start with ``get_timeline``.
    """
    from src.file_mgmt import service as fm

    def _run() -> dict[str, Any]:
        if e := _require_fm_collection(collection):
            return e
        nid = (node_id or "").strip()
        if not nid:
            return err("node_id is required")
        try:
            detail = fm.get_node_detail(collection, nid)
            return ok(collection=collection, node=_dump(detail))
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("get_node failed")
            return err(str(exc))

    return await _await_result(_run)


# ── list_groups ────────────────────────────────────────────────


async def list_groups(collection: str) -> dict[str, Any]:
    """List node groups (timeline grouping labels). Read-only in L1.

    **When to use:** need group catalog (name ↔ folder) without the full timeline.
    **When not:** understanding event order — use ``get_timeline`` (includes groups).
    """
    from src.file_mgmt import service as fm

    def _run() -> dict[str, Any]:
        if e := _require_fm_collection(collection):
            return e
        try:
            groups = fm.list_groups(collection)
            return ok(collection=collection, groups=_dump(groups), total=len(groups))
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("list_groups failed")
            return err(str(exc))

    return await _await_result(_run)


# ── staging helpers ────────────────────────────────────────────


async def _load_staged_bytes(
    staging_token: str,
    file_path: str,
    filename: str,
) -> tuple[bytes, str] | dict[str, Any]:
    """Return (raw, filename) or an err() dict."""
    from src.mcp.staging import staging_store

    if file_path and not staging_token:
        path = Path(file_path)
        if not path.is_file():
            return err(f"File not found: {file_path}")
        resolved = path.resolve()
        allowed_roots = [Path("data").resolve(), Path("/tmp").resolve()]
        if not any(str(resolved).startswith(str(root)) for root in allowed_roots):
            return err(f"File path must be under data/ or /tmp/. Got: {file_path}")
        try:
            raw = path.read_bytes()
        except Exception as exc:
            return err(f"Failed to read file: {exc}")
        use_filename = filename.strip() or path.name
        return raw, use_filename

    if staging_token:
        entry = await staging_store.take(staging_token)
        if entry is None:
            return err(
                f"Staging token '{staging_token}' not found or expired. "
                f"Tokens expire after 10 minutes."
            )
        use_filename = filename.strip() if filename.strip() else entry.filename
        return entry.content, use_filename

    return err(
        "Either staging_token or file_path is required. "
        "Stage via POST /api/mcp/stage-content, or use a server-local file_path."
    )


# ── upload_file_from_staging ───────────────────────────────────


async def upload_file_from_staging(
    collection: str = "default",
    staging_token: str = "",
    filename: str = "",
    file_path: str = "",
    folder_id: str = "",
    on_name_conflict: str = "error",
) -> dict[str, Any]:
    """Upload a file into file-management (folder or collection root).

    Prefer staging::

        curl -F \"file=@/path/to/report.pdf\" -F \"collection=col_xxx\" {base_url}/api/mcp/upload

    ``folder_id`` empty = root orphan. Poll ``task_id`` with :func:`get_task_status`.
    """
    from src.file_mgmt import service as fm

    loaded = await _load_staged_bytes(staging_token, file_path, filename)
    if isinstance(loaded, dict):
        return loaded
    raw, use_filename = loaded

    try:
        safe_name = safe_filename(use_filename)
    except ValueError as exc:
        return err(str(exc))

    conflict = (on_name_conflict or "error").strip() or "error"
    if conflict not in ("error", "auto_rename"):
        return err("on_name_conflict must be 'error' or 'auto_rename'")

    fid = folder_id.strip() or None

    def _run() -> dict[str, Any]:
        if e := _require_fm_collection(collection):
            return e
        try:
            result = fm.upload_file_to_folder(
                collection,
                fid,
                raw,
                safe_name,
                source_node_id=None,
                on_name_conflict=conflict,
            )
            data = _dump(result)
            return ok(
                message="File uploaded; ingest may be async",
                collection=collection,
                file=data,
                file_id=data.get("file_id"),
                task_id=data.get("task_id"),
                folder_id=fid,
            )
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("upload_file_from_staging failed")
            return err(str(exc))

    return await _await_result(_run)


# ── upload_file_version_from_staging ───────────────────────────


async def upload_file_version_from_staging(
    collection: str = "default",
    file_id: str = "",
    staging_token: str = "",
    filename: str = "",
    file_path: str = "",
    commit_message: str = "",
) -> dict[str, Any]:
    """Upload a new version of an existing file. ``file_id`` required."""
    from src.file_mgmt import service as fm

    if not (file_id or "").strip():
        return err("file_id is required")

    loaded = await _load_staged_bytes(staging_token, file_path, filename)
    if isinstance(loaded, dict):
        return loaded
    raw, use_filename = loaded

    try:
        safe_name = safe_filename(use_filename)
    except ValueError as exc:
        return err(str(exc))

    fid = file_id.strip()

    def _run() -> dict[str, Any]:
        if e := _require_fm_collection(collection):
            return e
        try:
            result = fm.upload_file_version(
                collection,
                fid,
                raw,
                safe_name,
                commit_message=commit_message or "",
            )
            data = _dump(result)
            return ok(
                message="New version uploaded; ingest may be async",
                collection=collection,
                file=data,
                file_id=fid,
                task_id=data.get("task_id"),
            )
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("upload_file_version_from_staging failed")
            return err(str(exc))

    return await _await_result(_run)


# ── set_file_definitive ────────────────────────────────────────


async def set_file_definitive(
    collection: str,
    file_id: str,
    is_definitive: bool = True,
) -> dict[str, Any]:
    """Set or clear the definitive flag on a file (feeds collection summary)."""
    from src.file_mgmt import service as fm

    def _run() -> dict[str, Any]:
        if e := _require_fm_collection(collection):
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
            return ok(
                collection=collection,
                file=_dump(result),
                is_definitive=bool(is_definitive),
            )
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("set_file_definitive failed")
            return err(str(exc))

    return await _await_result(_run)
