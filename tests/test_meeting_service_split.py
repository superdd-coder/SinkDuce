"""SP-03: MeetingService stream / translation / allocate live in mixins."""

from __future__ import annotations


def test_carry_general_allocation_marks_reingest():
    from src.meeting.generation import build_general_tab, carry_general_tab_allocation

    assert carry_general_tab_allocation([]) == {}
    assert carry_general_tab_allocation([{"tab_id": "t1", "allocated_file_id": "x"}]) == {}

    old = [{
        "tab_id": "tab_general",
        "allocated_file_id": "file1",
        "associated_collection_id": "col1",
        "associated_collection_name": "Alpha",
        "allocated_chain_id": "ch1",
        "ingested_content_hash": "abc",
        "todo_candidates": [{"id": "c1"}],
    }]
    carried = carry_general_tab_allocation(old)
    assert carried["allocated_file_id"] == "file1"
    assert carried["associated_collection_id"] == "col1"
    assert carried["needs_reingest"] is True
    assert carried["todo_candidates"] == [{"id": "c1"}]

    tab = build_general_tab("/tmp/tab_general.md", old)
    assert tab["tab_id"] == "tab_general"
    assert tab["name"] == "General"
    assert tab["allocated_file_id"] == "file1"
    assert tab["needs_reingest"] is True
    assert tab["md_file_path"] == "/tmp/tab_general.md"


def test_single_entity_blueprint_is_dropped():
    from src.meeting.generation import keep_split_blueprint

    one = [{"blueprint_id": "bp_01", "tab_name": "Entity A"}]
    two = [
        {"blueprint_id": "bp_01", "tab_name": "Entity A"},
        {"blueprint_id": "bp_02", "tab_name": "Entity B"},
    ]
    assert keep_split_blueprint([]) == []
    assert keep_split_blueprint(one) == []
    assert keep_split_blueprint(two) == two


def test_generation_methods_live_on_generation_mixin():
    from src.meeting import generation, service

    for name in (
        "generate_blueprint_stream",
        "_do_blueprint_summary",
        "generate_section_stream",
        "_persist_section_done",
        "_persist_section_idle",
    ):
        assert getattr(service.MeetingService, name) is getattr(
            generation.MeetingGenerationMixin, name
        )


def test_translation_methods_live_on_translation_mixin():
    from src.meeting import service, translation

    assert service._TranslationStream is translation._TranslationStream
    for name in (
        "list_summary_translations",
        "list_active_translations",
        "generate_translation_stream",
        "_start_translation_thread",
    ):
        assert getattr(service.MeetingService, name) is getattr(
            translation.MeetingTranslationMixin, name
        )


def test_allocate_methods_live_on_allocate_mixin():
    from src.meeting import allocate, service

    for name in (
        "_delete_allocation",
        "cleanup_meeting_allocations",
        "_managed_file_exists",
        "allocate_section_to_collection",
        "delete_section_allocation",
    ):
        assert getattr(service.MeetingService, name) is getattr(
            allocate.MeetingAllocateMixin, name
        )
