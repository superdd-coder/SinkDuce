"""Meeting catalog + transcript lookup for Chat / Collection QC / Group / MCP."""

from __future__ import annotations

from unittest.mock import patch

from src.chatbox.query_tools import allowed_tool_names, force_collection_args, tools_for_mode
from src.meeting.models import Meeting
from src.prompts import (
    DEFAULT_SYSTEM_PROMPT,
    MEETING_GROUP_CHAT_SYSTEM_PROMPT,
    QUICK_CHAT_SYSTEM_PROMPT,
)


def _meeting(mid: str, title: str, *, cols: list[str] | None = None, indexed: bool = True):
    return Meeting(
        id=mid,
        title=title,
        allocated_collections=list(cols or []),
        transcript_index_status="ready" if indexed else "building",
    )


class TestToolAllowlists:
    def test_chat_and_quick_expose_catalog_and_lookup(self):
        for mode in ("agentic", "direct"):
            names = {t["function"]["name"] for t in tools_for_mode(mode)}
            assert "list_meeting_catalog" in names
            assert "lookup_meeting_transcript" in names
            assert "read_meeting_summary" in names
            lookup = next(
                t for t in tools_for_mode(mode) if t["function"]["name"] == "lookup_meeting_transcript"
            )
            props = lookup["function"]["parameters"]["properties"]
            assert "meeting_ids" in props

    def test_meeting_qc_lookup_only_no_catalog(self):
        names = {t["function"]["name"] for t in tools_for_mode("direct", is_meeting=True)}
        assert names == {"lookup_meeting_transcript"}
        assert "list_meeting_catalog" not in allowed_tool_names("direct", is_meeting=True)
        lookup = tools_for_mode("direct", is_meeting=True)[0]
        assert "meeting_ids" not in lookup["function"]["parameters"]["properties"]

    def test_group_uses_catalog_and_unified_lookup(self):
        names = {t["function"]["name"] for t in tools_for_mode("direct", is_group=True)}
        assert names == {
            "list_meeting_catalog",
            "lookup_meeting_transcript",
            "read_meeting_summary",
        }
        assert "lookup_group_transcript" not in names
        assert allowed_tool_names("direct", is_group=True) == frozenset(names)

    def test_quick_chat_locks_collection_on_catalog(self):
        args, err = force_collection_args(
            "list_meeting_catalog",
            {"collection": "other"},
            mode="direct",
            forced_collection="col_bound",
        )
        assert err is None
        assert args["collection"] == "col_bound"


class TestVisibleSet:
    def test_collection_scope_only_ingested(self):
        from src.meeting.catalog import visible_meeting_ids

        meetings = [
            _meeting("m1", "In", cols=["col_a"]),
            _meeting("m2", "Out", cols=["col_b"]),
            _meeting("m3", "Also", cols=["col_a", "col_b"]),
        ]
        with patch("src.meeting.store.list_meetings", return_value=meetings):
            assert visible_meeting_ids(collection="col_a") == ["m1", "m3"]

    def test_all_meetings_when_no_collection(self):
        from src.meeting.catalog import visible_meeting_ids

        meetings = [_meeting("m1", "A"), _meeting("m2", "B")]
        with patch("src.meeting.store.list_meetings", return_value=meetings):
            assert visible_meeting_ids() == ["m1", "m2"]

    def test_chat_selected_collections_union_ingested(self):
        from src.meeting.catalog import visible_meeting_ids

        meetings = [
            _meeting("m1", "A", cols=["col_a"]),
            _meeting("m2", "B", cols=["col_b"]),
            _meeting("m3", "C", cols=["col_c"]),
            _meeting("m4", "AB", cols=["col_a", "col_b"]),
        ]
        with patch("src.meeting.store.list_meetings", return_value=meetings):
            assert visible_meeting_ids(collections=["col_a", "col_b"]) == [
                "m1",
                "m2",
                "m4",
            ]

    def test_chat_collection_arg_must_be_in_selected(self):
        from src.meeting.catalog import visible_meeting_ids

        meetings = [
            _meeting("m1", "A", cols=["col_a"]),
            _meeting("m3", "C", cols=["col_c"]),
        ]
        with patch("src.meeting.store.list_meetings", return_value=meetings):
            assert visible_meeting_ids(
                collections=["col_a", "col_b"], collection="col_a"
            ) == ["m1"]
            assert visible_meeting_ids(
                collections=["col_a", "col_b"], collection="col_c"
            ) == []

    def test_lookup_rejects_id_outside_visible_set(self):
        from src.meeting.catalog import select_lookup_targets

        targets, err = select_lookup_targets(["m1", "m2"], ["m9"])
        assert targets is None
        assert err is not None
        assert "m9" in err

    def test_summary_rejects_id_outside_visible_set(self):
        from src.meeting.catalog import read_meeting_summary_json

        meetings = [_meeting("m1", "In", cols=["col_a"])]
        with (
            patch("src.meeting.store.list_meetings", return_value=meetings),
            patch("src.meeting.store.get_meeting", return_value=meetings[0]),
        ):
            import json

            data = json.loads(
                read_meeting_summary_json("m9", collection="col_a")
            )
        assert "error" in data
        assert "m9" in data["error"]

    def test_lookup_omit_ids_keeps_visible_order(self):
        from src.meeting.catalog import select_lookup_targets

        targets, err = select_lookup_targets(["m1", "m2"], None)
        assert err is None
        assert targets == ["m1", "m2"]


class TestSourcesAndPrompts:
    def test_lookup_hits_dedupe_to_one_source_per_meeting(self):
        from src.meeting.catalog import lookup_hits_to_chat_sources

        rows = lookup_hits_to_chat_sources(
            {
                "meetings_searched": ["m1", "m1", "m2"],
                "meeting_titles": {"m1": "Kickoff", "m2": "Follow-up"},
            }
        )
        ids = [r["metadata"]["meeting_id"] for r in rows]
        assert ids == ["m1", "m2"]
        assert all(r["metadata"]["source_type"] == "meeting" for r in rows)
        assert rows[0]["metadata"]["source_label"] == "Kickoff"
        assert rows[0]["metadata"]["id"] == "m1"

    def test_system_prompts_have_no_meeting_directory_dump(self):
        assert "list_meeting_catalog" in DEFAULT_SYSTEM_PROMPT
        assert "read_meeting_summary" in DEFAULT_SYSTEM_PROMPT
        assert "currently selected in Chat" in DEFAULT_SYSTEM_PROMPT
        assert "do not look them up again" in DEFAULT_SYSTEM_PROMPT.lower()
        assert "later call" not in DEFAULT_SYSTEM_PROMPT.lower()
        assert "list_meeting_catalog" in QUICK_CHAT_SYSTEM_PROMPT
        assert "read_meeting_summary" in QUICK_CHAT_SYSTEM_PROMPT
        assert "do not look them up again" in QUICK_CHAT_SYSTEM_PROMPT.lower()
        assert "Each turn a roster" not in MEETING_GROUP_CHAT_SYSTEM_PROMPT
        assert "list_meeting_catalog" in MEETING_GROUP_CHAT_SYSTEM_PROMPT
        assert "search again with a sharper need" not in MEETING_GROUP_CHAT_SYSTEM_PROMPT.lower()


class TestChatSelectedCollectionsWiring:
    def test_scope_kwargs_forward_selected_collections(self):
        from src.chatbox.agent import _meeting_scope_kwargs

        kw = _meeting_scope_kwargs("sess_chat", {}, None, ["col_a", "col_b"])
        assert kw["collection"] is None
        assert kw["collections"] == ["col_a", "col_b"]

        empty = _meeting_scope_kwargs("sess_chat", {}, None, [])
        assert empty["collections"] is None

        qc = _meeting_scope_kwargs("sess_qc", {}, "col_a", ["col_a", "col_b"])
        assert qc == {"collection": "col_a"}

    def test_catalog_run_filters_to_selected_collections(self):
        import json

        from src.chatbox.agent import _run_list_meeting_catalog

        meetings = [
            _meeting("m1", "A", cols=["col_a"]),
            _meeting("m2", "B", cols=["col_b"]),
            _meeting("m3", "C", cols=["col_c"]),
        ]

        def _get(mid: str):
            return next((m for m in meetings if m.id == mid), None)

        with (
            patch("src.meeting.store.list_meetings", return_value=meetings),
            patch("src.meeting.store.get_meeting", side_effect=_get),
        ):
            selected = json.loads(
                _run_list_meeting_catalog("sess_chat", {}, None, ["col_a"])
            )
            assert [r["meeting_id"] for r in selected["meetings"]] == ["m1"]

            all_meetings = json.loads(
                _run_list_meeting_catalog("sess_chat", {}, None, [])
            )
            assert [r["meeting_id"] for r in all_meetings["meetings"]] == [
                "m1",
                "m2",
                "m3",
            ]

    def test_summary_run_rejects_meeting_outside_selected(self):
        import json

        from src.chatbox.agent import _run_read_meeting_summary

        meetings = [
            _meeting("m1", "A", cols=["col_a"]),
            _meeting("m3", "C", cols=["col_c"]),
        ]
        with patch("src.meeting.store.list_meetings", return_value=meetings):
            data = json.loads(
                _run_read_meeting_summary(
                    "sess_chat", {"meeting_id": "m3"}, None, ["col_a"]
                )
            )
        assert "error" in data
        assert "m3" in data["error"]


class TestCatalogSummaryHead:
    def test_rows_include_summary_head_when_enabled(self):
        from src.meeting.catalog import catalog_rows

        meetings = [_meeting("m1", "A"), _meeting("m2", "B")]
        heads = {"m1": "Budget review.\n\nSecond paragraph."}
        with (
            patch("src.meeting.store.get_meeting", side_effect=lambda mid: next(m for m in meetings if m.id == mid)),
            patch("src.chatbox.meeting_context.load_general_summary_text", side_effect=lambda mid: heads.get(mid, "")),
        ):
            rows = catalog_rows(["m1", "m2"], include_summary_head=True)
        assert rows[0]["summary_head"] == "Budget review."
        assert "summary_head" not in rows[1]

    def test_summary_head_truncated_to_limit(self):
        from src.meeting.catalog import catalog_rows

        long_line = "x" * 500
        with (
            patch("src.meeting.store.get_meeting", return_value=_meeting("m1", "A")),
            patch("src.chatbox.meeting_context.load_general_summary_text", return_value=long_line),
        ):
            rows = catalog_rows(["m1"], include_summary_head=True)
        assert len(rows[0]["summary_head"]) <= 200
        assert rows[0]["summary_head"].endswith("…")

    def test_rows_without_head_keep_base_shape(self):
        from src.meeting.catalog import catalog_rows

        with (
            patch("src.meeting.store.get_meeting", return_value=_meeting("m1", "A")),
        ):
            rows = catalog_rows(["m1"])
        # Meeting defaults created_at to now → date is today; check the rest.
        assert rows[0]["meeting_id"] == "m1"
        assert rows[0]["title"] == "A"
        assert rows[0]["index_ready"] is True
        assert set(rows[0]) == {"meeting_id", "title", "date", "index_ready"}


class TestMeetingCatalogDigest:
    def test_digest_lists_titles_only_and_scopes_collections(self):
        from src.chatbox.meeting_context import build_meeting_catalog_digest

        meetings = [
            _meeting("m1", "Kickoff", cols=["col_a"]),
            _meeting("m2", "Other", cols=["col_b"]),
        ]
        with (
            patch("src.meeting.store.list_meetings", return_value=meetings),
            patch("src.meeting.store.get_meeting", side_effect=lambda mid: next(m for m in meetings if m.id == mid)),
        ):
            digest = build_meeting_catalog_digest(["col_a"])
        assert "Kickoff" in digest
        assert "Other" not in digest
        assert "summary_head" not in digest
        assert "indexed" in digest

    def test_digest_empty_when_no_visible_meetings(self):
        from src.chatbox.meeting_context import build_meeting_catalog_digest

        with patch("src.meeting.store.list_meetings", return_value=[]):
            assert build_meeting_catalog_digest(["col_a"]) == ""
            assert build_meeting_catalog_digest(None) == ""

    def test_digest_caps_rows_and_notes_overflow(self):
        from src.chatbox.meeting_context import build_meeting_catalog_digest

        meetings = [
            _meeting(f"m{i}", f"Meeting {i}", cols=["col_a"]) for i in range(35)
        ]
        with (
            patch("src.meeting.store.list_meetings", return_value=meetings),
            patch("src.meeting.store.get_meeting", side_effect=lambda mid: next(m for m in meetings if m.id == mid)),
        ):
            digest = build_meeting_catalog_digest(["col_a"])
        assert "+5 more" in digest
        assert "list_meeting_catalog" in digest
        assert "Meeting 29" in digest
        assert "Meeting 30" not in digest
