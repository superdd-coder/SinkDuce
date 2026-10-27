"""Emission coverage: every declared event type must route to a real domain.

This is the guard the design calls for in §5: the domain table is the single
authority for type → frontend surface, so a type that is emitted but absent from
the table would silently refetch nothing. Pinning the catalog to the table turns
that class of typo into a test failure.
"""

from __future__ import annotations

from src.events.models import DOMAIN_BY_TYPE, resolve_domain

KNOWN_DOMAINS = {
    "files",
    "timeline",
    "sessions",
    "meetings",
    "notes",
    "collections",
    "hot_words",
    "people",
    "config",
    "tasks",
}


class TestCatalogCoverage:
    def test_every_declared_event_type_has_a_domain(self):
        from src.file_mgmt.events import EVENT_TYPES

        missing = sorted(t for t in EVENT_TYPES if t not in DOMAIN_BY_TYPE)
        assert missing == [], f"event types with no domain: {missing}"

    def test_every_declared_event_type_resolves_to_a_known_domain(self):
        from src.file_mgmt.events import EVENT_TYPES

        bad = {
            t: resolve_domain(t)
            for t in sorted(EVENT_TYPES)
            if resolve_domain(t) not in KNOWN_DOMAINS
        }
        assert bad == {}

    def test_domain_table_has_no_entries_that_are_not_declared(self):
        # A stale table entry means a removed event still appears routable.
        from src.file_mgmt.events import EVENT_TYPES

        extra = sorted(t for t in DOMAIN_BY_TYPE if t not in EVENT_TYPES)
        assert extra == [], f"table entries not in the event catalog: {extra}"

    def test_unknown_type_falls_back_rather_than_misroutes(self):
        assert resolve_domain("totally.unknown") == "unknown"
