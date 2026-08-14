"""SP-03: MeetingService stream / translation / allocate live in mixins."""

from __future__ import annotations


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
