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
- :func:`list_todos` / :func:`create_todo` / :func:`update_todo` / :func:`delete_todo`

**Write**
- :func:`upload_file_from_staging` / :func:`upload_file_version_from_staging`
- :func:`set_file_definitive`
- :func:`create_todo` / :func:`update_todo` / :func:`delete_todo` (destructive)

Body text for a known ``file_id`` lives in documents tools:
``get_document_text`` / ``get_file_chunks`` (prefer file_id).

Return type is compact MCP CallToolResult (short text + structuredContent).

Not in L1: create/reorder nodes, branch create, end-chain, drag-drop.
"""

from __future__ import annotations

from mcp.server.fastmcp import Context

import logging
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from src.mcp.common import err, mcp_result, ok, require_collection, run_sync, safe_filename, module_gated
from src.hub.adapters import envelope, total_of
from src.hub.confirm import confirm_destructive
from src.hub.routing import _require_collection_on_remote, body_without, hub_routed
from src.hub.upload_forwards import (
    upload_file_forward,
    upload_file_version_forward,
)
from src.hub.write_forwards import (
    create_todo_forward,
    delete_todo_forward,
    require_fm_collection_on_remote,
    update_todo_forward,
)

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


# 迁往 src/file_mgmt/views（批次 4：REST 端点与工具共用）。保留**转发薄壳**
#（而不是赋值别名——别名在 import 时固化函数对象，测试 patch views 侧时
# 旧调用点会漏掉）。
def _require_fm_collection(collection: str) -> dict[str, Any] | None:
    from src.file_mgmt.views import require_fm_collection

    return require_fm_collection(collection)


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


@module_gated("chat", "database")
@hub_routed(
    method="GET",
    path="/file-mgmt/{collection}/library-tree",
    precheck=require_fm_collection_on_remote,
    query=lambda a: {
        "max_depth": a.get("max_depth", 0),
        # ⚠️ 不能写 `or None`——显式 False 会被吞，REST 默认 True 就把
        # 归档文件放进来了（R5 泄露）。按布尔显式取。
        "include_archived_files": bool(a.get("include_archived_files", True)),
        "fields": a.get("fields") or "summary",
    },
    forwards=("collection", "max_depth", "include_archived_files", "fields"),
)
async def list_library_tree(
    collection: str,
    max_depth: int = 0,
    include_archived_files: bool = True,
    fields: str = "summary",
    ctx: Context | None = None,
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

    Each embedded file ref is ``{file_id, name, doc_kind}`` (``name`` =
    display label, falling back to filename) plus sparse flags that appear
    only when informative: ``is_definitive``/``archived`` (true),
    ``mount_count``/``version_count`` (>1). A multi-mounted file appears
    under **each** of its folders — the tree is the full mount map. Mount
    bookkeeping (is_primary, per-path archive) → ``get_file(file_id)``.

    Args:
        max_depth: ``0`` = unlimited (expand files at every depth).
            ``1`` = expand files for **root folders only** (depth 0);
            descendants stay in the tree as truncated stubs with real counts.
        include_archived_files: include file-level archived files
        fields: ``minimal`` | ``summary`` (default) | ``full``.
            ``full`` embeds complete file objects with mounts (large when
            multi-mounted); minimal/summary stay slim.

    Returns ``folders`` (Root included as a folder) and ``summary``.
    """
    def _run() -> dict[str, Any]:
        from src.file_mgmt.views import library_tree_view

        try:
            return library_tree_view(
                collection,
                max_depth=max_depth,
                include_archived_files=include_archived_files,
                fields=fields,
            )
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("list_library_tree failed")
            return err(str(exc))

    return await _await_result(_run)


# ── list_folders ───────────────────────────────────────────────


@module_gated("chat", "database")
@hub_routed(
    method="GET",
    path="/file-mgmt/{collection}/folders",
    precheck=require_fm_collection_on_remote,
    # ★ 这个 query 是**算出来的常量**，不是透传工具参数 —— 因为工具**没有**
    # ``include_unassigned`` 参数，它的契约是隐含的「不含未分配」（v2 R6）。
    # 若写成透传，args 里取不到该键 → REST 用默认 ``True`` → **未分配会被暴露**。
    # 这正是「参数无法忠实转发」那类静默陷阱的镜像：**必须补上工具隐含的语义**。
    query=lambda _args: {"include_unassigned": False},
    shape=envelope("folders", extra=total_of("folders")),
    forwards=("collection",),
)
async def list_folders(collection: str, ctx: Context | None = None) -> dict[str, Any]:
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
            # ``include_unassigned=False``：未分配池对库浏览不可见（v2 R6）。
            # **没有 ``include_root`` 这个参数** —— 根节点本来就总是返回
            # （``get_folder_tree`` 按 ``children_map[None]`` 取根）。历史上这里传了它，
            # 于是本工具**每次都抛 TypeError**、从改版那天起就是个死工具
            # （2026-09-18 用一个一个过工具的走查发现；引入于 e7553a8，与本次重构无关）。
            tree = fm.get_folder_tree(collection, include_unassigned=False)
            return ok(collection=collection, folders=_dump(tree))
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("list_folders failed")
            return err(str(exc))

    return await _await_result(_run)


# ── list_files ─────────────────────────────────────────────────


@module_gated("chat", "database")
@hub_routed(
    method="GET",
    path="/file-mgmt/{collection}/files",
    # lean=true：REST 侧的 Agent 信封形态（files.list_files_lean，与本工具
    # 本地实现共用）——裸 REST 是「Root 视图 + 无 mounts + 未分配可见」，
    # 与工具契约在三个轴上都不同，靠 lean 一次对齐。
    # scope 校验交给 REST（400 同文案）——本地也是先过 fm 库校验再验 scope，
    # 顺序一致（refuse 会先于 precheck 跑，反而颠倒本地的报错顺序）。
    precheck=require_fm_collection_on_remote,
    query=lambda a: {
        "lean": True,
        "folder_id": (a.get("folder_id") or "").strip() or None,
        "archived": _parse_bool(a.get("archived")),
        "is_definitive": _parse_bool(a.get("is_definitive")),
        "scope": a.get("scope") or "all",
    },
    forwards=("collection", "folder_id", "archived", "is_definitive", "scope"),
)
async def list_files(
    collection: str,
    folder_id: str = "",
    archived: str = "",
    is_definitive: str = "",
    scope: str = "all",
    ctx: Context | None = None,
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
            # 信封/mounts/未分配过滤/聚合全部住在 service 层的同一份实现里
            #（REST ?lean=true 与本工具共用——两侧永不漂移）。
            return fm.list_files_lean(
                collection,
                folder_id=folder_id.strip() or None,
                archived=_parse_bool(archived),
                is_definitive=_parse_bool(is_definitive),
                scope=scope,
            )
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("list_files failed")
            return err(str(exc))

    return await _await_result(_run)


# ── get_file ───────────────────────────────────────────────────


@module_gated("chat", "database")
@hub_routed(
    method="GET",
    path="/agent/file-mgmt/{collection}/files/{file_id}",
    precheck=require_fm_collection_on_remote,
    forwards=("collection", "file_id"),
)
async def get_file(
    collection: str, file_id: str, ctx: Context | None = None,
) -> dict[str, Any]:
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
    def _run() -> dict[str, Any]:
        from src.file_mgmt.views import file_detail_view

        try:
            return file_detail_view(collection, file_id)
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("get_file failed")
            return err(str(exc))

    return await _await_result(_run)


# ── list_file_versions ─────────────────────────────────────────


@module_gated("chat", "database")
@hub_routed(
    method="GET",
    path="/file-mgmt/{collection}/files/{file_id}/versions",
    precheck=require_fm_collection_on_remote,
    forwards=("collection", "file_id"),
)
async def list_file_versions(
    collection: str, file_id: str, ctx: Context | None = None,
) -> dict[str, Any]:
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
    def _run() -> dict[str, Any]:
        # 信封（含未分配闸门/gaps/blob 计数）住在 views.file_versions_view，
        # REST 端点与本工具共用同一份（防两侧漂移）。
        from src.file_mgmt.views import file_versions_view

        try:
            return file_versions_view(collection, file_id)
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("list_file_versions failed")
            return err(str(exc))

    return await _await_result(_run)


# ── get_timeline (timeline/node graph only) ────────────────────


@module_gated("chat", "database")
@hub_routed(
    method="GET",
    path="/file-mgmt/{collection}/timeline",
    precheck=require_fm_collection_on_remote,
    query=lambda a: {"depth": a.get("depth") or "summary"},
    forwards=("collection", "depth"),
)
async def get_timeline(
    collection: str,
    depth: str = "summary",
    ctx: Context | None = None,
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
    def _run() -> dict[str, Any]:
        from src.file_mgmt.views import timeline_view

        try:
            return timeline_view(collection, depth=depth)
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("get_timeline failed")
            return err(str(exc))

    return await _await_result(_run)


# ── list_chains ────────────────────────────────────────────────


@module_gated("chat", "database")
@hub_routed(
    method="GET",
    path="/agent/file-mgmt/{collection}/chains",
    precheck=require_fm_collection_on_remote,
    forwards=("collection",),
)
async def list_chains(
    collection: str, ctx: Context | None = None,
) -> dict[str, Any]:
    """List timeline chain skeletons (**no nodes**).

    **When to use:** rare — only need chain ids/titles without node payloads.
    **When not:** prefer :func:`get_timeline` for a full nested graph in one call.

    Main chain with null title is shown as title=\"Main\" inside get_timeline.
    """
    def _run() -> dict[str, Any]:
        from src.file_mgmt.views import chains_view

        try:
            return chains_view(collection)
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("list_chains failed")
            return err(str(exc))

    return await _await_result(_run)


# ── get_chain ──────────────────────────────────────────────────


@module_gated("chat", "database")
@hub_routed(
    method="GET",
    path="/file-mgmt/{collection}/chains/{chain_id}/view",
    precheck=require_fm_collection_on_remote,
    forwards=("collection", "chain_id"),
)
async def get_chain(
    collection: str, chain_id: str, ctx: Context | None = None,
) -> dict[str, Any]:
    """Get **one** chain with enriched nodes (group_name, counts, child_branches).

    **When to use:** already know ``chain_id`` and only need that branch.
    **When not:** whole timeline → :func:`get_timeline` (avoids N calls).

    Nodes sorted by ``(order ASC, created_at ASC)``.
    """
    def _run() -> dict[str, Any]:
        from src.file_mgmt.views import chain_view

        try:
            return chain_view(collection, chain_id)
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("get_chain failed")
            return err(str(exc))

    return await _await_result(_run)


# ── get_node ───────────────────────────────────────────────────


@module_gated("chat", "database")
@hub_routed(
    method="GET",
    path="/agent/file-mgmt/{collection}/nodes/{node_id}",
    precheck=require_fm_collection_on_remote,
    forwards=("collection", "node_id"),
)
async def get_node(
    collection: str, node_id: str, ctx: Context | None = None,
) -> dict[str, Any]:
    """Get one timeline node with **full** attachments and messages.

    **When to use:** after ``get_timeline`` / ``get_chain`` when you need the
    complete attachment list, message bodies, or node metadata for one node_id.
    **When not:** surveying the whole graph — start with ``get_timeline``.
    """
    def _run() -> dict[str, Any]:
        from src.file_mgmt.views import node_view

        try:
            return node_view(collection, node_id)
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("get_node failed")
            return err(str(exc))

    return await _await_result(_run)


# ── list_groups ────────────────────────────────────────────────


@module_gated("chat", "database")
@hub_routed(
    method="GET",
    path="/file-mgmt/{collection}/groups",
    precheck=require_fm_collection_on_remote,
    shape=envelope("groups", extra=total_of("groups")),
    forwards=("collection",),
)
async def list_groups(collection: str, ctx: Context | None = None) -> dict[str, Any]:
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


# ── collection todos ───────────────────────────────────────────

_ME_UNSET = (
    "No Me person is set. Mark yourself in People before filtering to mine."
)
_WRITE_COLLECTION_REQUIRED = (
    "collection is required to create/update/delete a todo. "
    "Use list_collections to get an ID."
)


def _resolve_done_filter(include_done: bool, done: bool | None) -> bool | None:
    if done is not None:
        return bool(done)
    if include_done:
        return None
    return False


def _me_person_id() -> str | None:
    from src.speakers.store import get_me_person_id

    return get_me_person_id()


def _resolve_assignee(
    assign_to_me: bool,
    assignee_person_id: str,
) -> tuple[str | None, dict[str, Any] | None]:
    explicit = (assignee_person_id or "").strip() or None
    if explicit:
        return explicit, None
    if assign_to_me:
        pid = _me_person_id()
        if not pid:
            return None, err(_ME_UNSET)
        return pid, None
    return None, None


def _todo_dict(todo: Any, collection_id: str) -> dict[str, Any]:
    # 增富实现已下沉到 service 层（REST 与 MCP 共用一份，防两侧漂移——
    # 批次 2 盘点时发现转发路径拿不到这三个字段）。这里保留薄壳给既有调用点。
    from src.file_mgmt.todos import todo_public_dict

    if not isinstance(todo, dict) and not hasattr(todo, "model_dump"):
        d = _dump(todo)
        if isinstance(d, dict):
            todo = d
    return todo_public_dict(todo, collection_id)


def _iter_fm_collections() -> list[tuple[str, str]]:
    from src.collections.store import list_collections_meta
    from src.file_mgmt.store import get_db

    out: list[tuple[str, str]] = []
    for meta in list_collections_meta():
        cid = str(meta.get("id") or "").strip()
        if not cid:
            continue
        try:
            conn = get_db(cid)
            conn.close()
        except Exception:
            continue
        out.append((cid, str(meta.get("name") or cid)))
    return out


def _list_todos_in_collection(
    collection: str,
    *,
    done: bool | None,
    mine: bool,
    me_id: str | None,
) -> list[dict[str, Any]]:
    from src.file_mgmt import service as fm

    items = fm.list_todos(collection, done=done)
    dumped = [_todo_dict(t, collection) for t in items]
    if mine:
        dumped = [t for t in dumped if t.get("assignee_person_id") == me_id]
    return dumped


def _todos_query(args: dict[str, Any]) -> dict[str, Any]:
    """``include_done`` / ``done`` → REST 的单个 ``done`` 参数（算出来，不透传）。

    工具的语义（见 ``_resolve_done_filter``）：``done`` 显式给就用它；
    否则 ``include_done=True`` → 不过滤；默认只看未完成。
    REST ``/todos`` 只认一个 ``done`` —— 直接透传两个参数会让 ``include_done``
    落空，而"包含已完成"的需求就静默失效了。
    """
    mine = args.get("mine") or None
    if args.get("done") is not None:
        return {"done": bool(args["done"]), "mine": mine}
    if args.get("include_done"):
        return {"mine": mine}
    return {"done": False, "mine": mine}


@module_gated("chat", "database")
@hub_routed(
    method="GET",
    path="/file-mgmt/{collection}/todos",
    # 读也会自动建库（_open_db 幂等创建）——与写同一道 fm 前置校验。
    precheck=require_fm_collection_on_remote,
    query=_todos_query,
    shape=lambda payload, args: (
        {
            "todos": payload,
            "total": len(payload),
            "mine": bool(args.get("mine")),
            "collection": args.get("collection"),
        }
        if isinstance(payload, list)
        else payload
    ),
    refuse=lambda args: (
        # 空 collection = 本地"扫每个库"的语义，远端**没有**对应端点
        # （``/api/file-mgmt//todos`` 会 404，实测）。这种情况必须**明说**，
        # 而不是让调用方收到一个 404 或（更糟）被静默当成空结果。
        None
        if args.get("collection")
        else 'list_todos without a collection scans every library on THIS machine; '
        'the remote API has no equivalent. Pass a collection, or call '
        'switch_box with name="local" to scan all of them here.'
    ),
    forwards=("collection", "include_done", "done", "mine"),
)
async def list_todos(
    collection: str = "",
    include_done: bool = False,
    done: bool | None = None,
    mine: bool = False,
    ctx: Context | None = None,
) -> Any:
    """List collection to-dos (checklist items, not timeline nodes).

    **When to use:** user asks what to-dos exist, what is open, what is assigned
    to them, or due dates across one library or all libraries.

    **When not to use:** document Q&A → search tools; folder layout →
    ``list_library_tree``; timeline events → ``get_timeline``.

    Default returns **open** (not done) items. Set ``include_done=true`` to
    include completed (open first). Set ``done=true/false`` for an exact filter
    (overrides ``include_done``).

    ``mine=true`` keeps items whose assignee is the People **Me** person.
    Unassigned items are not mine. If Me is not set, this returns an error.

    Omit ``collection`` to scan every library that has a file-management DB.
    """

    def _run() -> dict[str, Any]:
        me_id = None
        if mine:
            me_id = _me_person_id()
            if not me_id:
                return err(_ME_UNSET)
        done_f = _resolve_done_filter(include_done, done)
        col = (collection or "").strip()
        try:
            if col:
                if e := _require_fm_collection(col):
                    return e
                todos = _list_todos_in_collection(
                    col, done=done_f, mine=mine, me_id=me_id
                )
                return ok(collection=col, todos=todos, total=len(todos), mine=mine)
            todos: list[dict[str, Any]] = []
            for cid, _name in _iter_fm_collections():
                try:
                    todos.extend(
                        _list_todos_in_collection(
                            cid, done=done_f, mine=mine, me_id=me_id
                        )
                    )
                except HTTPException:
                    continue
                except Exception:
                    logger.debug("list_todos skip collection %s", cid, exc_info=True)
                    continue
            return ok(todos=todos, total=len(todos), mine=mine)
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("list_todos failed")
            return err(str(exc))

    return await _await_result(_run)


def _todo_collection_required(args: dict[str, Any]) -> str | None:
    """todo 写类共用的 refuse：空 collection（本地文案同源）。"""
    if not str(args.get("collection") or "").strip():
        return _WRITE_COLLECTION_REQUIRED
    return None


@module_gated("chat", "database")
@hub_routed(
    # 批量语义（todos 列表优先）→ 循环 N 次 REST 再按本地实现同构聚合；
    # assign_to_me 在**远端**解析 Me。见 write_forwards.create_todo_forward。
    forward=create_todo_forward,
    precheck=require_fm_collection_on_remote,
    refuse=_todo_collection_required,
    forwards=("collection", "title", "body", "ddl",
              "assign_to_me", "assignee_person_id", "todos"),
)
async def create_todo(
    collection: str,
    title: str = "",
    body: str = "",
    ddl: str = "",
    assign_to_me: bool = False,
    assignee_person_id: str = "",
    todos: list[dict[str, Any]] | None = None,
    ctx: Context | None = None,
) -> Any:
    """Create one or more collection to-dos in a single call.

    **When to use:** user asks to add / create to-do(s). Prefer ``todos`` with
    every item (title, optional body/ddl/assignee) instead of repeated calls.

    Requires a collection **ID**. Default unassigned; ``assign_to_me`` uses Me.
    """
    from src.file_mgmt import service as fm
    from src.file_mgmt.models import TodoCreate

    def _run() -> dict[str, Any]:
        col = (collection or "").strip()
        if not col:
            return err(_WRITE_COLLECTION_REQUIRED)
        if e := _require_fm_collection(col):
            return e
        items: list[dict[str, Any]] = []
        if isinstance(todos, list):
            items.extend(x for x in todos if isinstance(x, dict))
        if (title or "").strip():
            items.insert(
                0,
                {
                    "title": title,
                    "body": body,
                    "ddl": ddl,
                    "assign_to_me": assign_to_me,
                    "assignee_person_id": assignee_person_id,
                },
            )
        if not items:
            return err("title or todos is required")
        created: list[dict[str, Any]] = []
        errors: list[str] = []
        try:
            for it in items:
                t = str(it.get("title") or "").strip()
                if not t:
                    errors.append("empty title")
                    continue
                to_me = bool(it.get("assign_to_me") or False)
                pid = str(it.get("assignee_person_id") or "")
                assignee, aerr = _resolve_assignee(to_me, pid)
                if aerr:
                    return aerr
                todo = fm.create_todo(
                    col,
                    TodoCreate(
                        title=t,
                        body=(str(it.get("body") or "").strip() or None),
                        ddl=(str(it.get("ddl") or "").strip() or None),
                        assignee_person_id=assignee,
                    ),
                )
                created.append(_todo_dict(todo, col))
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("create_todo failed")
            return err(str(exc))
        if not created:
            return err("; ".join(errors) or "title or todos is required")
        return ok(
            collection=col,
            todo=created[0],
            todos=created,
            total=len(created),
            errors=errors or None,
        )

    return await _await_result(_run)


def _todo_update_fields(
    item: dict[str, Any],
    *,
    resolve_assignee: "Callable[[bool, str], tuple[str | None, dict[str, Any] | None]] | None" = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Build TodoUpdate kwargs; second value is an err() payload if assignee fails.

    ``resolve_assignee`` 可注入：本地实现用默认的（读本机 People-Me）；
    跨盒子转发传**远端版**（在目标盒子上解析 Me），逻辑零漂移。
    """
    from typing import Callable  # noqa: F401  (仅注解用，避免模块顶部多一个 import)

    _resolve = resolve_assignee or _resolve_assignee
    fields: dict[str, Any] = {}
    title = str(item.get("title") or "").strip()
    if title:
        fields["title"] = title
    if item.get("clear_body"):
        fields["clear_body"] = True
    elif "body" in item and item.get("body") != "":
        fields["body"] = item.get("body")
    if item.get("clear_ddl"):
        fields["clear_ddl"] = True
    elif str(item.get("ddl") or "").strip():
        fields["ddl"] = str(item.get("ddl")).strip()
    if item.get("done") is not None:
        fields["done"] = bool(item.get("done"))
    if item.get("clear_assignee"):
        fields["clear_assignee"] = True
    elif str(item.get("assignee_person_id") or "").strip() or item.get("assign_to_me"):
        assignee, aerr = _resolve(
            bool(item.get("assign_to_me") or False),
            str(item.get("assignee_person_id") or ""),
        )
        if aerr:
            return {}, aerr
        fields["assignee_person_id"] = assignee
    return fields, None


@module_gated("chat", "database")
@hub_routed(
    # 批量语义（updates 列表）+ 空字段项回读当前 todo —— 见
    # write_forwards.update_todo_forward（条目→TodoUpdate 字段逻辑复用
    # _todo_update_fields，注入远端 assignee 解析器）。
    forward=update_todo_forward,
    precheck=require_fm_collection_on_remote,
    refuse=_todo_collection_required,
    forwards=(
        "collection", "todo_id", "title", "body", "clear_body",
        "ddl", "clear_ddl", "done", "assignee_person_id",
        "assign_to_me", "clear_assignee", "updates",
    ),
)
async def update_todo(
    collection: str,
    todo_id: str = "",
    title: str = "",
    body: str = "",
    clear_body: bool = False,
    ddl: str = "",
    clear_ddl: bool = False,
    done: bool | None = None,
    assignee_person_id: str = "",
    assign_to_me: bool = False,
    clear_assignee: bool = False,
    updates: list[dict[str, Any]] | None = None,
    ctx: Context | None = None,
) -> Any:
    """Update one or more to-dos in a single call.

    Prefer ``updates`` (each with ``todo_id`` plus fields) when changing several
    items. A lone ``todo_id`` plus fields still works.
    """
    from src.file_mgmt import service as fm
    from src.file_mgmt.models import TodoUpdate

    def _run() -> dict[str, Any]:
        col = (collection or "").strip()
        if not col:
            return err(_WRITE_COLLECTION_REQUIRED)
        if e := _require_fm_collection(col):
            return e
        items: list[dict[str, Any]] = []
        if isinstance(updates, list):
            items.extend(x for x in updates if isinstance(x, dict))
        if (todo_id or "").strip():
            items.insert(
                0,
                {
                    "todo_id": todo_id,
                    "title": title,
                    "body": body,
                    "clear_body": clear_body,
                    "ddl": ddl,
                    "clear_ddl": clear_ddl,
                    "done": done,
                    "assignee_person_id": assignee_person_id,
                    "assign_to_me": assign_to_me,
                    "clear_assignee": clear_assignee,
                },
            )
        if not items:
            return err("todo_id or updates is required")
        patched: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        try:
            for it in items:
                tid = str(it.get("todo_id") or "").strip()
                if not tid:
                    errors.append({"todo_id": "", "error": "todo_id is required"})
                    continue
                fields, aerr = _todo_update_fields(it)
                if aerr:
                    return aerr
                if not fields:
                    todo = fm.get_todo(col, tid)
                    patched.append(_todo_dict(todo, col))
                    continue
                try:
                    todo = fm.update_todo(col, tid, TodoUpdate(**fields))
                    patched.append(_todo_dict(todo, col))
                except HTTPException as exc:
                    errors.append({"todo_id": tid, "error": str(exc.detail)})
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("update_todo failed")
            return err(str(exc))
        if not patched and errors:
            return err(errors[0]["error"], results=errors)
        return ok(
            collection=col,
            todo=patched[0] if patched else None,
            todos=patched,
            total=len(patched),
            errors=errors or None,
        )

    return await _await_result(_run)


@module_gated("chat", "database")
@confirm_destructive()
@hub_routed(
    # 批量删除（todo_ids 去重合并）+ 逐条 404 记入 errors 继续 —— 见
    # write_forwards.delete_todo_forward。
    forward=delete_todo_forward,
    precheck=require_fm_collection_on_remote,
    refuse=_todo_collection_required,
    forwards=("collection", "todo_id", "todo_ids"),
)
async def delete_todo(
    collection: str,
    todo_id: str = "",
    todo_ids: list[str] | None = None,
    ctx: Context | None = None,
) -> Any:
    """Delete one or more collection to-dos. Destructive.

    **When to use:** user explicitly asks to delete / remove to-dos.
    Prefer ``todo_ids`` with every id in one call. ``todo_id`` is a single-id
    alias. Chat/Quick Chat confirm each id; MCP clients should confirm first.

    Requires a collection **ID**.
    """
    from src.file_mgmt import service as fm

    def _run() -> dict[str, Any]:
        col = (collection or "").strip()
        if not col:
            return err(_WRITE_COLLECTION_REQUIRED)
        ids: list[str] = []
        seen: set[str] = set()
        for raw in list(todo_ids or []) + [todo_id]:
            tid = str(raw or "").strip()
            if tid and tid not in seen:
                seen.add(tid)
                ids.append(tid)
        if not ids:
            return err("todo_id or todo_ids is required")
        if e := _require_fm_collection(col):
            return e
        deleted: list[str] = []
        errors: list[dict[str, str]] = []
        try:
            for tid in ids:
                try:
                    fm.delete_todo(col, tid)
                    deleted.append(tid)
                except HTTPException as exc:
                    errors.append({"todo_id": tid, "error": str(exc.detail)})
        except Exception as exc:
            logger.exception("delete_todo failed")
            return err(str(exc))
        if not deleted and errors:
            return err(errors[0]["error"], results=errors)
        return ok(
            collection=col,
            deleted=True,
            todo_id=deleted[0] if len(deleted) == 1 else None,
            todo_ids=deleted,
            errors=errors or None,
            total=len(deleted),
        )

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
        try:
            from src.paths import assert_readable_data_file

            assert_readable_data_file(path)
        except ValueError:
            return err(f"File path must be under data/ (not config, qdrant, or *.db). Got: {file_path}")
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


@module_gated("chat", "database")
@hub_routed(
    forward=upload_file_forward,
    precheck=require_fm_collection_on_remote,
    timeout=300.0,
    forwards=("collection", "staging_token", "filename", "file_path",
              "folder_id", "on_name_conflict"),
)
async def upload_file_from_staging(
    collection: str = "default",
    staging_token: str = "",
    filename: str = "",
    file_path: str = "",
    folder_id: str = "",
    on_name_conflict: str = "error",
    ctx: Context | None = None,
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


@module_gated("chat", "database")
@hub_routed(
    forward=upload_file_version_forward,
    precheck=require_fm_collection_on_remote,
    timeout=300.0,
    forwards=("collection", "file_id", "staging_token", "filename",
              "file_path", "commit_message"),
)
async def upload_file_version_from_staging(
    collection: str = "default",
    file_id: str = "",
    staging_token: str = "",
    filename: str = "",
    file_path: str = "",
    commit_message: str = "",
    ctx: Context | None = None,
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


@module_gated("chat", "database")
@hub_routed(
    method="POST",
    path="/file-mgmt/{collection}/files/{file_id}/definitive",
    precheck=require_fm_collection_on_remote,
    body=body_without("collection", "file_id"),
    forwards=("collection", "file_id", "is_definitive"),
)
async def set_file_definitive(
    collection: str,
    file_id: str,
    is_definitive: bool = True,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Set or clear the definitive flag on a file (feeds collection summary)."""
    def _run() -> dict[str, Any]:
        from src.file_mgmt.views import set_file_definitive_view

        try:
            return set_file_definitive_view(
                collection, file_id, is_definitive=is_definitive
            )
        except HTTPException as exc:
            return _http_err(exc)
        except Exception as exc:
            logger.exception("set_file_definitive failed")
            return err(str(exc))

    return await _await_result(_run)
