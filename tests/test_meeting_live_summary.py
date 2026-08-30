"""Live meeting summary engine — ops application, compaction, window,
prompt layout (prefix-cache stability), round loop, persistence.

Run: pytest tests/test_meeting_live_summary.py -v --tb=short
"""

from __future__ import annotations

import json
import threading
import time

import pytest


def _engine(meeting_id="m1", llm=None, persist=False, **kw):
    from src.meeting.live_summary import LiveSummaryEngine

    return LiveSummaryEngine(meeting_id, llm=llm, persist=persist, **kw)


def _feed(eng, n=1, t0=0.0, speaker="spk:0"):
    for i in range(n):
        eng.ingest_segment(
            start=t0 + i * 5,
            end=t0 + i * 5 + 4,
            text=f"line {t0}_{i}",
            speaker_id=speaker if speaker is not None else None,
            key=f"k{t0}_{i}",
        )


def _delta_json(**kw):
    return json.dumps(kw, ensure_ascii=False)


class _FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def generate(self, prompt, system="", **kw):
        self.calls.append({"prompt": prompt, "system": system, "kw": kw})
        return self.replies.pop(0) if self.replies else "{}"


# ── delta ops ────────────────────────────────────────────────────────


def test_add_assigns_sequential_ids_and_entry_t_from_last_segment():
    eng = _engine()
    _feed(eng, 2)
    res = eng.apply_delta(
        {
            "add": [
                {"kind": "decision", "text": "Ship Thursday", "speaker": "spk:1"},
                {"kind": "question", "text": "Who owns rollback?"},
            ]
        }
    )
    assert res["added"] == ["e1", "e2"]
    snap = eng.snapshot()
    assert [e["id"] for e in snap["entries"]] == ["e1", "e2"]
    assert snap["entries"][0]["kind"] == "decision"
    assert snap["entries"][0]["t"] == pytest.approx(9.0)  # last ingested end


def test_add_rejects_unknown_kind_and_blank_text():
    eng = _engine()
    res = eng.apply_delta(
        {
            "add": [
                {"kind": "bogus", "text": "x"},
                {"kind": "point", "text": "   "},
                {"kind": "point", "text": "valid"},
            ]
        }
    )
    assert res["added"] == ["e1"]
    assert len(eng.snapshot()["entries"]) == 1


def test_amend_replaces_text_and_keeps_id():
    eng = _engine()
    eng.apply_delta({"add": [{"kind": "decision", "text": "Ship Wednesday"}]})
    res = eng.apply_delta({"amend": [{"target": "e1", "text": "Ship Thursday"}]})
    assert res["amended"] == ["e1"]
    entry = eng.snapshot()["entries"][0]
    assert entry["id"] == "e1"
    assert entry["text"] == "Ship Thursday"


def test_amend_unknown_target_is_ignored():
    eng = _engine()
    res = eng.apply_delta({"amend": [{"target": "e404", "text": "nope"}]})
    assert res["amended"] == []
    assert eng.snapshot()["entries"] == []


def test_resolve_marks_entry_resolved():
    eng = _engine()
    eng.apply_delta({"add": [{"kind": "question", "text": "Q1"}]})
    res = eng.apply_delta({"resolve": ["e1", "e999"]})
    assert res["resolved"] == ["e1"]
    # Resolved entries leave the UI snapshot but stay in state so the model
    # still knows they were answered (and will not re-add them).
    assert eng.snapshot()["entries"] == []
    assert eng.state.entries[0].status == "resolved"


def test_resolved_entries_stripped_from_ws_push_too():
    # Regression: apply_delta used to push/persist a raw model_dump that
    # still contained resolved entries — the panel never lost them.
    pushed: list[dict] = []
    eng = _engine(on_update=pushed.append)
    eng.apply_delta({"add": [{"kind": "question", "text": "Q"}, {"kind": "point", "text": "P"}]})
    eng.apply_delta({"resolve": ["e1"]})
    assert pushed, "on_update fired"
    last = pushed[-1]
    assert [e["id"] for e in last["entries"]] == ["e2"]


def test_delta_ops_accept_scalars_instead_of_lists():
    # Weak models sometimes return "resolve": "e5" or a single add dict —
    # iterating a bare string would split it into characters and drop the op.
    eng = _engine()
    res = eng.apply_delta({"add": {"kind": "point", "text": "single"}})
    assert res["added"] == ["e1"]
    res = eng.apply_delta({"resolve": "e1"})
    assert res["resolved"] == ["e1"]
    assert eng.state.entries[0].status == "resolved"


def test_invented_speaker_names_are_stripped():
    # Realtime transcripts often carry no diarization — the model then loves
    # to invent names. Only speaker labels that actually appeared in the
    # transcript may be attached; anything else is dropped.
    eng = _engine()
    _feed(eng, 2, speaker="spk:1")
    eng.apply_delta(
        {
            "add": [
                {"kind": "point", "text": "real label", "speaker": "spk:1"},
                {"kind": "point", "text": "invented name", "speaker": "Jethro"},
            ]
        }
    )
    entries = eng.snapshot()["entries"]
    assert entries[0]["speaker"] == "spk:1"
    assert entries[1]["speaker"] is None


def test_all_speakers_stripped_when_transcript_has_none():
    eng = _engine()
    _feed(eng, 2, speaker=None)
    eng.apply_delta({"add": [{"kind": "point", "text": "x", "speaker": "spk:9"}]})
    assert eng.snapshot()["entries"][0]["speaker"] is None


def test_llm_calls_disable_thinking_for_latency():
    llm = _FakeLLM([_delta_json()])
    eng = _engine(llm=llm)
    _feed(eng, 1)
    assert eng.run_round() is True
    assert llm.calls[0]["kw"].get("thinking") is False


def test_concurrent_rounds_are_serialized_not_duplicated():
    # finalize() joins the loop thread with a timeout — its final round can
    # race the loop's in-flight round. The second round must wait, then see
    # no pending content and no-op (one LLM call total, no double-processing).
    import threading

    started = threading.Event()
    release = threading.Event()

    class _BlockingLLM(_FakeLLM):
        def generate(self, prompt, system="", **kw):
            self.calls.append({"prompt": prompt, "system": system, "kw": kw})
            started.set()
            release.wait(2)
            return "{}"

    llm = _BlockingLLM([])
    eng = _engine(llm=llm)
    _feed(eng, 2)

    first = threading.Thread(target=eng.run_round)
    first.start()
    assert started.wait(1)

    result: list[bool] = []
    second = threading.Thread(target=lambda: result.append(eng.run_round()))
    second.start()
    time.sleep(0.2)
    assert second.is_alive(), "second round must block on the round lock"

    release.set()
    first.join(2)
    second.join(2)
    assert result == [False]
    assert len(llm.calls) == 1


# ── prompt rendering: new/old boundary & entry metadata ──────────────


def test_prompt_marks_new_transcript_lines_below_divider():
    llm = _FakeLLM([_delta_json(), _delta_json()])
    eng = _engine(llm=llm)
    _feed(eng, 2)
    assert eng.run_round() is True  # tail → 9
    _feed(eng, 2, t0=30.0)  # new lines, still inside the 60s context window
    _system, user = eng.build_prompt()
    tx = user.split("<recent-transcript>")[1]
    divider = "---- new lines below ----"
    assert divider in tx
    before, after = tx.split(divider)
    assert "line 0.0_" in before  # covered lines stay as context above
    assert "line 30.0_0" in after  # uncovered lines below the divider


def test_state_renders_placeholder_and_entry_times():
    eng = _engine()
    _sys, user = eng.build_prompt()
    assert "(no entries yet)" in user
    _feed(eng, 1)
    eng.apply_delta({"add": [{"kind": "point", "text": "x"}]})
    _sys, user = eng.build_prompt()
    assert '"t"' in user  # entry JSON carries its meeting time


def test_drop_removes_entry():
    eng = _engine()
    eng.apply_delta({"add": [{"kind": "point", "text": "a"}, {"kind": "point", "text": "b"}]})
    res = eng.apply_delta({"drop": ["e1", "e404"]})
    assert res["dropped"] == ["e1"]
    assert [e["id"] for e in eng.snapshot()["entries"]] == ["e2"]


def test_topic_update_sets_since_and_closed_semantics():
    eng = _engine()
    _feed(eng, 1, t0=100.0)
    eng.apply_delta({"topic": "Release plan"})
    topic = eng.snapshot()["topic"]
    assert topic["text"] == "Release plan"
    assert topic["since"] == pytest.approx(104.0)
    eng.apply_delta({"topic": "Release plan", "topic_closed": True})
    assert eng.snapshot()["topic"]["closed"] is True
    eng.apply_delta({"topic": "Rollout risks"})
    topic = eng.snapshot()["topic"]
    assert topic["text"] == "Rollout risks"
    assert topic["closed"] is False  # new topic resets closed


def test_snapshot_shape():
    eng = _engine()
    _feed(eng, 1)
    eng.apply_delta({"topic": "T", "add": [{"kind": "point", "text": "x"}]})
    snap = eng.snapshot()
    assert {
        "entries",
        "topic",
        "compacted_upto",
        "tail_from_t",
        "round",
        "engine",
        "updated_at",
    } <= set(snap.keys())
    assert set(snap["entries"][0].keys()) == {"id", "kind", "text", "speaker", "t", "status"}


# ── compaction ───────────────────────────────────────────────────────


def test_compaction_rewrite_merge_keep_ids():
    eng = _engine()
    eng.apply_delta(
        {
            "add": [
                {"kind": "point", "text": "verbose one"},
                {"kind": "point", "text": "verbose two"},
                {"kind": "point", "text": "standalone"},
                {"kind": "question", "text": "Q"},
            ]
        }
    )
    eng.apply_delta({"resolve": ["e4"]})
    eng.apply_compaction(
        {
            "rewrite": [{"id": "e1", "text": "tight one"}],
            "merge": [{"keep": "e2", "absorb": ["e3"], "text": "two and three"}],
            "drop": ["e4"],
        }
    )
    snap = eng.snapshot()
    assert [(e["id"], e["text"]) for e in snap["entries"]] == [
        ("e1", "tight one"),
        ("e2", "two and three"),
    ]
    assert snap["compacted_upto"] == "e4"  # max id that existed at compaction


def test_needs_compaction_threshold():
    eng = _engine(compact_entries=3)
    assert eng.needs_compaction() is False
    eng.apply_delta({"add": [{"kind": "point", "text": f"p{i}"} for i in range(4)]})
    assert eng.needs_compaction() is True


def test_compaction_triggers_after_interval_with_fresh_entries():
    eng = _engine(compact_entries=40, compact_interval_s=300, compact_min_entries=3)
    eng.apply_delta({"add": [{"kind": "point", "text": f"p{i}"} for i in range(3)]})
    assert eng.needs_compaction(now=1000.0) is False  # never compacted, but fresh clock base
    eng._last_compact_ts = 1000.0
    assert eng.needs_compaction(now=1299.0) is False  # interval not yet elapsed
    assert eng.needs_compaction(now=1300.0) is True  # interval reached AND ≥3 new entries


def test_compaction_time_alone_is_not_enough_below_min_entries():
    eng = _engine(compact_interval_s=300, compact_min_entries=10)
    eng.apply_delta({"add": [{"kind": "point", "text": "only one"}]})
    assert eng.needs_compaction(now=10_000.0) is False


# ── prompts: admission bar + language follow + English only ──────────


def test_live_summary_system_prompt_semantics_and_language():
    import re

    from src.prompts import MEETING_LIVE_SUMMARY_SYSTEM

    assert not re.search(r"[\u4e00-\u9fff]", MEETING_LIVE_SUMMARY_SYSTEM)
    # Question bar: only tensions the meeting did NOT verbally acknowledge
    assert "acknowledge" in MEETING_LIVE_SUMMARY_SYSTEM
    # Point bar: durable information only
    assert "durable" in MEETING_LIVE_SUMMARY_SYSTEM
    # Output language follows the meeting's spoken language
    assert "spoken language" in MEETING_LIVE_SUMMARY_SYSTEM


# ── ingestion & window ───────────────────────────────────────────────


def test_ingest_dedups_by_key():
    eng = _engine()
    eng.ingest_segment(0, 2, "dup", key="a")
    eng.ingest_segment(0, 2, "dup", key="a")
    eng.ingest_segment(3, 5, "fresh", key="b")
    assert len(eng.window_segments()) == 2


def test_window_capped_to_recent_audio():
    eng = _engine()
    for i in range(20):  # 0..99s
        eng.ingest_segment(i * 5, i * 5 + 4, f"line{i}", key=f"k{i}")
    segs = eng.window_segments(window_s=30.0)
    assert segs
    assert segs[0]["start"] >= 70.0  # only the most recent ~30s


# ── prompt layout (prefix-cache stability) ──────────────────────────


def test_prompt_layout_state_before_transcript_with_stable_prefix():
    eng = _engine()
    _feed(eng, 2)
    eng.apply_delta({"add": [{"kind": "point", "text": "key fact"}]})
    eng.apply_delta({"topic": "Planning"})
    system1, user1 = eng.build_prompt()
    assert "<state>" in user1 and "<recent-transcript>" in user1 and "<task>" in user1
    assert user1.index("<state>") < user1.index("<recent-transcript>") < user1.index("<task>")
    assert "key fact" in user1.split("<recent-transcript>")[0]
    # entries render before the topic line so appends keep the prefix stable
    assert user1.index("topic:") > user1.index("e1 ")

    # append-only evolution keeps the system+entries prefix byte-stable
    eng.apply_delta({"add": [{"kind": "point", "text": "later fact"}]})
    _, user2 = eng.build_prompt()
    entries_part = user1.split("<recent-transcript>")[0].rsplit("topic:", 1)[0]
    assert user2.startswith(entries_part)


# ── round loop ───────────────────────────────────────────────────────


def test_run_round_applies_delta_and_advances_tail():
    llm = _FakeLLM([_delta_json(topic="Planning", add=[{"kind": "decision", "text": "Ship"}])])
    eng = _engine(llm=llm)
    _feed(eng, 3)
    assert eng.run_round() is True
    snap = eng.snapshot()
    assert snap["round"] == 1
    assert snap["topic"]["text"] == "Planning"
    assert snap["entries"][0]["text"] == "Ship"
    assert snap["tail_from_t"] == pytest.approx(14.0)

    # no new finals → round skipped, no extra LLM call
    assert eng.run_round() is False
    assert len(llm.calls) == 1


def test_run_round_second_window_contains_only_new_lines():
    llm = _FakeLLM([_delta_json(), _delta_json()])
    eng = _engine(llm=llm)
    _feed(eng, 2)
    eng.run_round()
    _feed(eng, 2, t0=100.0)
    assert eng.run_round() is True
    tail = llm.calls[1]["prompt"].split("<recent-transcript>")[1]
    assert "line 0_0" not in tail
    assert "line 100.0_0" in tail


def test_run_round_retries_invalid_json_once_then_skips():
    llm = _FakeLLM(["not json at all", "```\n{oops\n```"])
    eng = _engine(llm=llm)
    _feed(eng, 1)
    assert eng.run_round() is False
    assert len(llm.calls) == 2  # initial + one strict retry
    assert eng.snapshot()["round"] == 0


def test_run_round_accepts_fenced_json():
    llm = _FakeLLM(["```json\n" + _delta_json(topic="T") + "\n```"])
    eng = _engine(llm=llm)
    _feed(eng, 1)
    assert eng.run_round() is True
    assert eng.snapshot()["topic"]["text"] == "T"


def test_run_round_triggers_compaction_over_threshold():
    deltas = [_delta_json(add=[{"kind": "point", "text": f"p{i}"}]) for i in range(4)]
    compact = json.dumps({"rewrite": [{"id": "e1", "text": "tight one"}]})
    llm = _FakeLLM(deltas + [compact])
    eng = _engine(llm=llm, compact_entries=3)
    for i in range(4):
        eng.ingest_segment(i * 10, i * 10 + 5, f"line{i}", key=f"k{i}")
        assert eng.run_round() is True
    # 4th delta pushed active count over the threshold → one extra compaction call
    assert len(llm.calls) == 5
    assert "compact" in llm.calls[4]["prompt"].lower()
    snap = eng.snapshot()
    assert snap["compacted_upto"] == "e4"
    assert snap["entries"][0]["text"] == "tight one"


def test_reset_clears_state_but_keeps_transcript_buffer():
    llm = _FakeLLM([_delta_json(add=[{"kind": "point", "text": "x"}])])
    eng = _engine(llm=llm)
    _feed(eng, 1)
    eng.run_round()
    eng.reset()
    snap = eng.snapshot()
    assert snap["entries"] == []
    assert snap["topic"] is None
    assert snap["round"] == 0
    assert eng.window_segments()  # buffer survives; tail rewound to re-summarize


# ── LLM resolution chain ─────────────────────────────────────────────


def _provider(**kwargs):
    from types import SimpleNamespace

    data = {"id": "p1", "is_default": False, "default_model": "m-a", "model": "m-a"}
    data.update(kwargs)
    return SimpleNamespace(**data)


def _patch_llm_env(monkeypatch, providers, enrichment):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    created = {}

    def _create(p, model=None):
        created["id"] = p.id
        created["model"] = model
        return MagicMock()

    monkeypatch.setattr("src.providers.llm.create_llm_for_provider", _create)
    cfg = SimpleNamespace(
        llm=SimpleNamespace(providers=providers),
        enrichment=SimpleNamespace(**enrichment),
    )
    monkeypatch.setattr("src.config.get_config", lambda: cfg)
    return created


def test_enrichment_config_has_live_summary_model():
    from src.config import EnrichmentConfig

    assert EnrichmentConfig().live_summary_model == ""


def test_live_summary_llm_prefers_own_model(monkeypatch):
    from src.meeting.live_summary import resolve_live_summary_llm

    created = _patch_llm_env(
        monkeypatch,
        [_provider(id="fast"), _provider(id="meet")],
        {"live_summary_model": "fast|turbo", "meeting_model": "meet|m1"},
    )
    resolve_live_summary_llm()
    assert created["id"] == "fast"
    assert created["model"] == "turbo"


def test_live_summary_llm_falls_back_to_meeting_model(monkeypatch):
    from src.meeting.live_summary import resolve_live_summary_llm

    created = _patch_llm_env(
        monkeypatch,
        [_provider(id="fast"), _provider(id="meet")],
        {"live_summary_model": "", "meeting_model": "meet|m1"},
    )
    resolve_live_summary_llm()
    assert created["id"] == "meet"


def test_live_summary_llm_falls_back_to_default_card(monkeypatch):
    from src.meeting.live_summary import resolve_live_summary_llm

    created = _patch_llm_env(
        monkeypatch,
        [_provider(id="fast"), _provider(id="meet", is_default=True)],
        {"live_summary_model": "", "meeting_model": ""},
    )
    resolve_live_summary_llm()
    assert created["id"] == "meet"


# ── persistence ──────────────────────────────────────────────────────


def test_round_persists_and_engine_resumes(tmp_path, monkeypatch):
    import src.meeting.store as meeting_store

    monkeypatch.setattr(meeting_store, "MEETINGS_DIR", tmp_path)
    meeting = meeting_store.create_meeting("live summary test")
    llm = _FakeLLM([_delta_json(topic="T", add=[{"kind": "point", "text": "kept"}])])
    eng = _engine(meeting.id, llm=llm, persist=True)
    _feed(eng, 1)
    assert eng.run_round() is True
    saved = meeting_store.get_live_summary(meeting.id)
    assert saved is not None
    assert saved["round"] == 1
    assert saved["entries"][0]["text"] == "kept"

    eng2 = _engine(meeting.id, llm=_FakeLLM([]), persist=True)
    snap = eng2.snapshot()
    assert snap["round"] == 1
    assert snap["entries"][0]["text"] == "kept"
