"""Blueprint + section SSE generation — mixed into MeetingService."""
from __future__ import annotations

from src.prompts import (
    MEETING_BLUEPRINT_PROMPT,
    MEETING_BLUEPRINT_SYSTEM,
    MEETING_GENERAL_SUMMARY_PROMPT,
    MEETING_SUMMARIZER_V3_PROMPT,
    MEETING_SUMMARIZER_V3_SYSTEM,
    MEETING_TAGGER_V3_PROMPT,
    MEETING_TAGGER_V3_SYSTEM,
)


class MeetingGenerationMixin:

    def generate_blueprint_stream(self, meeting_id: str):
        """Stream blueprint generation as SSE event dicts.

        Two-pass pipeline with per-phase generation-state tracking:

        Pass 1 — General Summary (streaming):
          Uses generate_stream_tagged() to yield thinking and content
          tokens in real-time.  Thinking tokens are emitted as
          ``{"event": "thinking", ...}`` so the frontend can show them
          in a collapsible section that auto-hides when real content
          begins.  Generation states transition:
          idle → prefilling → streaming → idle.

        Pass 2 — Blueprint Decomposition (non-streaming):
          Runs after Pass 1 completes.  Emits ``blueprint_start`` and
          ``blueprint_done`` events.  State: idle → prefilling → idle.

        Deduplication: if a blueprint task is already running for this
        meeting, the new SSE connection attaches to the existing event
        queue instead of spawning duplicate LLM calls.

        Yields dicts::

          {"event": "state", "data": {"summary": "prefilling"}}
          {"event": "thinking", "data": "..."}
          {"event": "token", "data": "## Summary\\n..."}
          {"event": "state", "data": {"summary": "streaming"}}
          {"event": "summary_done", "data": {"title": "...", "general_md": "..."}}
          {"event": "state", "data": {"blueprint": "prefilling"}}
          {"event": "blueprint_done", "data": {"taxonomy": {...}, "blueprint": [...]}}
          {"event": "state", "data": {"blueprint": "idle"}}
          {"event": "error", "data": {"message": "..."}}
        """
        import queue
        import threading

        from src.meeting.models import ProcessingState, GenerationState

        # ── Deduplication: reuse existing task if one is running ────
        with self._blueprint_stream_lock:
            existing = self._active_blueprint_streams.get(meeting_id)
            if existing is not None:
                eq, thread = existing
                if thread.is_alive():
                    logger.info(
                        "[STREAM] Reusing existing blueprint task for %s",
                        meeting_id,
                    )
                    try:
                        while True:
                            try:
                                event_type, event_data = eq.get(timeout=0.1)
                            except queue.Empty:
                                continue
                            if event_type == "done":
                                break
                            yield {"event": event_type, "data": event_data}
                    except GeneratorExit:
                        logger.info(
                            "[STREAM] SSE client disconnected (reuse) for %s",
                            meeting_id,
                        )
                    return
                else:
                    del self._active_blueprint_streams[meeting_id]

        logger.info("[STREAM] Starting blueprint stream for meeting %s", meeting_id)

        # ── Build context (shared with _do_blueprint_summary) ──────
        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            yield {"event": "error", "data": {"message": "Meeting not found"}}
            return

        # Transcript
        sentences_data = store.get_sentences(meeting_id)
        if sentences_data:
            speaker_names: dict[str, str] = getattr(meeting, "speaker_names", None) or {}
            lines = []
            for s in sentences_data:
                sid = s.get("sentence_id", "")
                speaker = s.get("speaker", "")
                text = s.get("original_text", "")
                spk_name = speaker_names.get(speaker, "")
                if spk_name:
                    text = text.removeprefix(spk_name).strip()
                    text = text.removeprefix(":").strip()
                spk_part = f"[spk:{speaker}] " if speaker else ""
                lines.append(f"[{_num_id(sid)}] {spk_part}{text}")
            transcript_text = "\n".join(lines)
        else:
            transcript_result = store.get_transcript(meeting_id)
            transcript_text = (
                transcript_result.text if transcript_result else "(No transcript available)"
            )

        notes_text = store.get_notes(meeting_id) or "(No notes)"

        # Collection catalog
        alias_to_real: dict[str, str] = {}
        real_to_alias: dict[str, str] = {}
        collection_catalog = "No existing collections."
        try:
            from src.rag.summary_manager import SummaryManager
            sm = SummaryManager(db=services.db, vector_size=_detect_embedding_dim())
            sm.ensure_collection()
            project_descs = sm.get_all_project_descriptions()
            if project_descs:
                from src.collections.store import get_collection_meta, list_collections_meta
                existing_ids = {c["id"] for c in list_collections_meta()}
                catalog_lines = []
                stale_ids: list[str] = []
                alias_idx = 0
                for pd in project_descs:
                    cid = pd.get("collection_id", "")
                    cnt = pd.get("content", "")
                    if cid not in existing_ids:
                        stale_ids.append(cid)
                        continue
                    meta = get_collection_meta(cid)
                    display_name = meta.get("name", cid) if meta else cid
                    alias_idx += 1
                    alias = f"col_{alias_idx}"
                    alias_to_real[alias] = cid
                    real_to_alias[cid] = alias
                    catalog_lines.append(
                        f"- id: {alias}  |  name: {display_name}  |  description: {cnt}"
                    )
                for stale_cid in stale_ids:
                    try:
                        sm.delete_project_description(stale_cid)
                    except Exception:
                        pass
                catalog_lines.sort(key=lambda ln: ln)
                collection_catalog = "\n".join(catalog_lines)
        except Exception as e:
            logger.warning("[STREAM] Failed to build catalog: %s", e)

        # Hot words
        hot_words_text = "(None)"
        if meeting.hot_words_library_id:
            try:
                from src.hot_words.store import get_library
                lib = get_library(meeting.hot_words_library_id)
                if lib and lib.words:
                    hot_words_text = ", ".join(w.text for w in lib.words)
            except Exception:
                logger.warning("[STREAM] Failed to load hot words", exc_info=True)

        llm = _resolve_meeting_llm()
        think_summary = _thinking_for_meeting_call("summary")
        think_blueprint = _thinking_for_meeting_call("blueprint")
        think_effort = _meeting_thinking_effort()
        logger.info(
            "[STREAM] thinking summary=%s blueprint=%s effort=%s",
            think_summary,
            think_blueprint,
            think_effort,
        )

        # ── Build prompts ────────────────────────────────────
        summary_prompt = MEETING_GENERAL_SUMMARY_PROMPT.format(
            transcript=transcript_text,
            notes=notes_text,
            hot_words=hot_words_text,
        )
        blueprint_prompt = MEETING_BLUEPRINT_PROMPT.format(
            transcript=transcript_text,
            notes=notes_text,
            hot_words=hot_words_text,
            collection_catalog=collection_catalog,
        )
        all_sids = [s.get("sentence_id", "") for s in (sentences_data or [])]

        logger.info("[STREAM] Call 1 prompt: %d chars, Call 2 prompt: %d chars",
                    len(summary_prompt), len(blueprint_prompt))

        # ── Set initial state ─────────────────────────────────
        store.update_meeting(
            meeting_id,
            processing_state=ProcessingState.summarizing.value,
            summary_gen_state=GenerationState.prefilling.value,
        )

        # ── Helper: parse blueprint LLM response ──────────────
        def _process_raw_blueprint(raw_bp: str) -> dict:
            bp_data = _parse_json_response(raw_bp, ["taxonomy", "blueprint"])
            parsed_title = bp_data.get("title", "")
            blueprint_raw = bp_data.get("blueprint", [])
            taxonomy = bp_data.get("taxonomy", None)
            if not isinstance(blueprint_raw, list):
                blueprint_raw = []
            blueprint_raw.sort(key=lambda item: item.get("tab_name", ""))
            bp_list: list[dict] = []
            for idx, item in enumerate(blueprint_raw):
                bp_name = item.get("tab_name", f"Section {idx + 1}")
                if bp_name.strip().lower() == "other":
                    continue
                real_cid = alias_to_real.get(item.get("associated_collection_id", ""), "")
                bp_list.append({
                    "blueprint_id": f"bp_{idx + 1:02d}",
                    "tab_name": bp_name,
                    "tab_description": item.get("section_description", "")[:600],
                    "associated_collection_id": real_cid,
                    "associated_collection_name": item.get("associated_collection_name", ""),
                })
            return {
                "bp_data": bp_data,
                "blueprint": bp_list,
                "taxonomy": taxonomy,
                "title": parsed_title,
            }

        # ── Background thread: runs LLM, persists results ─────
        import queue
        import threading
        import concurrent.futures

        event_queue: queue.Queue = queue.Queue()

        def _run_llm() -> None:
            """All LLM interaction + persistence.  Survives SSE disconnect."""
            bp_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                # ── Pass 1: General Summary ────────────────
                event_queue.put(("state", {"summary": "prefilling"}))
                logger.info("[STREAM-THREAD] Call 1 starting for meeting %s", meeting_id)

                accumulated = ""
                in_thinking = True
                bp_future: concurrent.futures.Future | None = None
                _bp_emitted = False
                _bp_cache: dict | None = None

                for text, is_thinking in llm.generate_stream_tagged(
                    summary_prompt,
                    system=MEETING_BLUEPRINT_SYSTEM,
                    max_tokens=_thinking_max_tokens(32768, think_summary),
                    temperature=0.0,
                    thinking=think_summary,
                    thinking_effort=think_effort if think_summary else None,
                ):
                    if is_thinking:
                        event_queue.put(("thinking", text))
                    else:
                        if in_thinking:
                            in_thinking = False
                            store.update_meeting(
                                meeting_id,
                                summary_gen_state=GenerationState.streaming.value,
                                blueprint_gen_state=GenerationState.prefilling.value,
                            )
                            event_queue.put(("state", {"summary": "streaming", "blueprint": "prefilling"}))
                            logger.info("[STREAM-THREAD] Call 1 streaming — launching Call 2 in parallel")
                            bp_future = bp_executor.submit(
                                llm.generate,
                                prompt=blueprint_prompt,
                                system=MEETING_BLUEPRINT_SYSTEM,
                                temperature=0.0,
                                max_tokens=_thinking_max_tokens(8192, think_blueprint),
                                response_format={"type": "json_object"},
                                thinking=think_blueprint,
                                thinking_effort=think_effort,
                            )
                        accumulated += text
                        event_queue.put(("token", text))

                    # Early-completion check
                    if bp_future is not None and bp_future.done() and not _bp_emitted:
                        raw_bp = bp_future.result()
                        parsed = _process_raw_blueprint(raw_bp)
                        bp_count = len(parsed.get("blueprint", []))
                        if bp_count == 0:
                            logger.warning(
                                "[STREAM-THREAD] Call 2 early — EMPTY blueprint "
                                "(raw=%d chars, first 400: %.400r)",
                                len(raw_bp), raw_bp[:400],
                            )
                        else:
                            logger.info(
                                "[STREAM-THREAD] Call 2 finished early — %d blueprint items",
                                bp_count,
                            )
                        _bp_cache = parsed
                        _bp_emitted = True
                        store.update_meeting(
                            meeting_id,
                            blueprint=parsed["blueprint"],
                            blueprint_taxonomy=parsed["taxonomy"],
                            blueprint_gen_state=GenerationState.idle.value,
                        )
                        event_queue.put(("blueprint_done", {
                            "taxonomy": parsed["taxonomy"],
                            "blueprint": parsed["blueprint"],
                        }))
                        event_queue.put(("state", {"blueprint": "idle"}))

                logger.info("[STREAM-THREAD] Call 1 done: %d chars", len(accumulated))
                general_md = accumulated.strip()
                general_md = _clean_refs(
                    _normalize_refs(_normalize_brackets(general_md)), all_sids,
                )

                # Persist Call 1 result (content lives in tab_general.md, not meta.json)
                general_tab_path = store.save_section_md(meeting_id, "tab_general", general_md)
                store.update_meeting(
                    meeting_id,
                    summary_gen_state=GenerationState.idle.value,
                )
                event_queue.put(("summary_done", {"general_md": general_md}))
                event_queue.put(("state", {"summary": "idle"}))

                # ── Call 2 result ───────────────────────────
                if not _bp_emitted:
                    try:
                        if bp_future is not None:
                            logger.info("[STREAM-THREAD] Waiting for Call 2...")
                            raw_blueprint = bp_future.result()
                        else:
                            raw_blueprint = llm.generate(
                                blueprint_prompt,
                                system=MEETING_BLUEPRINT_SYSTEM,
                                max_tokens=_thinking_max_tokens(8192, think_blueprint),
                                temperature=0.0,
                                thinking=think_blueprint,
                                thinking_effort=think_effort,
                                response_format={"type": "json_object"},
                            )
                        logger.info("[STREAM-THREAD] Call 2 done: %d chars", len(raw_blueprint))
                        parsed = _process_raw_blueprint(raw_blueprint)
                        if not parsed.get("blueprint"):
                            logger.warning(
                                "[STREAM-THREAD] Call 2 produced empty blueprint "
                                "(raw=%d chars, first 300: %.300r)",
                                len(raw_blueprint), raw_blueprint[:300],
                            )
                    except Exception as bp_exc:
                        logger.exception(
                            "[STREAM-THREAD] Call 2 FAILED (summary preserved): %s", bp_exc,
                        )
                        parsed = {"bp_data": {}, "blueprint": [], "taxonomy": None, "title": ""}
                else:
                    parsed = _bp_cache or {}
                    logger.info("[STREAM-THREAD] Call 2 was emitted early — reusing cache")

                bp_data = parsed.get("bp_data", {})
                parsed_title = parsed.get("title", "")
                blueprint = parsed.get("blueprint", [])
                taxonomy = parsed.get("taxonomy", None)

                # ── Build tabs ──────────────────────────────
                old_tabs: list[dict] = list(meeting.tabs or [])
                is_re_summarize = any(
                    (t["tab_id"] if isinstance(t, dict) else t.tab_id) != "tab_general"
                    for t in old_tabs
                )
                tabs: list[dict] = [{
                    "tab_id": "tab_general", "type": "general",
                    "blueprint_id": "", "name": "General", "description": "",
                    "processing_state": "idle",
                    "associated_collection_id": "", "associated_collection_name": "",
                    "allocated_file_id": "", "is_dirty": False,
                    "md_file_path": general_tab_path, "payload_ref": [],
                }]
                old_section_tabs: list[dict] = []
                for t in old_tabs:
                    td = t if isinstance(t, dict) else (
                        t.model_dump() if hasattr(t, "model_dump") else dict(t)
                    )
                    tid = td.get("tab_id", "")
                    if tid == "tab_general":
                        continue
                    if is_re_summarize:
                        td["blueprint_id"] = ""
                    td.setdefault("blueprint_id", "")
                    td.setdefault("description", td.get("description", ""))
                    td.setdefault("processing_state", "idle")
                    td.setdefault("allocated_file_id", "")
                    td.setdefault("is_dirty", False)
                    old_section_tabs.append(td)
                matched_old: set[int] = set()
                for bp_entry in blueprint:
                    bp_cid = bp_entry["associated_collection_id"]
                    for ot in old_section_tabs:
                        if id(ot) in matched_old:
                            continue
                        if bp_cid and ot.get("associated_collection_id") == bp_cid:
                            ot["blueprint_id"] = bp_entry["blueprint_id"]
                            ot["name"] = bp_entry["tab_name"]
                            ot["description"] = bp_entry["tab_description"]
                            matched_old.add(id(ot))
                            break
                tabs.extend(old_section_tabs)

                # ── Final persist ───────────────────────────
                update_fields: dict = dict(
                    blueprint=blueprint,
                    blueprint_taxonomy=taxonomy,
                    tabs=tabs,
                    processing_state=ProcessingState.idle.value,
                    blueprint_gen_state=GenerationState.idle.value,
                )
                if parsed_title:
                    update_fields["title"] = parsed_title
                store.update_meeting(meeting_id, **update_fields)

                if not _bp_emitted:
                    event_queue.put(("blueprint_done", {
                        "taxonomy": taxonomy,
                        "blueprint": blueprint,
                    }))
                    event_queue.put(("state", {"blueprint": "idle"}))
                logger.info("[STREAM-THREAD] Complete for meeting %s", meeting_id)

            except Exception as e:
                logger.exception("[STREAM-THREAD] Failed for meeting %s: %s", meeting_id, e)
                store.update_meeting(
                    meeting_id,
                    processing_state=ProcessingState.idle.value,
                    summary_gen_state=GenerationState.idle.value,
                    blueprint_gen_state=GenerationState.idle.value,
                )
                event_queue.put(("error", {"message": str(e)}))
            finally:
                bp_executor.shutdown(wait=False)
                event_queue.put(("done", None))
                with self._blueprint_stream_lock:
                    self._active_blueprint_streams.pop(meeting_id, None)

        # ── Launch thread ────────────────────────────────────────
        thread = threading.Thread(target=_run_llm, daemon=True)
        with self._blueprint_stream_lock:
            self._active_blueprint_streams[meeting_id] = (event_queue, thread)
        thread.start()

        # ── Read queue → SSE events ──────────────────────────────
        try:
            while True:
                try:
                    event_type, event_data = event_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if event_type == "done":
                    break
                yield {"event": event_type, "data": event_data}
        except GeneratorExit:
            logger.info(
                "[STREAM] SSE client disconnected for meeting %s — LLM continues in background",
                meeting_id,
            )

    def _do_blueprint_summary(self, meeting_id: str) -> None:
        """Node 0.3 v3: generate General summary + decomposition blueprint.

        Single-pass LLM call → {title, general_md_content, blueprint[]}.
        Blueprint IDs are code-assigned (bp_01, bp_02, ...).
        On re-summarize, existing section tabs keep their tab_id but
        clear their blueprint_id linkage.
        """
        from src.meeting.models import ProcessingState

        logger.info("[BLUEPRINT] Starting for meeting %s", meeting_id)
        try:
            meeting = store.get_meeting(meeting_id)
            if meeting is None:
                return

            # ── Build transcript: [stt_XXXX] [spk:ID] {text} ──────
            sentences_data = store.get_sentences(meeting_id)

            if sentences_data:
                # Resolve speaker names to strip them from original_text
                speaker_names: dict[str, str] = getattr(meeting, "speaker_names", None) or {}
                lines = []
                for s in sentences_data:
                    sid = s.get("sentence_id", "")
                    speaker = s.get("speaker", "")
                    text = s.get("original_text", "")
                    # Strip speaker name prefix from text (STT may include it)
                    spk_name = speaker_names.get(speaker, "")
                    if spk_name:
                        text = text.removeprefix(spk_name).strip()
                        text = text.removeprefix(":").strip()
                    spk_part = f"[spk:{speaker}] " if speaker else ""
                    lines.append(f"[{_num_id(sid)}] {spk_part}{text}")
                transcript_text = "\n".join(lines)
            else:
                transcript_result = store.get_transcript(meeting_id)
                transcript_text = (
                    transcript_result.text
                    if transcript_result
                    else "(No transcript available)"
                )

            notes_text = store.get_notes(meeting_id) or "(No notes)"
            logger.info(
                "[BLUEPRINT] Transcript: %d chars, Notes: %d chars",
                len(transcript_text),
                len(notes_text),
            )

            # ── Build collection catalog ──────────────────────────
            # Alias real collection IDs → col_1, col_2, ... so the LLM
            # never sees UUIDs that it might hallucinate or truncate.
            alias_to_real: dict[str, str] = {}
            real_to_alias: dict[str, str] = {}
            collection_catalog = "No existing collections."
            try:
                from src.rag.summary_manager import SummaryManager

                sm = SummaryManager(
                    db=services.db, vector_size=_detect_embedding_dim()
                )
                sm.ensure_collection()
                project_descs = sm.get_all_project_descriptions()
                if project_descs:
                    from src.collections.store import get_collection_meta, list_collections_meta

                    existing_ids = {c["id"] for c in list_collections_meta()}
                    catalog_lines = []
                    stale_ids: list[str] = []
                    alias_idx = 0
                    for pd in project_descs:
                        cid = pd.get("collection_id", "")
                        cnt = pd.get("content", "")
                        if cid not in existing_ids:
                            stale_ids.append(cid)
                            continue
                        meta = get_collection_meta(cid)
                        display_name = meta.get("name", cid) if meta else cid
                        alias_idx += 1
                        alias = f"col_{alias_idx}"
                        alias_to_real[alias] = cid
                        real_to_alias[cid] = alias
                        catalog_lines.append(
                            f"- id: {alias}  |  name: {display_name}  |  description: {cnt}"
                        )
                    for stale_cid in stale_ids:
                        try:
                            sm.delete_project_description(stale_cid)
                        except Exception:
                            pass
                    # Sort for deterministic ordering — Qdrant scroll is unordered
                    catalog_lines.sort(key=lambda ln: ln)
                    collection_catalog = "\n".join(catalog_lines)
                    logger.info(
                        "[BLUEPRINT] Catalog contents (%d aliases):\n%s",
                        len(alias_to_real), collection_catalog)
                    logger.info(
                        "[BLUEPRINT] Found %d collections for catalog (%d stale cleaned)",
                        len(catalog_lines),
                        len(stale_ids),
                    )
            except Exception as e:
                logger.warning("[BLUEPRINT] Failed to build collection catalog: %s", e)

            # ── Hot words ─────────────────────────────────────────
            hot_words_text = "(None)"
            if meeting.hot_words_library_id:
                try:
                    from src.hot_words.store import get_library

                    lib = get_library(meeting.hot_words_library_id)
                    if lib and lib.words:
                        hot_words_text = ", ".join(w.text for w in lib.words)
                except Exception:
                    logger.warning("[BLUEPRINT] Failed to load hot words", exc_info=True)

            llm = _resolve_meeting_llm()
            think_summary = _thinking_for_meeting_call("summary")
            think_blueprint = _thinking_for_meeting_call("blueprint")
            think_effort = _meeting_thinking_effort()

            # ── Call 1: General Summary (no collection catalog) ───
            # Isolated from catalog so collection descriptions cannot
            # bias the Summary wording.
            summary_prompt = MEETING_GENERAL_SUMMARY_PROMPT.format(
                transcript=transcript_text,
                notes=notes_text,
                hot_words=hot_words_text,
            )
            logger.info("[SUMMARY] Calling LLM with %d char prompt...", len(summary_prompt))

            raw_summary = llm.generate(
                summary_prompt,
                system=MEETING_BLUEPRINT_SYSTEM,
                max_tokens=_thinking_max_tokens(32768, think_summary),
                temperature=0.0,
                thinking=think_summary,
                thinking_effort=think_effort if think_summary else None,
            )
            logger.info("[SUMMARY] LLM returned %d chars", len(raw_summary))

            # Call 1 outputs raw markdown directly — no JSON wrapper
            general_md = raw_summary.strip()
            parsed_title = ""  # title comes from Call 2
            logger.info("[SUMMARY] general_md=%d chars", len(general_md))

            # ── Call 2: Blueprint Decomposition (with catalog) ────
            # Focused purely on classification — no Summary task
            # competing for attention.  Shares transcript prefix with
            # Call 1 for prefix-cache hits.
            blueprint_prompt = MEETING_BLUEPRINT_PROMPT.format(
                transcript=transcript_text,
                notes=notes_text,
                hot_words=hot_words_text,
                collection_catalog=collection_catalog,
            )
            logger.info("[BLUEPRINT] Calling LLM with %d char prompt...", len(blueprint_prompt))

            raw_blueprint = llm.generate(
                blueprint_prompt,
                system=MEETING_BLUEPRINT_SYSTEM,
                max_tokens=_thinking_max_tokens(8192, think_blueprint),
                temperature=0.0,
                thinking=think_blueprint,
                thinking_effort=think_effort,
                response_format={"type": "json_object"},
            )
            logger.info("[BLUEPRINT] LLM returned %d chars", len(raw_blueprint))

            bp_data = _parse_json_response(raw_blueprint, ["taxonomy", "blueprint"])
            parsed_title = bp_data.get("title", "") or parsed_title
            blueprint_raw = bp_data.get("blueprint", [])
            taxonomy = bp_data.get("taxonomy", None)
            if not isinstance(blueprint_raw, list):
                blueprint_raw = []
            logger.info(
                "[BLUEPRINT] Parsed: blueprint=%d sections, taxonomy=%s, title='%s'",
                len(blueprint_raw),
                taxonomy.get("dimension", "") if taxonomy else "",
                parsed_title,
            )

            # ── Validate sentence refs ────────────────────────────
            all_sids = [s.get("sentence_id", "") for s in (sentences_data or [])]
            general_md = _clean_refs(_normalize_refs(_normalize_brackets(general_md)), all_sids)

            # ── Build blueprint entries (v3: blueprint_id = bp_XX) ──
            # Sort by tab_name for deterministic bp_XX assignment
            blueprint_raw.sort(key=lambda item: item.get("tab_name", ""))
            blueprint: list[dict] = []
            for idx, item in enumerate(blueprint_raw):
                bp_id = f"bp_{idx + 1:02d}"
                bp_name = item.get("tab_name", f"Section {idx + 1}")
                # Skip "Other" from blueprint entirely
                if bp_name.strip().lower() == "other":
                    continue
                # Map alias back to real collection ID (safe: unknown aliases → "")
                raw_cid = item.get("associated_collection_id", "")
                real_cid = alias_to_real.get(raw_cid, "")
                if raw_cid and not real_cid:
                    logger.warning(
                        "[BLUEPRINT] Unknown collection alias '%s' in LLM response — cleared",
                        raw_cid,
                    )
                bp_entry = {
                    "blueprint_id": bp_id,
                    "tab_name": bp_name,
                    "tab_description": item.get("section_description", "")[:600],
                    "associated_collection_id": real_cid,
                    "associated_collection_name": item.get(
                        "associated_collection_name", ""
                    ),
                }
                blueprint.append(bp_entry)

            # ── Build tabs: preserve existing section tabs ────────
            old_tabs: list[dict] = list(meeting.tabs or [])
            is_re_summarize = any(
                (t["tab_id"] if isinstance(t, dict) else t.tab_id) != "tab_general"
                for t in old_tabs
            )

            tabs: list[dict] = []
            general_tab_path = store.save_section_md(
                meeting_id, "tab_general", general_md
            )
            tabs.append(
                {
                    "tab_id": "tab_general",
                    "type": "general",
                    "blueprint_id": "",
                    "name": "General",
                    "description": "",
                    "processing_state": "idle",
                    "associated_collection_id": "",
                    "associated_collection_name": "",
                    "allocated_file_id": "",
                    "is_dirty": False,
                    "md_file_path": general_tab_path,
                    "payload_ref": [],
                }
            )

            for t in old_tabs:
                tid = t["tab_id"] if isinstance(t, dict) else (
                    t.tab_id if hasattr(t, "tab_id") else ""
                )
                if tid == "tab_general":
                    continue
                td = t if isinstance(t, dict) else (
                    t.model_dump() if hasattr(t, "model_dump") else dict(t)
                )
                # On re-summarize: clear blueprint_id, keep everything else
                if is_re_summarize:
                    td["blueprint_id"] = ""
                # Ensure v3 fields exist for legacy tabs
                td.setdefault("blueprint_id", "")
                td.setdefault("description", td.get("description", ""))
                td.setdefault("processing_state", "idle")
                td.setdefault("allocated_file_id", "")
                td.setdefault("is_dirty", False)
                tabs.append(td)

            # ── Persist (content lives in tab_general.md, not meta.json) ─
            update_fields: dict = dict(
                blueprint=blueprint,
                blueprint_taxonomy=taxonomy,
                tabs=tabs,
                processing_state=ProcessingState.idle.value,
            )
            # Create-time title keeps date (routes / create dialog). After summary
            # use LLM topic only — no datetime prefix (same as stream path).
            if parsed_title:
                update_fields["title"] = parsed_title.strip()

            store.update_meeting(meeting_id, **update_fields)
            logger.info(
                "[BLUEPRINT] Done for meeting %s: %d blueprint items, %d tabs",
                meeting_id, len(blueprint), len(tabs),
            )

        except Exception as e:
            logger.error("[BLUEPRINT] Failed for meeting %s: %s", meeting_id, e, exc_info=True)
            store.update_meeting(
                meeting_id,
                processing_state=ProcessingState.idle.value,
            )

    def generate_section_stream(self, meeting_id: str, tab_id: str):
        """Stream single-section generation as SSE event dicts (Tagger → Summarizer).

        Runs Tagger (non-streaming) first, then streams Summarizer output via
        ``generate_stream_tagged``.  All LLM work happens in a background thread;
        the main generator reads from a queue so the SSE connection can drop
        without cancelling the work.

        Deduplication: if a generation task is already running for this
        ``(meeting_id, tab_id)``, the new SSE connection attaches to the
        existing event queue instead of spawning a duplicate LLM call.

        Yields dicts::

          {"event": "state", "data": {"section_gen": "prefilling"}}
          {"event": "thinking", "data": "..."}
          {"event": "token", "data": "..."}
          {"event": "state", "data": {"section_gen": "streaming"}}
          {"event": "section_done", "data": {"tab_id": "...", "md": "..."}}
          {"event": "error", "data": {"message": "..."}}
        """
        import queue
        import threading
        import re as _re

        from src.meeting.pipeline import build_payload
        from src.meeting.schemas import Sentence

        # ── Deduplication: reuse existing task if one is running ────────
        task_key = f"{meeting_id}:{tab_id}"
        with self._section_stream_lock:
            existing = self._active_section_streams.get(task_key)
            if existing is not None:
                eq, thread = existing
                if thread.is_alive():
                    logger.info(
                        "[SECTION-STREAM] Reusing existing task for %s", task_key,
                    )
                    try:
                        while True:
                            try:
                                event_type, event_data = eq.get(timeout=0.1)
                            except queue.Empty:
                                continue
                            if event_type == "done":
                                break
                            yield {"event": event_type, "data": event_data}
                    except GeneratorExit:
                        logger.info(
                            "[SECTION-STREAM] SSE client disconnected (reuse) for %s",
                            task_key,
                        )
                    return
                else:
                    # Thread finished — clean up stale entry
                    del self._active_section_streams[task_key]

        event_queue: queue.Queue = queue.Queue()

        # Register this task so reconnecting SSE clients can reuse it
        def _run() -> None:
            try:
                # ── Load context ──────────────────────────────────
                meeting = store.get_meeting(meeting_id)
                if meeting is None:
                    event_queue.put(("error", {"message": "Meeting not found"}))
                    return

                # Find tab metadata
                tab_meta: dict | None = None
                for t in (meeting.tabs or []):
                    td = t if isinstance(t, dict) else t.model_dump()
                    if td.get("tab_id") == tab_id:
                        tab_meta = td
                        break
                if tab_meta is None:
                    event_queue.put(("error", {"message": f"Tab '{tab_id}' not found"}))
                    return

                section_name = tab_meta.get("name", "")
                section_desc = tab_meta.get("description", "")

                sentences_data = store.get_sentences(meeting_id)
                if not sentences_data:
                    event_queue.put(("error", {"message": "No sentences data"}))
                    return

                sentences = [
                    Sentence(**s) if isinstance(s, dict) else s
                    for s in sentences_data
                ]
                id_to_sentence: dict[str, Sentence] = {
                    s.sentence_id: s for s in sentences
                }

                # ── Build full transcript ─────────────────────────
                speaker_names: dict[str, str] = getattr(meeting, "speaker_names", None) or {}
                transcript_lines = []
                for s in sentences_data:
                    sid = s.get("sentence_id", "")
                    speaker = s.get("speaker", "")
                    text = s.get("original_text", "")
                    spk_name = speaker_names.get(speaker, "")
                    if spk_name:
                        text = text.removeprefix(spk_name).strip()
                        text = text.removeprefix(":").strip()
                    spk_part = f"[spk:{speaker}] " if speaker else ""
                    transcript_lines.append(f"[{_num_id(sid)}] {spk_part}{text}")
                full_transcript = "\n".join(transcript_lines)

                # ── Other sections text ───────────────────────────
                existing_tabs: list[dict] = list(meeting.tabs or [])
                blueprint = meeting.blueprint or []
                _all_tab_names: set[str] = set()
                for t in existing_tabs:
                    nm = t.get("name", "") if isinstance(t, dict) else getattr(t, "name", "")
                    if nm:
                        _all_tab_names.add(nm)

                def _other_sections_text(exclude_tid: str) -> str:
                    others = []
                    for t in existing_tabs:
                        tid = t["tab_id"] if isinstance(t, dict) else t.tab_id
                        if tid == exclude_tid or tid == "tab_general":
                            continue
                        md = t.get("md_file_path", "") if isinstance(t, dict) else getattr(t, "md_file_path", "")
                        if not md:
                            continue  # not yet extracted — skip
                        nm = t.get("name", "") if isinstance(t, dict) else getattr(t, "name", "")
                        dc = t.get("description", "") if isinstance(t, dict) else getattr(t, "description", "")
                        others.append(f"- {nm}: {dc}" if dc else f"- {nm}")
                    return "\n".join(others) if others else "(No other sections)"

                # ── Hot words ─────────────────────────────────
                hot_words_text = "(None)"
                if meeting.hot_words_library_id:
                    try:
                        from src.hot_words.store import get_library
                        lib = get_library(meeting.hot_words_library_id)
                        if lib and lib.words:
                            hot_words_text = ", ".join(w.text for w in lib.words)
                    except Exception:
                        logger.warning("[SECTION-STREAM] Failed to load hot words", exc_info=True)

                other_secs = _other_sections_text(tab_id)

                # ── Short-ID → full-ID lookup ─────────────────────
                short_to_full: dict[str, str] = {}
                for fid in id_to_sentence:
                    parts = fid.rsplit("_stt_", 1)
                    if len(parts) == 2:
                        short_to_full["stt_" + parts[1]] = fid

                llm = _resolve_meeting_llm()
                think_tagger = _thinking_for_meeting_call("tagger")
                think_summarizer = _thinking_for_meeting_call("summarizer")
                think_effort = _meeting_thinking_effort()

                # ── Phase 1: Tagger (non-streaming) ───────────────
                event_queue.put(("state", {"section_gen": "prefilling"}))
                logger.info("[SECTION-STREAM] Tagger starting for %s/%s", meeting_id, tab_id)

                tagger_prompt = MEETING_TAGGER_V3_PROMPT.format(
                    transcript=full_transcript,
                    hot_words=hot_words_text,
                    other_sections=other_secs,
                    section_name=section_name,
                    section_description=section_desc,
                )
                tagged_short_ids: list[str] = []
                for attempt in range(3):
                    try:
                        raw = llm.generate(
                            tagger_prompt,
                            system=MEETING_TAGGER_V3_SYSTEM,
                            max_tokens=_thinking_max_tokens(16384, think_tagger),
                            temperature=0.0,
                            thinking=think_tagger,
                            thinking_effort=think_effort if think_tagger else None,
                            response_format={"type": "json_object"},
                        )
                        parsed = _parse_tagger_response(raw)
                        tagged_short_ids = parsed.get("sentence_ids", [])
                        logger.info(
                            "[SECTION-STREAM] Tagger for '%s': %d sentences tagged",
                            section_name, len(tagged_short_ids),
                        )
                        break
                    except Exception as exc:
                        logger.warning(
                            "[SECTION-STREAM] Tagger attempt %d/3 for '%s': %s",
                            attempt + 1, section_name, exc,
                        )
                        if attempt < 2:
                            import time
                            time.sleep(2 ** attempt)
                else:
                    event_queue.put(("error", {"message": f"Tagger failed for '{section_name}'"}))
                    # Persist idle state so UI unsticks
                    self._persist_section_idle(meeting_id, tab_id)
                    return

                if not tagged_short_ids:
                    logger.warning("[SECTION-STREAM] No sentences tagged for '%s'", section_name)
                    # Persist empty result so UI unsticks
                    placeholder = f"# {section_name}\n\nNo relevant sentences found in the transcript."
                    md_path = store.save_section_md(meeting_id, tab_id, placeholder)
                    store.apply_section_tags(meeting_id, tab_id, [])
                    self._persist_section_done(meeting_id, tab_id, md_path, [])
                    event_queue.put(("section_done", {"tab_id": tab_id, "md": placeholder}))
                    return

                # Convert short IDs → full IDs
                full_tagged_ids: set[str] = set()
                for sid in tagged_short_ids:
                    full = short_to_full.get(sid, sid)
                    full_tagged_ids.add(full)

                # ── Build payload ─────────────────────────────────
                payload_ids = build_payload(
                    full_tagged_ids, sentences, radius=3, gap_threshold=10.0,
                )
                if not payload_ids:
                    logger.warning("[SECTION-STREAM] Empty payload for '%s'", section_name)
                    placeholder = f"# {section_name}\n\nNo relevant context found."
                    md_path = store.save_section_md(meeting_id, tab_id, placeholder)
                    store.apply_section_tags(meeting_id, tab_id, [])
                    self._persist_section_done(meeting_id, tab_id, md_path, [])
                    event_queue.put(("section_done", {"tab_id": tab_id, "md": placeholder}))
                    return

                # Merge FOCUS + NEARBY in chronological order
                merged_lines = []
                for pid in payload_ids:
                    sent = id_to_sentence.get(pid)
                    if sent is None:
                        continue
                    spk = sent.speaker
                    line = f"[{_num_id(pid)}] [spk:{spk}] {sent.original_text}"
                    if pid in full_tagged_ids:
                        merged_lines.append(f"[FOCUS] {line}")
                    else:
                        merged_lines.append(line)
                merged_text = "\n".join(merged_lines) if merged_lines else "(No sentences)"

                # ── Phase 2: Summarizer (streaming) ───────────────
                summarizer_prompt = MEETING_SUMMARIZER_V3_PROMPT.format(
                    transcript=full_transcript,
                    hot_words=hot_words_text,
                    other_sections=other_secs,
                    section_name=section_name,
                    section_description=section_desc,
                    merged_sentences=merged_text,
                )
                logger.info(
                    "[SECTION-STREAM] Summarizer starting for '%s' (prompt=%d chars, payload=%d)",
                    section_name, len(summarizer_prompt), len(payload_ids),
                )

                streaming_started = False
                accumulated = ""
                for text, is_thinking in llm.generate_stream_tagged(
                    summarizer_prompt,
                    system=MEETING_SUMMARIZER_V3_SYSTEM,
                    max_tokens=_thinking_max_tokens(8192, think_summarizer),
                    thinking=think_summarizer,
                    thinking_effort=think_effort if think_summarizer else None,
                ):
                    if is_thinking:
                        event_queue.put(("thinking", text))
                    else:
                        if not streaming_started:
                            streaming_started = True
                            event_queue.put(("state", {"section_gen": "streaming"}))
                        accumulated += text
                        event_queue.put(("token", text))

                logger.info(
                    "[SECTION-STREAM] Summarizer done for '%s': %d chars",
                    section_name, len(accumulated),
                )

                # ── Validate & persist ────────────────────────────
                validated = _clean_refs(
                    _normalize_refs(_normalize_brackets(accumulated.strip())),
                    list(payload_ids),
                )
                md_path = store.save_section_md(meeting_id, tab_id, validated)

                # Merge this tab's tags under the meeting lock so parallel
                # section streams do not wipe each other's T1/T2 labels.
                store.apply_section_tags(meeting_id, tab_id, list(payload_ids))

                self._persist_section_done(meeting_id, tab_id, md_path, list(payload_ids))
                event_queue.put(("section_done", {"tab_id": tab_id, "md": validated}))
                logger.info("[SECTION-STREAM] Complete for %s/%s", meeting_id, tab_id)

            except Exception as e:
                logger.exception("[SECTION-STREAM] Failed for %s/%s: %s", meeting_id, tab_id, e)
                self._persist_section_idle(meeting_id, tab_id)
                event_queue.put(("error", {"message": str(e)}))
            finally:
                event_queue.put(("done", None))
                with self._section_stream_lock:
                    self._active_section_streams.pop(task_key, None)

        # ── Launch background thread ──────────────────────────────
        thread = threading.Thread(target=_run, daemon=True)
        with self._section_stream_lock:
            self._active_section_streams[task_key] = (event_queue, thread)
        thread.start()

        # ── Read queue → SSE events ──────────────────────────────
        try:
            while True:
                try:
                    event_type, event_data = event_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if event_type == "done":
                    break
                yield {"event": event_type, "data": event_data}
        except GeneratorExit:
            logger.info(
                "[SECTION-STREAM] SSE client disconnected for %s/%s — LLM continues in background",
                meeting_id, tab_id,
            )

    def _persist_section_done(
        self, meeting_id: str, tab_id: str, md_path: str, payload_ids: list[str],
    ) -> None:
        """Persist completed section result to meeting store."""
        from src.meeting.models import ProcessingState
        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            return
        tabs: list[dict] = list(meeting.tabs or [])
        all_idle = True
        for t in tabs:
            tid = t["tab_id"] if isinstance(t, dict) else t.tab_id
            if tid == tab_id:
                t["md_file_path"] = md_path
                t["payload_ref"] = payload_ids
                t["processing_state"] = "idle"
                t["is_dirty"] = False
            elif t.get("processing_state") == "generating":
                all_idle = False
        if all_idle:
            store.update_meeting(
                meeting_id, tabs=tabs,
                processing_state=ProcessingState.idle.value,
            )
        else:
            store.update_meeting(meeting_id, tabs=tabs)

    def _persist_section_idle(self, meeting_id: str, tab_id: str) -> None:
        """Reset a section's processing_state to idle (on error)."""
        from src.meeting.models import ProcessingState
        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            return
        tabs: list[dict] = list(meeting.tabs or [])
        all_idle = True
        for t in tabs:
            tid = t["tab_id"] if isinstance(t, dict) else t.tab_id
            if tid == tab_id:
                t["processing_state"] = "idle"
            elif t.get("processing_state") == "generating":
                all_idle = False
        if all_idle:
            store.update_meeting(
                meeting_id, tabs=tabs,
                processing_state=ProcessingState.idle.value,
            )
        else:
            store.update_meeting(meeting_id, tabs=tabs)
