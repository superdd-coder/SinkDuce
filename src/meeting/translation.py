"""Summary translation streams — mixed into MeetingService."""
from __future__ import annotations

from src.prompts import (
    MEETING_TRANSLATION_PROMPT,
    MEETING_TRANSLATION_SYSTEM,
    TRANSLATION_LANG_NAMES,
)


class _TranslationStream:
    """Broadcast state for one summary-translation task.

    Unlike the queue-based section/blueprint streams, consumers tail a shared
    `accumulated` buffer guarded by `cond`.  Each SSE consumer reads from its
    own offset, so a reconnecting client (page refresh) replays every missed
    token and multiple windows each see the full stream.  The producer thread
    is fully decoupled from any consumer's connection.
    """

    def __init__(self, meeting_id: str, tab_id: str, lang: str) -> None:
        import threading
        self.meeting_id = meeting_id
        self.tab_id = tab_id
        self.lang = lang
        self.cond = threading.Condition()
        self.accumulated = ""                # full text so far (replay source)
        self.gen_state = "prefilling"        # prefilling | streaming | idle
        self.done = False
        self.error: str | None = None
        self.final: str | None = None        # final saved markdown


class MeetingTranslationMixin:

    def list_summary_translations(self, meeting_id: str, tab_id: str) -> list[str]:
        """Return language codes that already have a translation file."""
        return store.list_translation_langs(meeting_id, tab_id)

    def list_active_translations(self, meeting_id: str) -> list[dict]:
        """Return the (tab_id, language) pairs currently being translated.

        Used by the frontend to re-attach streams after a browser refresh.
        """
        with self._translation_stream_lock:
            return [
                {"tab_id": st.tab_id, "language": st.lang}
                for st in self._active_translation_streams.values()
                if st.meeting_id == meeting_id and not st.done
            ]

    def generate_translation_stream(self, meeting_id: str, tab_id: str, lang: str):
        """Yield SSE events for translating a summary into a target language.

        Broadcast + replay model: each task holds the full `accumulated` text
        behind a Condition.  Every consumer tails it from its own offset, so a
        reconnecting client (page refresh) replays all missed tokens, and
        multiple windows each see the full stream.  The LLM runs in a detached
        thread — client disconnect never cancels generation.

        Events: state / token / translation_done / error.
        """
        lang = lang.upper()
        if lang not in TRANSLATION_LANG_NAMES:
            yield {"event": "error", "data": {
                "message": f"Unsupported language '{lang}'. "
                           f"Supported: {', '.join(sorted(TRANSLATION_LANG_NAMES))}"}}
            return

        key = f"{meeting_id}:{tab_id}:{lang}"
        with self._translation_stream_lock:
            st = self._active_translation_streams.get(key)

        if st is None:
            # Not currently generating: serve cache, or start a fresh task.
            cached = store.get_translation_md(meeting_id, tab_id, lang)
            if cached is not None:
                yield {"event": "state", "data": {"translation_gen": "idle"}}
                yield {"event": "translation_done", "data": {
                    "tab_id": tab_id, "language": lang, "md": cached, "cached": True}}
                return
            source_md = store.get_section_md(meeting_id, tab_id)
            if not source_md or not source_md.strip():
                yield {"event": "error", "data": {
                    "message": f"No summary found for tab '{tab_id}'. Generate the summary first."}}
                return
            st = _TranslationStream(meeting_id, tab_id, lang)
            with self._translation_stream_lock:
                existing = self._active_translation_streams.get(key)
                if existing is not None:
                    st = existing               # lost a start race — tail the winner
                else:
                    self._active_translation_streams[key] = st
                    self._start_translation_thread(st, source_md)

        # Tail the shared buffer (fresh start AND re-attach share this path).
        emitted_state = None
        last = 0
        try:
            while True:
                with st.cond:
                    st.cond.wait(timeout=0.2)
                    cur = st.accumulated
                    gstate = st.gen_state
                    done = st.done
                    err = st.error
                    final = st.final
                if gstate != emitted_state:
                    emitted_state = gstate
                    yield {"event": "state", "data": {"translation_gen": gstate}}
                if len(cur) > last:
                    yield {"event": "token", "data": cur[last:]}
                    last = len(cur)
                if err is not None:
                    yield {"event": "error", "data": {"message": err}}
                    return
                if done:
                    yield {"event": "translation_done", "data": {
                        "tab_id": tab_id, "language": lang, "md": final, "cached": False}}
                    return
        except GeneratorExit:
            logger.info(
                "[TRANSLATE-STREAM] SSE client disconnected for %s — LLM continues in background",
                key,
            )

    def _start_translation_thread(self, st: "_TranslationStream", source_md: str) -> None:
        """Launch the detached LLM thread that feeds a _TranslationStream."""
        import threading

        def _stream_once(prompt: str, llm) -> str:
            """跑一次流式翻译，把 token 推进 st；返回最终文本。"""
            for text, is_thinking in llm.generate_stream_tagged(
                prompt,
                system=MEETING_TRANSLATION_SYSTEM,
                temperature=0.0,
                max_tokens=16384,
                thinking=False,
            ):
                if is_thinking or not text:
                    continue
                with st.cond:
                    if st.gen_state == "prefilling":
                        st.gen_state = "streaming"
                    st.accumulated += text
                    st.cond.notify_all()
            return st.accumulated.strip()

        def _run() -> None:
            key = f"{st.meeting_id}:{st.tab_id}:{st.lang}"
            try:
                llm = _resolve_meeting_llm()
                prompt = MEETING_TRANSLATION_PROMPT.format(
                    source_md=source_md,
                    target_language=TRANSLATION_LANG_NAMES[st.lang],
                )
                final = _stream_once(prompt, llm)

                # 数字保真校验（2026-09-17）：prompt 只是软约束——实测有模型把
                # 「N million」译成「N×10 万」（少一个数量级）。这里用代码兜：不对就
                # 带着差异重试一次（把缺失的量指出来，比笼统要求"检查数字"有效得多）。
                from src.meeting.number_fidelity import format_hint, missing_quantities

                missing = missing_quantities(source_md, final)
                if missing:
                    hint = format_hint(missing)
                    logger.warning(
                        "[TRANSLATE] number mismatch for %s: missing %s — retrying once",
                        key, hint,
                    )
                    retry_prompt = (
                        prompt
                        + "\n\nIMPORTANT: the previous attempt dropped or "
                        "altered these quantities: " + hint + ". "
                        "Re-translate the whole document and make sure every "
                        "number keeps its exact value (same magnitude)."
                    )
                    with st.cond:
                        st.accumulated = ""
                        st.gen_state = "prefilling"
                        st.cond.notify_all()
                    retried = _stream_once(retry_prompt, llm)
                    still = missing_quantities(source_md, retried)
                    if still:
                        # 重试仍不一致：保留重试稿（通常更接近），但留日志供排查。
                        logger.error(
                            "[TRANSLATE] number mismatch persists for %s: %s",
                            key, format_hint(still),
                        )
                    final = retried
                store.save_translation_md(st.meeting_id, st.tab_id, st.lang, final)
                with st.cond:
                    st.final = final
                    st.done = True
                    st.gen_state = "idle"
                    st.cond.notify_all()
            except Exception as exc:  # noqa: BLE001 — surfaced to client as error event
                logger.exception("[TRANSLATE-STREAM] generation failed for %s", key)
                with st.cond:
                    st.error = str(exc)
                    st.done = True
                    st.gen_state = "idle"
                    st.cond.notify_all()
            finally:
                with self._translation_stream_lock:
                    self._active_translation_streams.pop(key, None)

        threading.Thread(target=_run, daemon=True).start()
