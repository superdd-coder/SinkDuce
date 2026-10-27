"""TDD tests for Meeting model field migration.

Tests the migration from singular fields (allocated_collection, allocated_file_id)
to plural list fields (allocated_collections, allocated_file_ids) with backward
compatibility for old meta.json files.

Run: python -m pytest tests/test_meeting_model.py -x -v
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.meeting.models import Meeting, MeetingStatus


# ═══════════════════════════════════════════════════════════════
# 1. New list fields - creation
# ═══════════════════════════════════════════════════════════════


class TestNewListFields:
    """Meetings can be created with the new plural list fields."""

    def test_default_allocated_collections_is_empty_list(self):
        """allocated_collections defaults to an empty list, not None."""
        m = Meeting()
        assert m.allocated_collections == []
        assert isinstance(m.allocated_collections, list)

    def test_default_allocated_file_ids_is_empty_list(self):
        """allocated_file_ids defaults to an empty list, not None."""
        m = Meeting()
        assert m.allocated_file_ids == []
        assert isinstance(m.allocated_file_ids, list)

    def test_create_meeting_with_list_fields(self):
        """Meeting accepts list values for the new plural fields."""
        m = Meeting(
            allocated_collections=["col_a", "col_b"],
            allocated_file_ids=["file_1.md", "file_2.md"],
        )
        assert m.allocated_collections == ["col_a", "col_b"]
        assert m.allocated_file_ids == ["file_1.md", "file_2.md"]

    def test_create_meeting_with_single_item_list(self):
        """A single-item list works correctly."""
        m = Meeting(
            allocated_collections=["my_col"],
            allocated_file_ids=["notes.md"],
        )
        assert m.allocated_collections == ["my_col"]
        assert m.allocated_file_ids == ["notes.md"]


# ═══════════════════════════════════════════════════════════════
# 2. Backward compatibility - old format deserialization
# ═══════════════════════════════════════════════════════════════


class TestBackwardCompatibility:
    """Old meta.json with singular string fields auto-migrates to lists."""

    def test_old_allocated_collection_migrates_to_list(self):
        """Old 'allocated_collection' string becomes single-item list."""
        data = {
            "id": "m1",
            "title": "Old Meeting",
            "allocated_collection": "my_collection",
        }
        m = Meeting(**data)
        assert m.allocated_collections == ["my_collection"]

    def test_old_allocated_file_id_migrates_to_list(self):
        """Old 'allocated_file_id' string becomes single-item list."""
        data = {
            "id": "m2",
            "title": "Old Meeting",
            "allocated_file_id": "meeting_notes.md",
        }
        m = Meeting(**data)
        assert m.allocated_file_ids == ["meeting_notes.md"]

    def test_both_old_fields_migrate(self):
        """Both old singular fields migrate together."""
        data = {
            "id": "m3",
            "title": "Old Meeting",
            "allocated_collection": "col_a",
            "allocated_file_id": "file_a.md",
        }
        m = Meeting(**data)
        assert m.allocated_collections == ["col_a"]
        assert m.allocated_file_ids == ["file_a.md"]

    def test_old_none_collection_migrates_to_empty_list(self):
        """Old 'allocated_collection': None becomes empty list."""
        data = {
            "id": "m4",
            "title": "Old Meeting",
            "allocated_collection": None,
        }
        m = Meeting(**data)
        assert m.allocated_collections == []

    def test_old_none_file_id_migrates_to_empty_list(self):
        """Old 'allocated_file_id': None becomes empty list."""
        data = {
            "id": "m5",
            "title": "Old Meeting",
            "allocated_file_id": None,
        }
        m = Meeting(**data)
        assert m.allocated_file_ids == []

    def test_old_empty_string_migrates_to_empty_list(self):
        """Old 'allocated_collection': '' (empty string) becomes empty list."""
        data = {
            "id": "m6",
            "title": "Old Meeting",
            "allocated_collection": "",
            "allocated_file_id": "",
        }
        m = Meeting(**data)
        assert m.allocated_collections == []
        assert m.allocated_file_ids == []


# ═══════════════════════════════════════════════════════════════
# 3. New format deserialization
# ═══════════════════════════════════════════════════════════════


class TestNewFormatDeserialization:
    """New meta.json with list fields deserializes directly."""

    def test_new_list_format_deserializes(self):
        """New format with list fields is accepted directly."""
        data = {
            "id": "n1",
            "title": "New Meeting",
            "allocated_collections": ["col_x", "col_y"],
            "allocated_file_ids": ["file_x.md"],
        }
        m = Meeting(**data)
        assert m.allocated_collections == ["col_x", "col_y"]
        assert m.allocated_file_ids == ["file_x.md"]

    def test_new_format_with_both_old_and_new_fields_prefers_new(self):
        """When both old and new fields present, new fields take precedence."""
        data = {
            "id": "n2",
            "title": "Mixed",
            "allocated_collection": "old_col",
            "allocated_collections": ["new_col"],
            "allocated_file_id": "old.md",
            "allocated_file_ids": ["new.md"],
        }
        m = Meeting(**data)
        # New fields should take precedence when both are present
        assert m.allocated_collections == ["new_col"]
        assert m.allocated_file_ids == ["new.md"]


# ═══════════════════════════════════════════════════════════════
# 4. Serialization - new field names in output
# ═══════════════════════════════════════════════════════════════


class TestSerialization:
    """model_dump() uses the new plural field names and excludes old fields."""

    def test_model_dump_uses_new_field_names(self):
        """model_dump() contains 'allocated_collections' and 'allocated_file_ids'."""
        m = Meeting(
            allocated_collections=["col_a"],
            allocated_file_ids=["file.md"],
        )
        data = m.model_dump()
        assert "allocated_collections" in data
        assert "allocated_file_ids" in data
        assert data["allocated_collections"] == ["col_a"]
        assert data["allocated_file_ids"] == ["file.md"]

    def test_model_dump_excludes_old_field_names(self):
        """model_dump() does NOT contain old 'allocated_collection' or 'allocated_file_id'."""
        m = Meeting(
            allocated_collections=["col_a"],
            allocated_file_ids=["file.md"],
        )
        data = m.model_dump()
        assert "allocated_collection" not in data
        assert "allocated_file_id" not in data

    def test_model_dump_default_meeting_has_empty_lists(self):
        """Default Meeting serializes with empty list fields."""
        m = Meeting()
        data = m.model_dump()
        assert data["allocated_collections"] == []
        assert data["allocated_file_ids"] == []

    def test_round_trip_serialization(self):
        """Meeting survives dump -> JSON -> reconstruct cycle."""
        m = Meeting(
            id="rt1",
            title="Round Trip",
            allocated_collections=["col_a", "col_b"],
            allocated_file_ids=["file_1.md", "file_2.md"],
            status=MeetingStatus.completed,
            created_at=datetime(2026, 6, 10, 10, 0, 0),
            updated_at=datetime(2026, 6, 10, 11, 0, 0),
        )
        data = m.model_dump()
        data["created_at"] = data["created_at"].isoformat()
        data["updated_at"] = data["updated_at"].isoformat()

        json_str = json.dumps(data)
        loaded = json.loads(json_str)

        # Simulate _dict_to_meeting
        loaded["created_at"] = datetime.fromisoformat(loaded["created_at"])
        loaded["updated_at"] = datetime.fromisoformat(loaded["updated_at"])
        restored = Meeting(**loaded)

        assert restored.allocated_collections == ["col_a", "col_b"]
        assert restored.allocated_file_ids == ["file_1.md", "file_2.md"]
        assert restored.id == "rt1"

    def test_round_trip_from_old_json_format(self):
        """Old meta.json format (with string fields) round-trips through new model."""
        old_meta = {
            "id": "old_rt",
            "title": "Old Format",
            "status": "completed",
            "allocated_collection": "my_db",
            "allocated_file_id": "notes.md",
            "created_at": "2026-06-01T10:00:00",
            "updated_at": "2026-06-01T11:00:00",
        }
        json_str = json.dumps(old_meta)
        loaded = json.loads(json_str)

        loaded["created_at"] = datetime.fromisoformat(loaded["created_at"])
        loaded["updated_at"] = datetime.fromisoformat(loaded["updated_at"])
        m = Meeting(**loaded)

        assert m.allocated_collections == ["my_db"]
        assert m.allocated_file_ids == ["notes.md"]

        # Re-serialize and verify it uses new format
        data = m.model_dump()
        data["created_at"] = data["created_at"].isoformat()
        data["updated_at"] = data["updated_at"].isoformat()
        assert "allocated_collections" in data
        assert "allocated_collection" not in data
        assert data["allocated_collections"] == ["my_db"]
