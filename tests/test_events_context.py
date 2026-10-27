"""Request-scoped client identity for event origin attribution."""

from __future__ import annotations

from src.events.context import (
    CLIENT_ID_HEADER,
    CLIENT_ID_QUERY,
    current_client,
    reset_client,
    set_client,
)


class TestClientContext:
    def test_default_is_empty(self):
        ctx = current_client()
        assert ctx.client_id is None
        assert ctx.key_id is None
        assert ctx.as_origin() == {}

    def test_set_binds_both_ids(self):
        token = set_client("c-1", "k-1")
        try:
            ctx = current_client()
            assert ctx.client_id == "c-1"
            assert ctx.key_id == "k-1"
            assert ctx.as_origin() == {"client_id": "c-1", "key_id": "k-1"}
        finally:
            reset_client(token)

    def test_reset_restores_previous(self):
        token = set_client("c-1", "k-1")
        reset_client(token)
        assert current_client().client_id is None

    def test_origin_omits_unknown_fields(self):
        # A local keyless request has no key id; don't put a null in the envelope.
        token = set_client("c-1", None)
        try:
            assert current_client().as_origin() == {"client_id": "c-1"}
        finally:
            reset_client(token)

    def test_header_and_query_names_are_stable(self):
        # The frontend and the middleware must agree on these spellings.
        assert CLIENT_ID_HEADER == "x-client-id"
        assert CLIENT_ID_QUERY == "client"
