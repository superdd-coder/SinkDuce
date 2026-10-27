"""Event reducer for DashScope LiveTranslate (qwen3.5-livetranslate-flash-realtime).

Turns the model's server events into the meeting module's
``on_segment(segment, is_final, key)`` protocol, pairing each source
transcription with its translation so the frontend can render one
bilingual caption block per VAD turn.

Pairing contract (from the LiveTranslate server-events doc):
  - Each VAD turn produces one ASR item and one translation output item.
  - The translation item's ``conversation.item.created`` event carries
    ``previous_item_id`` equal to the ASR item's ``item.id`` — that is the
    only explicit link between the two streams.
  - Timestamps only exist on the VAD events (``audio_start_ms`` /
    ``audio_end_ms``); transcription/translation events carry none.
  - Streaming events expose ``text`` (confirmed) + ``stash`` (tentative,
    may be revised later).

Because simultaneous interpretation starts translating before the source
utterance finishes, a turn is only ``is_final`` once BOTH the source
transcription and the translation have completed — the two streams may
finalize in either order.

This module is deliberately free of dashscope imports: the adapter wires
the SDK's JSON-string callback into ``handle()``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from src.meeting.models import TranscriptSegment

logger = logging.getLogger(__name__)

_EVT_SPEECH_STARTED = "input_audio_buffer.speech_started"
_EVT_SPEECH_STOPPED = "input_audio_buffer.speech_stopped"
_EVT_ITEM_CREATED = "conversation.item.created"
_EVT_ASR_TEXT = "conversation.item.input_audio_transcription.text"
_EVT_ASR_COMPLETED = "conversation.item.input_audio_transcription.completed"
_EVT_TRANS_TEXT = "response.text.text"
_EVT_TRANS_DONE = "response.text.done"


class _Turn:
    """One bilingual caption block, keyed by the ASR item id."""

    __slots__ = ("key", "start", "end", "text", "stash", "translation",
                 "trans_stash", "asr_final", "trans_final")

    def __init__(self, key: str, start: float, end: float):
        self.key = key
        self.start = start
        self.end = end
        self.text = ""
        self.stash = ""
        self.translation = ""
        self.trans_stash = ""
        self.asr_final = False
        self.trans_final = False

    @property
    def final(self) -> bool:
        return self.asr_final and self.trans_final

    def display_text(self) -> str:
        return (self.text + self.stash).strip()

    def display_translation(self) -> str:
        return (self.translation + self.trans_stash).strip()


class LiveTranslateEventReducer:
    """Consume parsed LiveTranslate event dicts, emit segment callbacks."""

    def __init__(self, on_segment: Callable[[TranscriptSegment, bool, Any], None]):
        self._on_segment = on_segment
        self._turns: dict[str, _Turn] = {}
        self._order: list[str] = []
        # translation output item_id → ASR item id (via previous_item_id)
        self._trans_to_asr: dict[str, str] = {}
        # open VAD window
        self._cur_start_ms: float | None = None
        self._open_keys: list[str] = []
        self._last_end_s: float = 0.0

    # -- public entry -------------------------------------------------------

    def handle(self, event: dict | str) -> None:
        if isinstance(event, str):
            try:
                event = json.loads(event)
            except (TypeError, ValueError):
                logger.warning("[LiveTranslate] Non-JSON event dropped: %.80r", event)
                return
        if not isinstance(event, dict):
            return
        etype = event.get("type")
        try:
            handler = self._HANDLERS.get(etype)
            if handler is not None:
                handler(self, event)
        except Exception:
            logger.warning("[LiveTranslate] Failed to process %s", etype, exc_info=True)

    # -- VAD window ----------------------------------------------------------

    def _on_speech_started(self, event: dict) -> None:
        ms = event.get("audio_start_ms")
        self._cur_start_ms = float(ms) if ms is not None else None
        self._open_keys = []

    def _on_speech_stopped(self, event: dict) -> None:
        ms = event.get("audio_end_ms")
        if ms is None:
            return
        end_s = float(ms) / 1000.0
        for key in self._open_keys:
            turn = self._turns.get(key)
            if turn is not None:
                turn.end = end_s
        self._last_end_s = max(self._last_end_s, end_s)
        self._open_keys = []

    # -- item linkage ----------------------------------------------------------

    def _on_item_created(self, event: dict) -> None:
        item = event.get("item") or {}
        item_id = item.get("id") or event.get("item_id")
        prev = event.get("previous_item_id")
        if item_id and prev:
            self._trans_to_asr[item_id] = prev

    # -- source transcription ---------------------------------------------------

    def _on_asr_text(self, event: dict) -> None:
        turn = self._turn_for_asr(event.get("item_id"))
        if turn is None:
            return
        turn.text = (event.get("text") or "").strip()
        turn.stash = (event.get("stash") or "").strip()
        self._emit(turn, False)

    def _on_asr_completed(self, event: dict) -> None:
        turn = self._turn_for_asr(event.get("item_id"))
        if turn is None:
            return
        turn.text = (event.get("transcript") or event.get("text") or "").strip()
        turn.stash = ""
        turn.asr_final = True
        self._emit(turn, turn.final)

    # -- translation ----------------------------------------------------------

    def _on_trans_text(self, event: dict) -> None:
        turn = self._turn_for_translation(event.get("item_id"))
        if turn is None:
            return
        turn.translation = (event.get("text") or "").strip()
        turn.trans_stash = (event.get("stash") or "").strip()
        self._emit(turn, False)

    def _on_trans_done(self, event: dict) -> None:
        turn = self._turn_for_translation(event.get("item_id"))
        if turn is None:
            return
        turn.translation = (event.get("text") or event.get("transcript") or "").strip()
        turn.trans_stash = ""
        turn.trans_final = True
        self._emit(turn, turn.final)

    # -- helpers ----------------------------------------------------------

    def _turn_for_asr(self, item_id: Any) -> _Turn | None:
        if not item_id:
            return None
        turn = self._turns.get(item_id)
        if turn is not None:
            return turn
        if self._cur_start_ms is not None:
            start = self._cur_start_ms / 1000.0
        else:
            start = self._last_end_s
        turn = _Turn(key=item_id, start=start, end=start)
        self._turns[item_id] = turn
        self._order.append(item_id)
        self._open_keys.append(item_id)
        return turn

    def _turn_for_translation(self, item_id: Any) -> _Turn | None:
        asr_key = self._trans_to_asr.get(item_id) if item_id else None
        if asr_key is None:
            # No created-event linkage seen yet — attach to the newest turn.
            asr_key = self._order[-1] if self._order else None
        if not asr_key:
            return None
        # SI may emit translation before any ASR event — create the turn
        # lazily so the bilingual block exists from the first event on.
        return self._turn_for_asr(asr_key)

    def _emit(self, turn: _Turn, is_final: bool) -> None:
        text = turn.display_text()
        if not text and not turn.display_translation():
            return
        segment = TranscriptSegment(
            start=turn.start,
            end=turn.end,
            text=text,
            speaker_id=None,
            translation=turn.display_translation() or None,
        )
        self._on_segment(segment, is_final, turn.key)

    _HANDLERS = {
        _EVT_SPEECH_STARTED: _on_speech_started,
        _EVT_SPEECH_STOPPED: _on_speech_stopped,
        _EVT_ITEM_CREATED: _on_item_created,
        _EVT_ASR_TEXT: _on_asr_text,
        _EVT_ASR_COMPLETED: _on_asr_completed,
        _EVT_TRANS_TEXT: _on_trans_text,
        _EVT_TRANS_DONE: _on_trans_done,
    }
