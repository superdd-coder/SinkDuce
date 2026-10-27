"""Section allocate / unregister — mixed into MeetingService."""
from __future__ import annotations

class MeetingAllocateMixin:

    @staticmethod
    def _delete_allocation(
        collection: str,
        file_id: str,
        *,
        meeting_id: str | None = None,
        detach_anchor: bool = True,
    ) -> None:
        """Delete an allocation: Qdrant, SQLite, disk; leftover files.json.

        Source is read from ``files.source`` first, then old ``files.json``.
        When *meeting_id* is set and *detach_anchor*, detaches the file from
        the meeting timeline anchor and deletes the node if empty.
        """
        try:
            source = ""
            try:
                from src.file_mgmt.service import _open_db

                conn_src = _open_db(collection)
                try:
                    row = conn_src.execute(
                        "SELECT source FROM files WHERE file_id=?", (file_id,)
                    ).fetchone()
                    if row and row["source"]:
                        source = str(row["source"]).strip()
                finally:
                    conn_src.close()
            except Exception:
                logger.debug(
                    "allocation source sqlite lookup skipped file=%s",
                    file_id[:12] if file_id else "",
                    exc_info=True,
                )
            if not source:
                from src.collections.file_index import load as load_file_index

                idx = load_file_index(collection) or {}
                source = str((idx.get(file_id) or {}).get("source") or "").strip()

            # Detach from meeting timeline anchor(s) before purging file rows
            if detach_anchor and meeting_id and file_id:
                try:
                    from src.file_mgmt.service import (
                        delete_meeting_anchor_if_empty,
                        detach_file_from_node,
                        meeting_external_ref,
                        _open_db,
                    )

                    ref = meeting_external_ref(meeting_id)
                    conn = _open_db(collection)
                    try:
                        nodes = conn.execute(
                            """SELECT n.node_id FROM file_nodes fn
                               JOIN nodes n ON n.node_id = fn.node_id
                               WHERE fn.file_id=? AND n.external_ref=?""",
                            (file_id, ref),
                        ).fetchall()
                        # Fallback: any attach of this file
                        if not nodes:
                            nodes = conn.execute(
                                "SELECT node_id FROM file_nodes WHERE file_id=?",
                                (file_id,),
                            ).fetchall()
                        node_ids = [r["node_id"] for r in nodes]
                    finally:
                        conn.close()
                    for node_id in node_ids:
                        try:
                            detach_file_from_node(collection, node_id, file_id)
                        except Exception:
                            logger.debug(
                                "detach_file_from_node skipped file=%s node=%s",
                                file_id[:12],
                                node_id[:12],
                                exc_info=True,
                            )
                    delete_meeting_anchor_if_empty(collection, meeting_id)
                except Exception:
                    logger.debug(
                        "Anchor detach skipped col=%s file=%s",
                        collection,
                        file_id[:12],
                        exc_info=True,
                    )

            if source:
                try:
                    if services.db:
                        services.db.delete_by_filter(
                            collection=collection, key="source", value=source
                        )
                except Exception as exc:
                    logger.warning("Qdrant delete by source failed: %s", exc)

                try:
                    from src.file_mgmt.service import unregister_files_for_source

                    unregister_files_for_source(
                        collection,
                        source,
                        remove_disk=True,
                        remove_index=True,
                    )
                except Exception as exc:
                    logger.warning("unregister_files_for_source failed: %s", exc)
            else:
                # No source on SQLite or leftover JSON — still drop this file_id.
                from src.collections.file_index import remove as remove_file_index
                from src.file_mgmt.service import _open_db, _purge_file_sqlite_rows

                file_dir = _files_dir(collection) / file_id
                if file_dir.exists():
                    shutil.rmtree(file_dir, ignore_errors=True)
                try:
                    remove_file_index(collection, file_id)
                except Exception:
                    logger.debug("legacy files.json remove skipped", exc_info=True)
                try:
                    conn = _open_db(collection)
                    try:
                        with conn:
                            if conn.execute(
                                "SELECT 1 FROM files WHERE file_id=?", (file_id,)
                            ).fetchone():
                                _purge_file_sqlite_rows(conn, file_id)
                    finally:
                        conn.close()
                except Exception:
                    logger.debug("sqlite purge fallback failed", exc_info=True)

            logger.info(
                "Deleted allocation file_id=%s source=%s from collection '%s'",
                file_id,
                source,
                collection,
            )
        except Exception as exc:
            logger.warning("Failed to delete allocation file_id=%s: %s", file_id, exc)

    def cleanup_meeting_allocations(self, meeting: Meeting) -> list[dict[str, str]]:
        """Purge ingested files for a meeting (SQLite + disk; leftover JSON)."""
        pairs: list[tuple[str, str]] = []
        for t in meeting.tabs or []:
            td = t if isinstance(t, dict) else t.model_dump()
            col = (td.get("associated_collection_id") or "").strip()
            fid = (td.get("allocated_file_id") or "").strip()
            if col and fid:
                pairs.append((col, fid))
        for col, fid in zip(
            meeting.allocated_collections or [],
            meeting.allocated_file_ids or [],
        ):
            col_s = (col or "").strip()
            fid_s = (fid or "").strip()
            if col_s and fid_s:
                pairs.append((col_s, fid_s))
        seen: set[tuple[str, str]] = set()
        cleaned: list[dict[str, str]] = []
        for col, fid in pairs:
            if (col, fid) in seen:
                continue
            seen.add((col, fid))
            try:
                self._delete_allocation(
                    col, fid, meeting_id=meeting.id, detach_anchor=True
                )
                cleaned.append({"collection": col, "file_id": fid})
            except Exception:
                logger.warning(
                    "Failed cleaning allocation col=%s file=%s meeting=%s",
                    col,
                    fid[:12],
                    meeting.id,
                    exc_info=True,
                )
        return cleaned

    @staticmethod
    def _managed_file_exists(collection_id: str, file_id: str) -> bool:
        if not file_id:
            return False
        try:
            from src.file_mgmt.service import _open_db

            conn = _open_db(collection_id)
            try:
                row = conn.execute(
                    "SELECT 1 FROM files WHERE file_id=?", (file_id,)
                ).fetchone()
                return bool(row)
            finally:
                conn.close()
        except Exception:
            return False

    async def allocate_section_to_collection(
        self,
        meeting_id: str,
        tab_id: str,
        collection_id: str,
        *,
        chain_id: str | None = None,
    ) -> tuple[Meeting, dict]:
        """Allocate a section into a collection (file-mgmt + optional timeline).

        Returns ``(meeting, bridge)`` where *bridge* has
        ``file_id``, ``task_id``, ``node_id``, ``source``, ``chain_id``.
        """
        import re as _re

        from src.collections.store import get_collection_meta
        from src.file_mgmt.service import (
            attach_file_to_node,
            detach_file_from_node,
            ensure_meeting_anchor_node,
            register_ingested_source_file,
            upload_file_version,
            _open_db,
            _main_chain_id,
        )
        from src.tasks.task_manager import task_manager

        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(f"Meeting {meeting_id} not found")

        tab_meta: dict | None = None
        for t in (meeting.tabs or []):
            tid = t["tab_id"] if isinstance(t, dict) else t.tab_id
            if tid == tab_id:
                tab_meta = t if isinstance(t, dict) else t.model_dump()
                break

        if tab_meta is None:
            raise ValueError(f"Tab '{tab_id}' not found")

        old_fid = (tab_meta.get("allocated_file_id") or "").strip()
        old_col = (tab_meta.get("associated_collection_id") or "").strip()

        # Read + process content (same snapshot as todo-candidate LLM extract)
        raw_md = store.get_section_md(meeting_id, tab_id)
        if not raw_md:
            raise ValueError(f"No content for tab '{tab_id}'")
        # Fingerprint raw editor content (not the processed upload blob)
        ingested_hash = store.section_content_hash(raw_md)

        speaker_names: dict[str, str] = getattr(meeting, "speaker_names", None) or {}
        from src.meeting.todo_candidates import prepare_section_todo_snapshot

        content = prepare_section_todo_snapshot(raw_md, speaker_names)

        section_label = tab_meta.get("name", tab_id)
        full_content = f"# {section_label}\n\n{content}"
        file_bytes = full_content.encode("utf-8")
        storage_name = f"{tab_id}.md"

        meeting_date = (
            meeting.created_at.strftime("%Y-%m-%d") if meeting.created_at else None
        )
        meeting_title = (meeting.title or "").strip() or "Untitled meeting"
        display_label = f"{meeting_title} / {section_label}"
        section_source = f"__meeting__:{meeting_id}:{tab_id}"

        task_id: str | None = None
        alloc_file_id: str
        node_id: str | None = None

        same_col_version = (
            bool(old_fid)
            and old_col == collection_id
            and self._managed_file_exists(collection_id, old_fid)
        )

        if same_col_version:
            # Stable file_id — new version (Notes-style reingest)
            alloc_file_id = old_fid
            result = upload_file_version(
                collection_id,
                alloc_file_id,
                file_bytes,
                storage_name,
                commit_message=f"Meeting re-ingest: {display_label}",
                document_source=section_source,
                source_label=display_label,
                file_type="meeting",
            )
            task_id = getattr(result, "task_id", None)
            register_ingested_source_file(
                collection_id,
                file_id=alloc_file_id,
                source=section_source,
                storage_name=storage_name,
                system_folder_name="Meeting",
                source_label=display_label,
            )
        else:
            # Different collection or first allocate — drop previous fully
            if old_col and old_fid:
                self._delete_allocation(
                    old_col, old_fid, meeting_id=meeting_id, detach_anchor=True
                )
                logger.info(
                    "Cleaned previous allocation %s/%s for tab %s",
                    old_col,
                    old_fid,
                    tab_id,
                )

            alloc_file_id = uuid.uuid4().hex
            file_dir = _files_dir(collection_id) / alloc_file_id
            file_dir.mkdir(parents=True, exist_ok=True)
            file_path = file_dir / storage_name
            file_path.write_text(full_content, encoding="utf-8")

            register_ingested_source_file(
                collection_id,
                file_id=alloc_file_id,
                source=section_source,
                storage_name=storage_name,
                system_folder_name="Meeting",
                source_label=display_label,
            )

            # Resolve version_id created by register
            version_id: str | None = None
            try:
                from src.file_mgmt.service import _open_db

                conn = _open_db(collection_id)
                try:
                    row = conn.execute(
                        "SELECT current_version_id FROM files WHERE file_id=?",
                        (alloc_file_id,),
                    ).fetchone()
                    version_id = row["current_version_id"] if row else None
                finally:
                    conn.close()
            except Exception:
                version_id = None

            try:
                # Await enqueue so upload can dequeue immediately (do not block
                # the event loop with sync LLM after create_task).
                task = await task_manager.create_task_async(
                    filename=storage_name,
                    task_type="upload",
                    file_path=str(file_path),
                    collection=collection_id,
                    filename_param=section_source,
                    source_label=display_label,
                    file_id=alloc_file_id,
                    version_id=version_id,
                    meeting_id=meeting_id,
                    meeting_date=meeting_date,
                )
                task_id = task.id
            except Exception as e:
                logger.warning(
                    "Failed to queue meeting allocate task %s: %s — falling back to sync",
                    section_source,
                    e,
                )
                # Fallback: synchronous ingest (legacy path)
                from src.tasks.handlers import upload_handler

                upload_task = Task(
                    id=str(uuid.uuid4()),
                    filename=f"meeting_{meeting_id}_{tab_id}",
                    collection=collection_id,
                    status=TaskStatus.PROCESSING,
                    created_at=datetime.now(timezone.utc),
                )
                await upload_handler(
                    upload_task,
                    str(file_path),
                    collection_id,
                    section_source,
                    source_label=display_label,
                    file_id=alloc_file_id,
                    meeting_id=meeting_id,
                    meeting_date=meeting_date,
                    version_id=version_id,
                )

        # Resolve target chain (default main)
        resolved_chain_id: str | None = (chain_id or "").strip() or None
        try:
            conn_ch = _open_db(collection_id)
            try:
                main_id = _main_chain_id(conn_ch)
                if not resolved_chain_id or resolved_chain_id == main_id:
                    resolved_chain_id = main_id
                else:
                    ch_ok = conn_ch.execute(
                        "SELECT 1 FROM chains WHERE chain_id=?",
                        (resolved_chain_id,),
                    ).fetchone()
                    if not ch_ok:
                        raise ValueError(f"Chain '{resolved_chain_id}' not found")
            finally:
                conn_ch.close()
        except ValueError:
            raise
        except Exception:
            logger.warning(
                "Chain resolve failed col=%s; using main", collection_id, exc_info=True
            )
            resolved_chain_id = None

        # Timeline anchor + attach (detach from prior nodes if chain/file moved)
        try:
            conn_det = _open_db(collection_id)
            try:
                prior = conn_det.execute(
                    "SELECT node_id FROM file_nodes WHERE file_id=?",
                    (alloc_file_id,),
                ).fetchall()
                prior_ids = [r["node_id"] for r in prior]
            finally:
                conn_det.close()
            for pid in prior_ids:
                try:
                    detach_file_from_node(collection_id, pid, alloc_file_id)
                except Exception:
                    logger.debug(
                        "pre-attach detach skipped file=%s node=%s",
                        alloc_file_id[:12],
                        pid[:12],
                        exc_info=True,
                    )

            node_id = ensure_meeting_anchor_node(
                collection_id,
                meeting_id,
                title=meeting_title,
                event_time=meeting_date,
                chain_id=resolved_chain_id,
            )
            attach_file_to_node(
                collection_id, node_id, file_id=alloc_file_id
            )
            # Drop empty anchors left on other chains after move
            from src.file_mgmt.service import delete_meeting_anchor_if_empty

            delete_meeting_anchor_if_empty(collection_id, meeting_id)
        except Exception:
            logger.warning(
                "Meeting anchor attach failed meeting=%s col=%s file=%s",
                meeting_id,
                collection_id,
                alloc_file_id[:12],
                exc_info=True,
            )
            node_id = None

        # ── Update tab metadata (do NOT wait on todo LLM) ───────
        # Keep prior candidates until background extract finishes so
        # Create todos still has something if user opens mid-refresh.
        prior_candidates = list(tab_meta.get("todo_candidates") or [])
        col_meta = get_collection_meta(collection_id)
        col_name = col_meta.get("name", collection_id) if col_meta else collection_id

        updated_tabs: list[dict] = []
        for t in (meeting.tabs or []):
            td = t if isinstance(t, dict) else t.model_dump()
            if td.get("tab_id") == tab_id:
                td["associated_collection_id"] = collection_id
                td["associated_collection_name"] = col_name
                td["allocated_file_id"] = alloc_file_id
                td["needs_reingest"] = False
                td["ingested_content_hash"] = ingested_hash
                td["allocated_chain_id"] = resolved_chain_id or ""
                td["allocated_node_id"] = node_id or ""
                td["todo_candidates"] = prior_candidates
            updated_tabs.append(td)

        store.update_meeting(meeting_id, tabs=updated_tabs)

        alloc_cols, alloc_fids = _rebuild_allocation_arrays(updated_tabs)
        store.update_meeting(
            meeting_id,
            allocated_collections=alloc_cols,
            allocated_file_ids=alloc_fids,
        )

        # Todo candidates: fire-and-forget (daemon thread) so allocate +
        # upload queue are never blocked by Meeting-model LLM latency.
        self.schedule_section_todo_extract(meeting_id, tab_id)

        updated = store.get_meeting(meeting_id)
        assert updated is not None

        bridge = {
            "file_id": alloc_file_id,
            "task_id": task_id,
            "node_id": node_id,
            "source": section_source,
            "collection_id": collection_id,
            "chain_id": resolved_chain_id,
            "todo_candidate_count": len(prior_candidates),
            "todo_candidates_pending": True,
        }
        logger.info(
            "Allocated section %s/%s → col=%s file=%s task=%s node=%s chain=%s "
            "todos_prior=%d (extract scheduled bg)",
            meeting_id,
            tab_id,
            collection_id,
            alloc_file_id[:12],
            task_id,
            (node_id or "")[:12],
            (resolved_chain_id or "")[:12],
            len(prior_candidates),
        )
        return updated, bridge

    async def delete_section_allocation(
        self, meeting_id: str, tab_id: str,
    ) -> Meeting:
        """Remove a section's allocation: delete file snapshot and clear tab metadata."""
        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(f"Meeting {meeting_id} not found")

        tab_meta: dict | None = None
        for t in (meeting.tabs or []):
            td = t if isinstance(t, dict) else t.model_dump()
            if td.get("tab_id") == tab_id:
                tab_meta = td
                break

        if tab_meta is None:
            raise ValueError(f"Tab '{tab_id}' not found")

        col_id = tab_meta.get("associated_collection_id", "")
        file_id = tab_meta.get("allocated_file_id", "")
        if col_id and file_id:
            self._delete_allocation(
                col_id, file_id, meeting_id=meeting_id, detach_anchor=True
            )

        updated_tabs: list[dict] = []
        for t in (meeting.tabs or []):
            td = t if isinstance(t, dict) else t.model_dump()
            if td.get("tab_id") == tab_id:
                td["associated_collection_id"] = ""
                td["associated_collection_name"] = ""
                td["allocated_file_id"] = ""
                td["needs_reingest"] = False
                td["ingested_content_hash"] = ""
                td["allocated_chain_id"] = ""
                td["allocated_node_id"] = ""
                td["todo_candidates"] = []
            updated_tabs.append(td)

        store.update_meeting(meeting_id, tabs=updated_tabs)

        alloc_cols, alloc_fids = _rebuild_allocation_arrays(updated_tabs)
        store.update_meeting(
            meeting_id,
            allocated_collections=alloc_cols,
            allocated_file_ids=alloc_fids,
        )

        updated = store.get_meeting(meeting_id)
        assert updated is not None
        return updated
