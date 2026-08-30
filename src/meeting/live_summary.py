"""In-meeting live summary engine.

Incremental summarizer that runs while a meeting is being recorded with
realtime transcription on. Core invariant: the LLM only ever returns
ops (per-round deltas and compaction ops) — the server validates and
applies them mechanically, so a malformed model output can cost one
round but never corrupt the state.

State layout note (prefix-cache): the per-round prompt renders the
entries first (append-only between compactions), then the topic line,
then the sliding transcript window and the task — so providers with
automatic prefix caching reuse the system+entries prefix across rounds.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone

from src.meeting import store as meeting_store
from src.meeting.models import LiveSummaryState, LiveSummaryTopic, LiveSummaryEntry
from src.prompts import (
    MEETING_LIVE_COMPACT_PROMPT,
    MEETING_LIVE_SUMMARY_PROMPT,
    MEETING_LIVE_SUMMARY_SYSTEM,
)

logger = logging.getLogger("meeting.live_summary")

_VALID_KINDS = {"point", "decision", "question", "action"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fmt_ts(seconds: float) -> str:
    total = max(0, int(seconds))
    m, s = divmod(total, 60)
    return f"{m}:{s:02d}"


def _id_num(entry_id: str) -> int:
    try:
        return int(entry_id[1:])
    except (ValueError, IndexError):
        return 0


def _language_note(window: list[dict]) -> str:
    """Deterministic language instruction from the window's script.

    A weak model left to "detect the dominant language" can latch onto a
    wrong language on a thin first window, and a consistency anchor then
    locks the mistake in for the whole meeting. Script detection removes
    the guessing for CJK meetings; for shared-script languages the note
    falls back to "the transcript's exact language, never translate".
    """
    text = " ".join(s.get("text", "") or "" for s in window)
    if any("\u3040" <= ch <= "\u30ff" for ch in text):
        lang = "Japanese"
    elif any("\uac00" <= ch <= "\ud7af" for ch in text):
        lang = "Korean"
    elif any("\u4e00" <= ch <= "\u9fff" for ch in text):
        lang = "Chinese"
    else:
        return (
            "Write every text value and the topic in the transcript's "
            "exact language — never translate."
        )
    return (
        f"The transcript language is {lang}. Write every text value and "
        f"the topic in {lang}."
    )


def _as_list(value) -> list:
    """Weak models sometimes emit a scalar instead of an array ("resolve":
    "e5" or a single add dict) — normalize so iterating never splits strings."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _parse_json_block(raw: str) -> dict | None:
    """Extract the first JSON object from an LLM reply (fences tolerated)."""
    if not raw:
        return None
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


class LiveSummaryEngine:
    def __init__(
        self,
        meeting_id: str,
        llm=None,
        persist: bool = False,
        cadence_s: float = 15.0,
        window_s: float = 60.0,
        compact_entries: int = 40,
        compact_interval_s: float = 180.0,  # user-tuned: compact every ~3 min
        compact_min_entries: int = 10,
        on_update=None,
    ):
        self.meeting_id = meeting_id
        self.llm = llm
        self.persist = persist
        self.cadence_s = cadence_s
        self.window_s = window_s
        self.compact_entries = compact_entries
        self.compact_interval_s = compact_interval_s
        self.compact_min_entries = compact_min_entries
        self.on_update = on_update  # callable(snapshot_dict) — WS push hook
        self._last_compact_ts = time.time()

        # Final transcript buffer (session-local; the LLM window is rebuilt
        # from it each round, capped to recent audio).
        self._segments: list[dict] = []
        self._seen_keys: set[str] = set()
        self._last_segment_end = 0.0
        self._seg_lock = threading.Lock()

        self._state_lock = threading.RLock()
        self._round_lock = threading.Lock()
        self.state = LiveSummaryState()
        if persist:
            saved = meeting_store.get_live_summary(meeting_id)
            if saved:
                try:
                    self.state = LiveSummaryState.model_validate(saved)
                except Exception:
                    logger.warning(
                        "[LIVE-SUMMARY] Discarding unreadable state for %s",
                        meeting_id,
                    )

        self._next_id = self._derive_next_id()
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._in_flight = False

    # ── state helpers ────────────────────────────────────────────

    def _derive_next_id(self) -> int:
        best = 0
        for e in self.state.entries:
            try:
                best = max(best, int(e.id[1:]))
            except (ValueError, IndexError):
                continue
        return best + 1

    def _entry_map(self) -> dict[str, LiveSummaryEntry]:
        return {e.id: e for e in self.state.entries}

    def _known_speakers(self) -> set[str]:
        """Speaker labels that actually appeared in the transcript — the model
        may only attach these (never invented names)."""
        with self._seg_lock:
            return {s["speaker_id"] for s in self._segments if s.get("speaker_id")}

    def snapshot(self) -> dict:
        with self._state_lock:
            snap = self.state.model_dump()
        # Resolved questions leave the UI (and the persisted artifact) once
        # answered; they stay in self.state so the model still sees that they
        # were settled and does not re-add them before the next compaction.
        snap["entries"] = [e for e in snap["entries"] if e["status"] == "active"]
        return snap

    # ── transcript ingestion ─────────────────────────────────────

    def ingest_segment(
        self,
        start: float,
        end: float,
        text: str,
        speaker_id: str | None = None,
        key: str | None = None,
    ) -> None:
        """Record one FINAL transcript segment (deduped by key across WS reconnects)."""
        if key is not None:
            with self._seg_lock:
                if key in self._seen_keys:
                    return
                self._seen_keys.add(key)
        with self._seg_lock:
            self._segments.append(
                {"start": start, "end": end, "text": text, "speaker_id": speaker_id}
            )
            self._last_segment_end = max(self._last_segment_end, end)

    def window_segments(self, window_s: float | None = None) -> list[dict]:
        """Recent transcript window used as the round's context: the last
        ``window_s`` of audio regardless of coverage. Lines already covered
        by an earlier round still appear as context; the prompt divider marks
        where the uncovered (new) lines start."""
        w = self.window_s if window_s is None else window_s
        with self._seg_lock:
            if not self._segments:
                return []
            latest = max(s["end"] for s in self._segments)
            cutoff = latest - w
            return [dict(s) for s in self._segments if s["end"] > cutoff]

    # ── ops application (pure state transitions) ────────────────

    def apply_delta(self, delta: dict) -> dict:
        """Apply one per-round delta; returns ids by operation for the UI."""
        result = {"added": [], "amended": [], "resolved": [], "dropped": []}
        with self._state_lock:
            now_t = self._last_segment_end
            for item in _as_list(delta.get("add")):
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("kind") or "").strip()
                text = str(item.get("text") or "").strip()
                if kind not in _VALID_KINDS or not text:
                    continue
                entry_id = f"e{self._next_id}"
                self._next_id += 1
                speaker_raw = str(item.get("speaker") or "").strip() or None
                known = self._known_speakers()
                speaker = speaker_raw if speaker_raw in known else None
                self.state.entries.append(
                    LiveSummaryEntry(
                        id=entry_id,
                        kind=kind,
                        text=text,
                        speaker=speaker,
                        t=now_t,
                    )
                )
                result["added"].append(entry_id)

            entries = self._entry_map()
            for item in _as_list(delta.get("amend")):
                if not isinstance(item, dict):
                    continue
                target = entries.get(str(item.get("target") or ""))
                text = str(item.get("text") or "").strip()
                if target is None or not text:
                    continue
                target.text = text
                result["amended"].append(target.id)

            for target_id in _as_list(delta.get("resolve")):
                target = entries.get(str(target_id))
                if target is None:
                    continue
                target.status = "resolved"
                result["resolved"].append(target.id)

            drop_ids = {str(d) for d in _as_list(delta.get("drop"))}
            if drop_ids:
                kept = []
                for e in self.state.entries:
                    if e.id in drop_ids:
                        result["dropped"].append(e.id)
                    else:
                        kept.append(e)
                self.state.entries = kept

            topic_text = str(delta.get("topic") or "").strip()
            if topic_text and (self.state.topic is None or self.state.topic.text != topic_text):
                self.state.topic = LiveSummaryTopic(
                    text=topic_text,
                    since=now_t,
                    closed=bool(delta.get("topic_closed", False)),
                )
            elif self.state.topic is not None and "topic_closed" in delta:
                self.state.topic.closed = bool(delta.get("topic_closed"))

            self.state.updated_at = _now_iso()
            snap = self.snapshot()
        self._after_mutation(snap)
        return result

    def apply_compaction(self, ops: dict) -> None:
        """Apply compaction ops; surviving entries keep their ids so later
        amends still land precisely."""
        with self._state_lock:
            entries = self._entry_map()
            existed_ids = set(entries)
            for item in _as_list(ops.get("rewrite")):
                if not isinstance(item, dict):
                    continue
                target = entries.get(str(item.get("id") or ""))
                text = str(item.get("text") or "").strip()
                if target is not None and text:
                    target.text = text
            for item in _as_list(ops.get("merge")):
                if not isinstance(item, dict):
                    continue
                keep = entries.get(str(item.get("keep") or ""))
                text = str(item.get("text") or "").strip()
                absorb = [str(a) for a in _as_list(item.get("absorb"))]
                if keep is None or not text or not absorb:
                    continue
                keep.text = text
                absorb_ids = {a for a in absorb if a in existed_ids and a != keep.id}
                if absorb_ids:
                    self.state.entries = [
                        e for e in self.state.entries if e.id not in absorb_ids
                    ]
            drop_ids = {str(d) for d in _as_list(ops.get("drop")) if str(d) in existed_ids}
            if drop_ids:
                self.state.entries = [e for e in self.state.entries if e.id not in drop_ids]

            self.state.compacted_upto = max(existed_ids, key=_id_num, default="")
            self.state.updated_at = _now_iso()
            snap = self.snapshot()
        self._after_mutation(snap)

    def needs_compaction(self, now: float | None = None) -> bool:
        """Compact when the active list grows past the cap, OR when enough new
        entries have piled up and the last compaction is older than the
        interval (so a long meeting keeps the list tight, not just under 40)."""
        now = time.time() if now is None else now
        with self._state_lock:
            active = sum(1 for e in self.state.entries if e.status == "active")
            if active > self.compact_entries:
                return True
            watermark = _id_num(self.state.compacted_upto) if self.state.compacted_upto else 0
            fresh = sum(1 for e in self.state.entries if _id_num(e.id) > watermark)
            return (
                fresh >= self.compact_min_entries
                and (now - self._last_compact_ts) >= self.compact_interval_s
            )

    def reset(self) -> None:
        """Clear entries/topic and rewind the transcript tail (start fresh,
        keep the in-session buffer so the next round re-summarizes from now)."""
        with self._state_lock:
            self.state = LiveSummaryState()
            self._next_id = 1
            snap = self.state.model_dump()
        self._after_mutation(snap)

    def _after_mutation(self, snap: dict) -> None:
        if self.persist:
            try:
                meeting_store.save_live_summary(self.meeting_id, snap)
            except FileNotFoundError:
                logger.warning(
                    "[LIVE-SUMMARY] Meeting %s vanished; skip persist",
                    self.meeting_id,
                )
            except Exception:
                logger.exception("[LIVE-SUMMARY] Persist failed for %s", self.meeting_id)
        if self.on_update:
            try:
                self.on_update(snap)
            except Exception:
                logger.exception("[LIVE-SUMMARY] on_update callback failed")

    # ── prompt building ──────────────────────────────────────────

    def _render_state(self) -> str:
        lines: list[str] = []
        for e in self.state.entries:
            obj: dict = {"kind": e.kind, "status": e.status, "t": _fmt_ts(e.t)}
            if e.speaker:
                obj["speaker"] = e.speaker
            obj["text"] = e.text
            lines.append(f"{e.id} {json.dumps(obj, ensure_ascii=False)}")
        if self.state.topic:
            mark = "closed" if self.state.topic.closed else "open"
            lines.append(f"topic: {self.state.topic.text} ({mark})")
        if not lines:
            return "(no entries yet)\n"
        return "\n".join(lines) + "\n"

    def build_prompt(self, window: list[dict] | None = None) -> tuple[str, str]:
        with self._state_lock:
            state_str = self._render_state()
            covered_upto = self.state.tail_from_t
        if window is None:
            window = self.window_segments()

        def _line(s: dict) -> str:
            speaker = f" [{s['speaker_id']}]" if s.get("speaker_id") else ""
            return f"[{_fmt_ts(s['start'])}]{speaker} {s['text']}"

        # Split the window at the previous round's coverage: lines above the
        # divider are context the model already processed; below are new.
        parts = [_line(s) for s in window]
        first_new = next((i for i, s in enumerate(window) if s["end"] > covered_upto), None)
        if first_new is not None:
            parts.insert(first_new, "---- new lines below ----")

        user = MEETING_LIVE_SUMMARY_PROMPT.format(
            state=state_str,
            transcript="\n".join(parts),
            language_note=_language_note(window),
        )
        return MEETING_LIVE_SUMMARY_SYSTEM, user

    # ── round loop ───────────────────────────────────────────────

    def run_round(self) -> bool:
        """One summarize round. Returns False when skipped (no new finals,
        LLM unavailable, or unparseable output after one strict retry).

        Serialized by _round_lock: finalize()'s closing round may race the
        background loop's in-flight round; without the lock both would build
        prompts from the same stale tail and double-process the same lines.
        """
        with self._round_lock:
            return self._run_round_locked()

    def _run_round_locked(self) -> bool:
        if self.llm is None:
            return False
        window = self.window_segments()
        if not any(s["end"] > self.state.tail_from_t for s in window):
            return False  # nothing new since the last round
        system, user = self.build_prompt(window)

        delta = self._call_llm_json(system, user)
        if delta is None:
            logger.warning("[LIVE-SUMMARY] Round skipped (unparseable LLM output)")
            return False

        tail_end = max(s["end"] for s in window)
        with self._state_lock:
            # Bump round/tail first so apply_delta's persist+notify includes them
            self.state.round += 1
            self.state.tail_from_t = max(self.state.tail_from_t, tail_end)
            result = self.apply_delta(delta)
        logger.info(
            "[LIVE-SUMMARY] Round %d for %s: +%d add / %d amend / %d resolve / %d drop",
            self.state.round,
            self.meeting_id,
            len(result["added"]),
            len(result["amended"]),
            len(result["resolved"]),
            len(result["dropped"]),
        )

        if self.needs_compaction():
            self.run_compaction()
        return True

    def run_compaction(self) -> bool:
        if self.llm is None:
            return False
        with self._state_lock:
            before = sum(1 for e in self.state.entries if e.status == "active")
            state_str = self._render_state()
        user = MEETING_LIVE_COMPACT_PROMPT.format(state=state_str)
        ops = self._call_llm_json(MEETING_LIVE_SUMMARY_SYSTEM, user)
        if ops is None:
            logger.warning("[LIVE-SUMMARY] Compaction skipped (unparseable output)")
            return False
        self.apply_compaction(ops)
        self._last_compact_ts = time.time()
        with self._state_lock:
            after = sum(1 for e in self.state.entries if e.status == "active")
        logger.info(
            "[LIVE-SUMMARY] Compaction for %s: %d active → %d entries",
            self.meeting_id,
            before,
            after,
        )
        return True

    def _call_llm_json(self, system: str, user: str) -> dict | None:
        # thinking=False: rounds are latency-sensitive — reasoning tokens can
        # double the round time for no quality gain on tiny JSON deltas.
        raw = self.llm.generate(
            user, system=system, temperature=0.2, max_tokens=500, thinking=False
        )
        parsed = _parse_json_block(raw)
        if parsed is not None:
            return parsed
        retry = user + "\n\nYour previous reply was not valid JSON. Reply again with ONLY the JSON object, no fences, no commentary."
        raw = self.llm.generate(
            retry, system=system, temperature=0.2, max_tokens=500, thinking=False
        )
        return _parse_json_block(raw)

    # ── background loop lifecycle ────────────────────────────────

    def start(self) -> None:
        with self._state_lock:
            self.state.engine = "running"
        if self._thread is not None and self._thread.is_alive():
            self._after_mutation(self.snapshot())
            return
        self._stop_evt = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"live-summary-{self.meeting_id}"
        )
        self._thread.start()
        self._after_mutation(self.snapshot())

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        with self._state_lock:
            self.state.engine = "idle"
        self._after_mutation(self.snapshot())

    def finalize(self) -> None:
        """Stop the loop and squeeze in one last round covering the tail."""
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        try:
            self.run_round()
        except Exception:
            logger.exception("[LIVE-SUMMARY] Final round failed for %s", self.meeting_id)
        with self._state_lock:
            self.state.engine = "idle"
        self._after_mutation(self.snapshot())

    def _loop(self) -> None:
        while not self._stop_evt.wait(self.cadence_s):
            if self._in_flight:
                continue
            self._in_flight = True
            try:
                self.run_round()
            except Exception:
                logger.exception(
                    "[LIVE-SUMMARY] Round crashed for %s", self.meeting_id
                )
            finally:
                self._in_flight = False

    def _notify(self) -> None:
        if self.on_update:
            try:
                self.on_update(self.snapshot())
            except Exception:
                logger.exception("[LIVE-SUMMARY] on_update callback failed")


# ── Per-meeting engine registry ─────────────────────────────────


def resolve_live_summary_llm():
    """LLM for live summary rounds: live_summary_model → meeting_model → default."""
    from src.config import get_config

    from src.meeting.service import _resolve_meeting_llm

    ref = getattr(get_config().enrichment, "live_summary_model", "") or ""
    return _resolve_meeting_llm(ref or None)


_engines: dict[str, LiveSummaryEngine] = {}
_engines_lock = threading.Lock()


def get_engine(meeting_id: str) -> LiveSummaryEngine | None:
    with _engines_lock:
        return _engines.get(meeting_id)


def ensure_engine(meeting_id: str, llm_factory=None) -> LiveSummaryEngine:
    """Get or create the engine for a meeting (persists + resumes state)."""
    with _engines_lock:
        eng = _engines.get(meeting_id)
        if eng is not None:
            return eng
        eng = LiveSummaryEngine(
            meeting_id,
            llm=llm_factory() if llm_factory else None,
            persist=True,
        )
        _engines[meeting_id] = eng
        return eng


def drop_engine(meeting_id: str) -> None:
    with _engines_lock:
        eng = _engines.pop(meeting_id, None)
    if eng is not None:
        try:
            eng.stop()
        except Exception:
            logger.exception("[LIVE-SUMMARY] Engine stop failed for %s", meeting_id)
