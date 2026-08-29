"""Shared name checks, row converters, and subtree deletes."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import HTTPException

from src.file_mgmt.models import ChainOut, FolderOut, GroupOut, NodeOut



# === Helpers (DB/identity: src.file_mgmt.access) ===

# ── Name uniqueness (same parent folder / same directory) ──────


def _split_display_name(name: str) -> tuple[str, str]:
    """Split 'report.pdf' → ('report', '.pdf'); 'notes' → ('notes', '')."""
    p = Path(name)
    suffix = p.suffix  # includes leading dot, or ""
    if not suffix:
        return name, ""
    stem = name[: -len(suffix)]
    return stem, suffix


def suggest_unique_name(desired: str, existing: set[str]) -> str:
    """Return desired if free, else 'stem (1).ext', 'stem (2).ext', … (case-insensitive)."""
    desired = (desired or "").strip() or "untitled"
    taken = {n.casefold() for n in existing if n}
    if desired.casefold() not in taken:
        return desired
    stem, ext = _split_display_name(desired)
    # If already ends with " (N)", strip before numbering
    base = stem
    n = 1
    while True:
        candidate = f"{base} ({n}){ext}"
        if candidate.casefold() not in taken:
            return candidate
        n += 1
        if n > 9999:
            return f"{base} ({uuid.uuid4().hex[:6]}){ext}"


def _raise_name_conflict(resource: str, name: str, suggested: str) -> None:
    raise HTTPException(
        status_code=409,
        detail={
            "code": "name_conflict",
            "resource": resource,
            "name": name,
            "suggested_name": suggested,
            "message": f"A {resource} named '{name}' already exists here.",
        },
    )


def _sibling_folder_names(
    conn,
    parent_folder_id: str | None,
    *,
    exclude_folder_id: str | None = None,
) -> set[str]:
    if parent_folder_id is None or parent_folder_id == "":
        rows = conn.execute(
            "SELECT folder_id, name FROM folders "
            "WHERE parent_folder_id IS NULL OR parent_folder_id = ''"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT folder_id, name FROM folders WHERE parent_folder_id=?",
            (parent_folder_id,),
        ).fetchall()
    names: set[str] = set()
    for r in rows:
        if exclude_folder_id and r["folder_id"] == exclude_folder_id:
            continue
        if r["name"]:
            names.add(r["name"])
    return names


def _assert_folder_name_free(
    conn,
    parent_folder_id: str | None,
    name: str,
    *,
    exclude_folder_id: str | None = None,
) -> None:
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "Folder name is required")
    existing = _sibling_folder_names(
        conn, parent_folder_id, exclude_folder_id=exclude_folder_id
    )
    if name.casefold() in {n.casefold() for n in existing}:
        _raise_name_conflict(
            "folder", name, suggest_unique_name(name, existing)
        )


def _file_display_names_in_folder(
    conn,
    folder_id: str | None,
    *,
    exclude_file_id: str | None = None,
) -> set[str]:
    """Current-version filenames of files mounted in this folder.

    ``folder_id=None`` → root orphans (files with no file_paths rows).
    """
    if folder_id is None:
        rows = conn.execute(
            """SELECT f.file_id, fv.storage_file_id AS filename
               FROM files f
               LEFT JOIN file_versions fv ON fv.version_id = f.current_version_id
               WHERE f.file_id NOT IN (SELECT file_id FROM file_paths)"""
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT f.file_id, fv.storage_file_id AS filename
               FROM file_paths fp
               JOIN files f ON f.file_id = fp.file_id
               LEFT JOIN file_versions fv ON fv.version_id = f.current_version_id
               WHERE fp.folder_id=?""",
            (folder_id,),
        ).fetchall()
    names: set[str] = set()
    for r in rows:
        if exclude_file_id and r["file_id"] == exclude_file_id:
            continue
        fn = r["filename"]
        if fn:
            # basename only (upload-folder may store relative paths)
            names.add(Path(fn).name)
    return names


def _assert_file_name_free(
    conn,
    folder_id: str | None,
    filename: str,
    *,
    exclude_file_id: str | None = None,
) -> str:
    """Return basename; raise name_conflict if taken in folder (or root)."""
    base = Path((filename or "").strip() or "unnamed").name
    existing = _file_display_names_in_folder(
        conn, folder_id, exclude_file_id=exclude_file_id
    )
    if base.casefold() in {n.casefold() for n in existing}:
        _raise_name_conflict(
            "file", base, suggest_unique_name(base, existing)
        )
    return base


def _row_to_folder(row) -> FolderOut:
    archived = False
    try:
        archived = bool(row["archived"])
    except (KeyError, IndexError):
        archived = False
    return FolderOut(
        folder_id=row["folder_id"],
        parent_folder_id=row["parent_folder_id"],
        name=row["name"],
        kind=row["kind"],
        is_system=bool(row["is_system"]),
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
        icon_type=_row_icon(row, "icon_type"),
        icon_value=_row_icon(row, "icon_value"),
        icon_color=_row_icon(row, "icon_color"),
        archived=archived,
    )


def _row_icon(row, key: str) -> str | None:
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _row_to_group(row, node_count: int = 0, is_system: bool = False) -> GroupOut:
    return GroupOut(
        group_id=row["group_id"],
        folder_id=row["folder_id"],
        name=row["name"],
        description=row["description"],
        created_by=row["created_by"],
        node_count=node_count,
        icon_type=_row_icon(row, "icon_type"),
        icon_value=_row_icon(row, "icon_value"),
        icon_color=_row_icon(row, "icon_color"),
        is_system=is_system,
    )


def _row_to_chain(row, has_end_node: bool = False, node_count: int = 0) -> ChainOut:
    # merge_node_id may be missing on very old Row objects before backfill
    try:
        merge_node_id = row["merge_node_id"]
    except (KeyError, IndexError):
        merge_node_id = None
    return ChainOut(
        chain_id=row["chain_id"],
        parent_chain_id=row["parent_chain_id"],
        parent_node_id=row["parent_node_id"],
        folder_id=row["folder_id"],
        title=row["title"],
        created_by=row["created_by"],
        is_main=row["parent_chain_id"] is None,
        has_end_node=has_end_node,
        node_count=node_count,
        merge_node_id=merge_node_id,
    )


def _row_to_node(row) -> NodeOut:
    keys = set(row.keys()) if hasattr(row, "keys") else set()
    ext = row["external_ref"] if "external_ref" in keys else None
    return NodeOut(
        node_id=row["node_id"],
        chain_id=row["chain_id"],
        group_id=row["group_id"],
        node_type=row["node_type"],
        title=row["title"],
        order=row["order"],
        event_time=row["event_time"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        version=row["version"],
        has_definitive_file=False,
        external_ref=ext,
    )


def _is_descendant(conn, folder_id: str, candidate_parent: str) -> bool:
    """True if candidate_parent lives inside the subtree of folder_id."""
    current = candidate_parent
    visited: set[str] = set()
    while current and current not in visited:
        if current == folder_id:
            return True
        visited.add(current)
        row = conn.execute(
            "SELECT parent_folder_id FROM folders WHERE folder_id=?",
            (current,),
        ).fetchone()
        if not row:
            return False
        current = row["parent_folder_id"]
    return False


def _delete_folder_subtree(conn, folder_id: str) -> None:
    """Delete a folder and all descendants, with kind-specific cleanup."""
    folder = conn.execute(
        "SELECT * FROM folders WHERE folder_id=?", (folder_id,)
    ).fetchone()
    if not folder:
        return

    children = conn.execute(
        "SELECT folder_id FROM folders WHERE parent_folder_id=?",
        (folder_id,),
    ).fetchall()
    for child in children:
        _delete_folder_subtree(conn, child["folder_id"])

    kind = folder["kind"]
    if kind == "user_group":
        conn.execute(
            "UPDATE nodes SET group_id=NULL WHERE group_id IN "
            "(SELECT group_id FROM node_groups WHERE folder_id=?)",
            (folder_id,),
        )
        conn.execute(
            "DELETE FROM node_groups WHERE folder_id=?", (folder_id,)
        )
        # Same residual cleanup as plain folders — previously missing, left
        # orphan folder messages that only showed under Nested at root with
        # blank source tags (no folder row to resolve the name).
        conn.execute("DELETE FROM file_paths WHERE folder_id=?", (folder_id,))
        conn.execute(
            "DELETE FROM messages WHERE owner_type='folder' AND owner_id=?",
            (folder_id,),
        )
        conn.execute(
            "DELETE FROM folders WHERE folder_id=?", (folder_id,)
        )
    elif kind == "branch":
        chain = conn.execute(
            "SELECT chain_id FROM chains WHERE folder_id=?", (folder_id,)
        ).fetchone()
        if chain:
            _delete_chain_subtree(conn, chain["chain_id"])
        else:
            conn.execute("DELETE FROM file_paths WHERE folder_id=?", (folder_id,))
            conn.execute(
                "DELETE FROM messages WHERE owner_type='folder' AND owner_id=?",
                (folder_id,),
            )
            conn.execute(
                "DELETE FROM folders WHERE folder_id=?", (folder_id,)
            )
    else:
        conn.execute("DELETE FROM file_paths WHERE folder_id=?", (folder_id,))
        conn.execute(
            "DELETE FROM messages WHERE owner_type='folder' AND owner_id=?",
            (folder_id,),
        )
        conn.execute(
            "DELETE FROM folders WHERE folder_id=?", (folder_id,)
        )


def _clear_node_inbound_fks(conn, node_id: str) -> None:
    """Clear or remove rows that reference *node_id* so DELETE FROM nodes succeeds."""
    # Messages may cite this node as the source of a cross-owner note
    conn.execute(
        "UPDATE messages SET source_node_id=NULL WHERE source_node_id=?",
        (node_id,),
    )
    # Closed branches that merge into this main-chain node → reopen loop
    conn.execute(
        "UPDATE chains SET merge_node_id=NULL WHERE merge_node_id=?",
        (node_id,),
    )


def _purge_node_owned_rows(conn, node_id: str) -> None:
    """Remove rows owned by *node_id* (attachments, derived paths, node messages)."""
    conn.execute("DELETE FROM file_nodes WHERE node_id=?", (node_id,))
    conn.execute("DELETE FROM file_paths WHERE source_node_id=?", (node_id,))
    conn.execute(
        "DELETE FROM messages WHERE owner_type='node' AND owner_id=?",
        (node_id,),
    )
    _clear_node_inbound_fks(conn, node_id)


def _delete_chain_subtree(conn, chain_id: str) -> None:
    """Delete a chain, its sub-chains, nodes, and associated folder."""
    sub_chains = conn.execute(
        "SELECT chain_id FROM chains WHERE parent_chain_id=?", (chain_id,)
    ).fetchall()
    for sc in sub_chains:
        _delete_chain_subtree(conn, sc["chain_id"])

    chain = conn.execute(
        "SELECT folder_id, merge_node_id FROM chains WHERE chain_id=?",
        (chain_id,),
    ).fetchone()

    # If this branch closed onto a merge node on the parent chain, remove that
    # merge node too (same as reopen_chain) after clearing the FK pointer.
    merge_id = None
    if chain is not None:
        try:
            merge_id = chain["merge_node_id"]
        except (KeyError, IndexError):
            merge_id = None
    if merge_id:
        conn.execute(
            "UPDATE chains SET merge_node_id=NULL WHERE chain_id=?",
            (chain_id,),
        )
        # Merge node lives on parent — purge its deps then delete
        _purge_node_owned_rows(conn, merge_id)
        conn.execute("DELETE FROM nodes WHERE node_id=?", (merge_id,))

    # Purge deps for every node on this chain before deleting nodes
    node_rows = conn.execute(
        "SELECT node_id FROM nodes WHERE chain_id=?", (chain_id,)
    ).fetchall()
    for nr in node_rows:
        _purge_node_owned_rows(conn, nr["node_id"])

    conn.execute("DELETE FROM nodes WHERE chain_id=?", (chain_id,))

    # Delete the chains record BEFORE the folder to avoid FK violation
    # (chains.folder_id REFERENCES folders).  Capture folder_id first.
    folder_id = chain["folder_id"] if chain else None
    conn.execute("DELETE FROM chains WHERE chain_id=?", (chain_id,))

    if folder_id:
        # Branch folder may still hold derived paths from start/merge anchors
        # (parent lives on main; paths.source_node_id points at parent, folder_id
        # at this branch). Clear them or DELETE folder hits FOREIGN KEY.
        conn.execute("DELETE FROM file_paths WHERE folder_id=?", (folder_id,))
        conn.execute(
            "DELETE FROM messages WHERE owner_type='folder' AND owner_id=?",
            (folder_id,),
        )
        children = conn.execute(
            "SELECT folder_id FROM folders WHERE parent_folder_id=?",
            (folder_id,),
        ).fetchall()
        for child in children:
            _delete_folder_subtree(conn, child["folder_id"])
        conn.execute(
            "DELETE FROM folders WHERE folder_id=?",
            (folder_id,),
        )
