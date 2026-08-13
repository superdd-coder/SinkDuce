"""SP-02: file_mgmt.service is a facade; first slice is access + todos."""

from __future__ import annotations


def test_access_helpers_are_the_same_objects_on_service():
    from src.file_mgmt import access, service

    assert service._open_db is access._open_db
    assert service._now_iso is access._now_iso
    assert service._actor_for is access._actor_for
    assert service._actor_id is access._actor_id
    assert service._validate_collection is access._validate_collection
    assert service._main_chain_id is access._main_chain_id


def test_todo_crud_is_defined_in_todos_module():
    from src.file_mgmt import service, todos

    for name in (
        "list_todos",
        "create_todo",
        "update_todo",
        "delete_todo",
        "link_todo_node",
    ):
        assert getattr(service, name) is getattr(todos, name)


def test_message_crud_is_defined_in_messages_module():
    from src.file_mgmt import messages, service

    for name in (
        "create_message",
        "update_message",
        "delete_message",
        "list_messages",
        "list_root_messages",
        "list_folder_messages",
        "_row_to_message",
        "_purge_orphan_messages",
    ):
        assert getattr(service, name) is getattr(messages, name)


def test_folders_timeline_files_are_facade_reexports():
    from src.file_mgmt import files, folders, service, timeline

    mapping = {
        folders: (
            "list_folders",
            "get_folder",
            "get_folder_tree",
            "create_folder",
            "update_folder",
            "delete_folder",
        ),
        timeline: (
            "list_groups",
            "create_group",
            "list_chains",
            "create_chain",
            "build_timeline",
            "create_node",
            "end_chain",
            "ensure_meeting_anchor_node",
        ),
        files: (
            "get_file_detail",
            "list_files",
            "build_library_tree",
            "upload_file_to_folder",
            "attach_file_to_node",
            "toggle_archive",
            "register_ingested_source_file",
            "unregister_files_for_source",
            "_load_file_index",
            "_attachment_display_fields",
            "_purge_file_sqlite_rows",
        ),
    }
    for mod, names in mapping.items():
        for name in names:
            assert getattr(service, name) is getattr(mod, name), name
