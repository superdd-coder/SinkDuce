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
