"""Live summary engine must survive reconnect cycles.

Reproduces the interrupted-meeting flow against the LiveSummaryEngine:
capture session ends (graceful stop → finalize(), or abrupt refresh →
engine left running), a NEW websocket session enables live summary again
(ensure_engine → start()), and rounds must fire again for newly ingested
segments.

Run: pytest tests/test_live_summary_restart.py -v --tb=short
"""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import patch

import pytest

from src.meeting.live_summary import LiveSummaryEngine, ensure_engine, drop_engine


@pytest.fixture()
def isolated_store(tmp_path):
    """ensure_engine persists — keep writes out of the real data directory."""
    with patch("src.meeting.store.MEETINGS_DIR", tmp_path / "meetings"):
        yield


class _FakeLLM:
    """Records generate() calls; returns a valid (empty) delta each time."""

    def __init__(self):
        self.calls = 0
        self._lock = threading.Lock()

    def generate(self, prompt, *, system=None, temperature=0.2, max_tokens=500,
                 thinking=False, **_):
        with self._lock:
            self.calls += 1
        return json.dumps({"add": [], "amend": [], "resolve": [], "drop": []})


def _make_engine(llm, cadence=0.05):
    eng = LiveSummaryEngine("test-mtg", llm=llm, cadence_s=cadence)
    return eng


def _wait_for(callable_, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if callable_():
            return True
        time.sleep(0.02)
    return False


def test_rounds_run_then_restart_after_finalize():
    llm = _FakeLLM()
    eng = _make_engine(llm)

    # ── session 1: enable → rounds fire for ingested segments ──
    assert ensure_engine.__module__  # registry module importable
    eng.start()
    assert eng.state.engine == "running"
    eng.ingest_segment(0.0, 5.0, "hello")
    assert _wait_for(lambda: llm.calls >= 1), "no round in session 1"
    rounds_after_s1 = eng.state.round
    assert rounds_after_s1 >= 1

    # ── graceful stop path (client_requested_stop → finalize) ──
    eng.finalize()
    assert eng.state.engine == "idle"
    calls_after_finalize = llm.calls

    # ── session 2: reconnect → enable → start() must revive the loop ──
    eng.start()
    assert eng.state.engine == "running"
    eng.ingest_segment(5.0, 10.0, "world")
    assert _wait_for(lambda: llm.calls > calls_after_finalize), (
        "engine did not run rounds after restart — reconnect kills live summary"
    )
    assert eng.state.round > rounds_after_s1
    eng.stop()


@pytest.mark.usefixtures("isolated_store")
def test_registry_returns_same_engine_and_restart_revives_it():
    # Mirror the route flow: ensure_engine on each session's enable action.
    # ensure_engine uses the production 15s cadence — shrink it so rounds
    # fire within the test window.
    llm = _FakeLLM()
    eng1 = ensure_engine("registry-mtg", llm_factory=lambda: llm)
    eng1.cadence_s = 0.05
    eng1.start()
    eng1.ingest_segment(0.0, 5.0, "one")
    assert _wait_for(lambda: llm.calls >= 1)
    eng1.finalize()

    # New session resolves the same engine and re-enables.
    eng2 = ensure_engine("registry-mtg", llm_factory=lambda: llm)
    assert eng2 is eng1
    eng2.cadence_s = 0.05
    eng2.start()
    eng2.ingest_segment(5.0, 10.0, "two")
    before = llm.calls
    assert _wait_for(lambda: llm.calls > before, timeout=3.0), (
        "ensure_engine→start() after finalize() does not resume rounds"
    )
    eng2.stop()
    drop_engine("registry-mtg")


def test_finalize_cannot_idle_a_restarted_engine():
    """The translation-toggle reconnect races finalize(): the new WS session
    re-enables (start()) while finalize's closing LLM round is still in
    flight. finalize's epilogue must not flip the freshly restarted engine
    back to idle — the 'engine == running' ingest gate then silently drops
    every later final and the live summary freezes until the next enable."""
    eng = _make_engine(_FakeLLM())

    class _RestartMidRoundLLM:
        def generate(self, *a, **kw):
            # New WS session re-enables while finalize's closing round runs.
            eng.start()
            return json.dumps({"add": [], "amend": [], "resolve": [], "drop": []})

    eng.llm = _RestartMidRoundLLM()
    eng.start()
    eng.ingest_segment(0.0, 5.0, "hello")
    eng.finalize()

    assert eng.state.engine == "running", (
        "finalize() idled an engine that was restarted while its closing "
        "round was in flight — live summary would freeze"
    )
    eng.stop()


def test_default_cadence_is_user_tuned_10s():
    """Lock the user-tuned round cadence (10s) against accidental drift."""
    eng = LiveSummaryEngine("cadence-check", llm=_FakeLLM())
    assert eng.cadence_s == 10.0


def test_timestamp_regression_across_sessions_does_not_wedge_rounds():
    """Provider sentence clocks are per-session: after an engine switch or a
    page refresh the new session's finals can carry far smaller timestamps
    than the previous session's (observed 71440 → 10560 in the wild). The
    round gate 'any(end > tail_from_t)' then never fires again and live
    summary silently freezes. Ingested segments must advance the stored
    timeline monotonically so reconnects keep generating."""
    llm = _FakeLLM()
    eng = _make_engine(llm)
    eng.state.tail_from_t = 71440.0  # covered by the previous WS session

    eng.start()
    # New session finals — timestamps regressed far below the tail.
    eng.ingest_segment(800.0, 10560.0, "after reconnect one")
    eng.ingest_segment(12080.0, 15680.0, "after reconnect two")
    before = llm.calls
    assert _wait_for(lambda: llm.calls > before, timeout=3.0), (
        "rounds wedged: regressed timestamps never exceeded tail_from_t"
    )
    assert eng.state.round >= 1
    eng.stop()
