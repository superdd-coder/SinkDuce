"""File-management facade — implementations live in sibling modules.

External callers keep ``from src.file_mgmt.service import …`` /
``from src.file_mgmt import service``.
"""

from __future__ import annotations

from src.file_mgmt.access import (  # noqa: F401
    _actor_for,
    _actor_id,
    _main_chain_id,
    _now_iso,
    _open_db,
    _validate_collection,
)
from src.file_mgmt.files import (  # noqa: F401
    MAX_VERSIONS,
    _attachment_display_fields,
    _load_file_index,
    _purge_file_sqlite_rows,
    add_file_path,
    attach_file_to_node,
    build_library_tree,
    delete_file,
    delete_file_version,
    demote_file_path,
    detach_file_from_node,
    get_file_detail,
    list_archived_files,
    list_files,
    list_files_in_folder,
    list_files_with_mounts,
    list_old_versions,
    promote_file_path,
    register_ingested_source_file,
    remove_file_path,
    rollback_file_version,
    toggle_archive,
    unregister_files_for_source,
    update_file,
    upload_file_to_folder,
    upload_file_version,
    upload_folder,
)
from src.file_mgmt.folders import (  # noqa: F401
    create_folder,
    delete_folder,
    get_folder,
    get_folder_tree,
    list_folders,
    toggle_folder_archive,
    update_folder,
)
from src.file_mgmt.layout import suggest_unique_name  # noqa: F401
from src.file_mgmt.messages import (  # noqa: F401
    _purge_orphan_messages,
    _row_to_message,
    create_message,
    delete_message,
    list_folder_messages,
    list_messages,
    list_root_messages,
    update_message,
)
from src.file_mgmt.store import COLLECTIONS_DIR  # noqa: F401
from src.file_mgmt.timeline import (  # noqa: F401
    build_timeline,
    create_chain,
    create_group,
    create_node,
    delete_chain,
    delete_group,
    delete_meeting_anchor_if_empty,
    delete_node,
    end_chain,
    ensure_meeting_anchor_node,
    get_node_by_external_ref,
    get_node_detail,
    list_chains,
    list_groups,
    list_nodes,
    list_nodes_by_external_ref,
    meeting_external_ref,
    reopen_chain,
    reorder_node,
    update_chain,
    update_group,
    update_node,
)
from src.file_mgmt.todos import (  # noqa: F401
    create_todo,
    delete_todo,
    get_todo,
    link_todo_node,
    list_todos,
    update_todo,
)
