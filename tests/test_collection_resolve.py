"""Chat-side collection name→ID resolution for create_note."""

from __future__ import annotations

import pytest

from src.chatbox import collection_resolve


DIRECTORY = [
    {"id": "col_helios", "name": "Helios"},
    {"id": "default", "name": "Default"},
    {"id": "col_projx", "name": "Project X"},
]


@pytest.fixture
def fixed_directory(monkeypatch):
    monkeypatch.setattr(
        collection_resolve, "build_collection_directory", lambda: list(DIRECTORY)
    )


@pytest.mark.usefixtures("fixed_directory")
class TestResolveCollection:
    def test_exact_name_case_insensitive(self):
        out = collection_resolve.resolve_collection("helios")
        assert out["collection"] == "col_helios"
        assert out["collection_name"] == "Helios"
        assert out["match"] == "exact"

    def test_exact_name_strips_whitespace(self):
        out = collection_resolve.resolve_collection("  Helios  ")
        assert out["collection"] == "col_helios"
        assert out["match"] == "exact"

    def test_id_passthrough_wins(self):
        out = collection_resolve.resolve_collection("col_projx")
        assert out["collection"] == "col_projx"
        assert out["match"] == "id"

    def test_id_that_looks_like_a_word(self):
        # "default" is both a plausible name and an ID — ID tier first.
        out = collection_resolve.resolve_collection("default")
        assert out["collection"] == "default"
        assert out["match"] == "id"

    def test_unique_prefix(self):
        out = collection_resolve.resolve_collection("proj")
        assert out["collection"] == "col_projx"
        assert out["collection_name"] == "Project X"
        assert out["match"] == "prefix"

    def test_ambiguous_prefix_lists_candidates(self):
        out = collection_resolve.resolve_collection("col_")
        assert "error" in out
        ids = {c["id"] for c in out["collections"]}
        assert {"col_helios", "col_projx"} <= ids

    def test_fuzzy_typo(self):
        out = collection_resolve.resolve_collection("Helos")
        assert out["collection"] == "col_helios"
        assert out["match"] == "fuzzy"

    def test_no_match_error_includes_directory(self):
        out = collection_resolve.resolve_collection("Atlantis")
        assert "error" in out
        assert out["collections"] == DIRECTORY

    def test_empty_error(self):
        out = collection_resolve.resolve_collection("  ")
        assert "error" in out

    def test_duplicate_names_ambiguous(self):
        monkey_dir = [
            {"id": "col_a1", "name": "Alpha"},
            {"id": "col_a2", "name": "alpha"},
        ]
        collection_resolve.build_collection_directory = lambda: monkey_dir  # type: ignore[method-assign]
        out = collection_resolve.resolve_collection("Alpha")
        assert "error" in out
        assert {c["id"] for c in out["collections"]} == {"col_a1", "col_a2"}


class TestDirectoryBuilder:
    def test_build_from_services(self, monkeypatch):
        import src.services as services_mod

        class _DB:
            def list_collections(self):
                return ["col_x", "__summaries__"]

        monkeypatch.setattr(services_mod.services, "db", _DB(), raising=False)

        from src.collections import store as cstore

        monkeypatch.setattr(
            cstore,
            "collection_display_name",
            lambda cid: {"col_x": "Xray"}.get(cid, cid),
        )

        out = collection_resolve.build_collection_directory()
        assert out == [{"id": "col_x", "name": "Xray"}]
