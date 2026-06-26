"""CollectionCatalog tests — cooling/dirty state machine."""

import threading
import pytest
from unittest.mock import MagicMock, patch

from src.rag.catalog import CollectionCatalog, CatalogEntry


def _make_catalog(db=None, llm=None):
    db = db or MagicMock()
    llm = llm or MagicMock()
    cat = CollectionCatalog(db=db, llm=llm)
    cat._collect_file_infos = MagicMock(return_value=[
        {"filename": "f1.pdf", "chunk0_text": "opening text 1"},
        {"filename": "f2.pdf", "chunk0_text": "opening text 2"},
    ])
    cat._count_active_upload_tasks = MagicMock(return_value=0)
    return cat


class TestCatalogBasic:
    def test_get_all_catalogs(self):
        db = MagicMock()
        db.list_collections.return_value = ["col_a", "col_b"]
        db.get_collection_config.side_effect = [
            {"project_description": "Project A"},
            {"project_description": "Project B"},
        ]
        cat = CollectionCatalog(db, MagicMock())
        entries = cat.get_catalog()
        assert len(entries) == 2

    def test_get_filtered_catalogs(self):
        db = MagicMock()
        db.get_collection_config.return_value = {"project_description": "Only A"}
        cat = CollectionCatalog(db, MagicMock())
        entries = cat.get_catalog(["col_A"])
        assert len(entries) == 1

    def test_catalog_entry_fields(self):
        db = MagicMock()
        db.get_collection_config.return_value = {
            "project_description": "Test", "coverage": "Financial reports", "tags": ["f"],
        }
        cat = CollectionCatalog(db, MagicMock())
        e = cat.get_catalog(["x"])[0]
        assert e.coverage == "Financial reports"
        assert e.tags == ["f"]

    def test_tags_initially_empty(self):
        db = MagicMock()
        db.get_collection_config.return_value = {"project_description": "P"}
        cat = CollectionCatalog(db, MagicMock())
        assert cat.get_catalog(["x"])[0].tags == []

    def test_update_tags(self):
        db = MagicMock()
        cat = CollectionCatalog(db, MagicMock())
        cat.update_tags("col_A", ["finance"])
        db.update_collection_config.assert_called_once_with("col_A", {"tags": ["finance"]})


class TestStateMachine:
    """Cooling / dirty state machine."""

    def test_first_call_generates_immediately(self):
        """No cooling, no active tasks → generate now."""
        db = MagicMock()
        db.get_collection_config.return_value = {"coverage": ""}
        db.update_collection_config.return_value = {}
        llm = MagicMock()
        llm.generate.return_value = "Project introduction"

        cat = CollectionCatalog(db, llm)
        cat._collect_file_infos = MagicMock(return_value=[
            {"filename": "f1.pdf", "chunk0_text": "text"},
        ])
        cat._count_active_upload_tasks = MagicMock(return_value=0)

        cat.update_coverage("col_a")
        assert llm.generate.call_count == 1
        assert cat._cooling["col_a"] is True  # entered cooling

    def test_second_call_during_cooling_marks_dirty(self):
        """If cooling, just mark dirty — don't call LLM."""
        db = MagicMock()
        db.get_collection_config.return_value = {"coverage": ""}
        llm = MagicMock()
        llm.generate.return_value = "Intro"

        cat = CollectionCatalog(db, llm)
        cat._collect_file_infos = MagicMock(return_value=[
            {"filename": "f1.pdf", "chunk0_text": "text"},
        ])
        cat._count_active_upload_tasks = MagicMock(return_value=0)

        cat.update_coverage("col_a")  # 1st → generate
        assert llm.generate.call_count == 1

        cat.update_coverage("col_a")  # 2nd → cooling → dirty
        assert llm.generate.call_count == 1  # still 1
        assert cat._dirty["col_a"] is True

    def test_timer_fires_with_dirty_regenerates(self):
        """Timer fires, dirty=true, no active tasks → regenerate."""
        db = MagicMock()
        db.get_collection_config.return_value = {"coverage": ""}
        llm = MagicMock()
        llm.generate.return_value = "Intro"

        cat = CollectionCatalog(db, llm)
        cat._collect_file_infos = MagicMock(return_value=[
            {"filename": "f1.pdf", "chunk0_text": "text"},
        ])
        cat._count_active_upload_tasks = MagicMock(return_value=0)
        cat._debounce_seconds = 0.1  # fast timer for test

        cat.update_coverage("col_a")  # generate + enter cooling
        assert llm.generate.call_count == 1

        cat.update_coverage("col_a")  # mark dirty
        assert cat._dirty["col_a"] is True

        # Wait for timer to fire
        import time as _time
        _time.sleep(0.3)

        # Timer should have triggered regeneration
        assert llm.generate.call_count == 2
        assert cat._dirty.get("col_a") is False

    def test_timer_fires_clean_does_nothing(self):
        """Timer fires, dirty=false → nothing."""
        db = MagicMock()
        db.get_collection_config.return_value = {"coverage": ""}
        llm = MagicMock()
        llm.generate.return_value = "Intro"

        cat = CollectionCatalog(db, llm)
        cat._collect_file_infos = MagicMock(return_value=[
            {"filename": "f1.pdf", "chunk0_text": "text"},
        ])
        cat._count_active_upload_tasks = MagicMock(return_value=0)
        cat._debounce_seconds = 0.1

        cat.update_coverage("col_a")  # generate + cool
        assert llm.generate.call_count == 1

        import time as _time
        _time.sleep(0.3)

        # Timer fired, no dirty → still 1
        assert llm.generate.call_count == 1

    def test_after_cooling_generate_immediately(self):
        """Cooling ended, dirty=false, then new call → generate immediately."""
        db = MagicMock()
        db.get_collection_config.return_value = {"coverage": ""}
        llm = MagicMock()
        llm.generate.return_value = "Intro"

        cat = CollectionCatalog(db, llm)
        cat._collect_file_infos = MagicMock(return_value=[
            {"filename": "f1.pdf", "chunk0_text": "text"},
        ])
        cat._count_active_upload_tasks = MagicMock(return_value=0)
        cat._debounce_seconds = 0.1

        cat.update_coverage("col_a")  # generate + cool
        assert llm.generate.call_count == 1

        import time as _time
        _time.sleep(0.3)  # timer fires, dirty=false

        cat.update_coverage("col_a")  # not cooling → generate immediately
        assert llm.generate.call_count == 2

    def test_active_tasks_during_cooling_defers(self):
        """During cooling with active upload tasks → mark dirty, wait."""
        db = MagicMock()
        db.get_collection_config.return_value = {"coverage": ""}
        llm = MagicMock()
        llm.generate.return_value = "Intro"

        cat = CollectionCatalog(db, llm)
        cat._collect_file_infos = MagicMock(return_value=[
            {"filename": "f1.pdf", "chunk0_text": "text"},
        ])
        cat._count_active_upload_tasks = MagicMock(return_value=0)
        cat._debounce_seconds = 60

        cat.update_coverage("col_a")  # generate + cool
        assert llm.generate.call_count == 1

        # Now simulate: active tasks are running
        cat._count_active_upload_tasks.return_value = 3
        cat.update_coverage("col_a")  # cooling → dirty
        assert cat._dirty["col_a"] is True

        # Clean up timer
        for t in list(cat._timers.values()):
            t.cancel()

    def test_caller_responsible_for_task_check(self):
        """Catalog trusts the caller to check active tasks before calling.
        The caller (handlers.py) does `remaining = active - 1` to decide
        whether to call.  update_coverage itself always generates when
        not cooling."""
        db = MagicMock()
        db.get_collection_config.return_value = {"coverage": ""}
        llm = MagicMock()
        llm.generate.return_value = "Intro"

        cat = CollectionCatalog(db, llm)
        cat._collect_file_infos = MagicMock(return_value=[
            {"filename": "f1.pdf", "chunk0_text": "text"},
        ])
        cat._count_active_upload_tasks = MagicMock(return_value=5)
        cat._debounce_seconds = 60

        cat.update_coverage("col_a")  # not cooling → generate (caller already checked)
        assert llm.generate.call_count == 1  # generates immediately

        for t in list(cat._timers.values()):
            t.cancel()


class TestCoverageLogic:
    def test_empty_collection_clears_coverage(self):
        db = MagicMock()
        db.get_collection_config.return_value = {"coverage": "old"}
        llm = MagicMock()

        cat = CollectionCatalog(db, llm)
        cat._collect_file_infos = MagicMock(return_value=[])
        cat._count_active_upload_tasks = MagicMock(return_value=0)

        cat.update_coverage("col_a")
        llm.generate.assert_not_called()
        db.update_collection_config.assert_called_once()
        assert db.update_collection_config.call_args[0][1]["coverage"] == ""

    def test_summary_field_preferred(self):
        """_get_chunk0 prefers chunk.metadata.summary over raw text."""
        db = MagicMock()
        db.get_collection_config.return_value = {"coverage": ""}
        llm = MagicMock()
        llm.generate.return_value = "Financial reports"

        cat = CollectionCatalog(db, llm)
        # Simulate enrichment: chunk 0 has a summary field
        cat._get_chunk0 = MagicMock(return_value="Q4 financial report summary")
        cat._list_sources = MagicMock(return_value=["report.pdf"])
        cat._count_active_upload_tasks = MagicMock(return_value=0)

        cat.update_coverage("col_a")
        prompt = llm.generate.call_args[0][0]
        assert "Q4 financial report summary" in prompt

    def test_prompt_classifies_type_not_topic(self):
        """Prompt asks for document TYPE, not topic."""
        db = MagicMock()
        db.get_collection_config.return_value = {"coverage": ""}
        llm = MagicMock()
        llm.generate.return_value = "Meeting notes"

        cat = CollectionCatalog(db, llm)
        cat._collect_file_infos = MagicMock(return_value=[
            {"filename": "notes.md", "chunk0_text": "Weekly sync discussion about biogas plant"},
        ])
        cat._count_active_upload_tasks = MagicMock(return_value=0)

        cat.update_coverage("col_a")
        prompt = llm.generate.call_args[0][0]
        assert "TYPE" in prompt
        assert "TOPIC" in prompt
        assert "WRONG" in prompt

    def test_truncation_safety_net(self):
        db = MagicMock()
        db.get_collection_config.return_value = {"coverage": ""}
        db.update_collection_config.return_value = {}
        llm = MagicMock()
        llm.generate.return_value = "A" * 100

        cat = CollectionCatalog(db, llm)
        cat._collect_file_infos = MagicMock(return_value=[
            {"filename": "f1.pdf", "chunk0_text": "text"},
        ])
        cat._count_active_upload_tasks = MagicMock(return_value=0)

        cat.update_coverage("col_a")
        saved = db.update_collection_config.call_args[0][1]["coverage"]
        assert len(saved) <= 50


class TestCoverageConcurrent:
    def test_concurrent_calls(self):
        db = MagicMock()
        db.get_collection_config.return_value = {"coverage": ""}
        db.update_collection_config.return_value = {}
        llm = MagicMock()
        llm.generate.return_value = "Coverage"

        cat = CollectionCatalog(db, llm)
        cat._collect_file_infos = MagicMock(return_value=[
            {"filename": "f1.pdf", "chunk0_text": "text"},
        ])
        cat._count_active_upload_tasks = MagicMock(return_value=0)
        cat._debounce_seconds = 60

        errors = []

        def call():
            try:
                cat.update_coverage("col_a")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=call) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert llm.generate.call_count >= 1

        for t in list(cat._timers.values()):
            t.cancel()
