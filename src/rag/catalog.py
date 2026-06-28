"""CollectionCatalog — 5-element metadata catalog for intelligent collection routing.

Each collection gets a CatalogEntry with name, id, definition, coverage, tags.
Coverage updates use a cooling/dirty state machine:
  - After generation → enter 180s cooling period w/ background timer.
  - File changes during cooling → mark dirty (deferred to timer expiry).
  - File changes outside cooling, with zero active upload tasks → generate immediately.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_CHUNK0_CHARS = 150  # max chars of chunk 0 / enrichment summary per file


@dataclass
class CatalogEntry:
    name: str
    id: str
    definition: str          # from collection project_description
    coverage: str            # ~300 chars, compact aspect inventory for Decomposer routing
    tags: list[str] = field(default_factory=list)


class CollectionCatalog:
    """Per-collection 5-element metadata for Decomposer routing."""

    def __init__(self, db, llm):
        self.db = db
        self.llm = llm

        self._lock = threading.Lock()
        self._debounce_seconds: float = 60.0

        # Per-collection state (protected by _lock)
        self._cooling: dict[str, bool] = {}          # in 60s cooldown after generation
        self._dirty: dict[str, bool] = {}            # file changed during cooling
        self._timers: dict[str, threading.Timer] = {}  # one timer per cooling collection

    # ══════════════════════════════════════════════════════════════════
    # Public API
    # ══════════════════════════════════════════════════════════════════

    def get_catalog(self, collections: list[str] | None = None) -> list[CatalogEntry]:
        """Return CatalogEntry for all or specified *collections*."""
        logger.info("[Catalog] get_catalog collections=%s", collections or "ALL")
        if collections is None:
            try:
                collections = [c for c in self.db.list_collections()
                               if not c.startswith("__")]
            except Exception:
                logger.exception("[Catalog] cannot list collections")
                return []

        entries = []
        for col_name in collections:
            try:
                col_config = self.db.get_collection_config(col_name)
            except Exception:
                logger.warning("[Catalog] cannot read config for collection=%r, skipping", col_name)
                continue

            # Priority: consolidation result → collection config → collection name
            definition = ""
            try:
                from src.rag.summary_manager import SummaryManager
                desc = SummaryManager(self.db).get_project_description(col_name)
                if desc and desc.get("content"):
                    import re
                    definition = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', desc["content"])
            except Exception:
                pass
            if not definition:
                definition = col_config.get("project_description", "")
            if not definition:
                try:
                    from src.collections import store as cs
                    meta = cs.get_collection_meta(col_name)
                    definition = meta.get("name", col_name) if meta else col_name
                except Exception:
                    definition = col_name

            coverage = col_config.get("coverage", "")
            tags = col_config.get("tags", [])
            if not isinstance(tags, list):
                tags = []

            # Resolve display name
            display_name = col_name
            try:
                from src.collections import store as cs
                meta = cs.get_collection_meta(col_name)
                if meta and meta.get("name"):
                    display_name = meta["name"]
            except Exception:
                pass

            entries.append(CatalogEntry(
                name=display_name,
                id=col_name,
                definition=str(definition),
                coverage=str(coverage) if coverage else "",
                tags=list(tags),
            ))

        return entries

    def update_coverage(self, collection_id: str) -> None:
        """Called after a file is uploaded or deleted."""
        with self._lock:
            cooling = self._cooling.get(collection_id, False)
            dirty = self._dirty.get(collection_id, False)

            logger.info("[Coverage] trigger col=%r cooling=%s dirty=%s",
                        collection_id, cooling, dirty)

            if cooling:
                self._dirty[collection_id] = True
                logger.info("[Coverage] → DEFER col=%r (cooling, will flush on timer)", collection_id)
                return

            self._dirty[collection_id] = False
            logger.info("[Coverage] → GENERATE col=%r (not cooling)", collection_id)
            self._do_generate(collection_id)

    def mark_dirty(self, collection_id: str) -> None:
        """Set dirty flag without triggering generation.

        Used when the caller knows a trigger is coming later (e.g. delete
        during active uploads — the last upload task will call update_coverage).
        """
        with self._lock:
            self._dirty[collection_id] = True
            logger.info("[Coverage] MARK DIRTY col=%r", collection_id)

    def update_tags(self, collection_id: str, tags: list[str]) -> None:
        try:
            self.db.update_collection_config(collection_id, {"tags": list(tags)})
            logger.info("[Catalog] tags updated for %r: %s", collection_id, tags)
        except Exception:
            logger.exception("[Catalog] failed to update tags for %r", collection_id)

    # ══════════════════════════════════════════════════════════════════
    # Internal — state machine
    # ══════════════════════════════════════════════════════════════════

    def _do_generate(self, collection_id: str) -> None:
        """Generate coverage, then enter cooling + start timer."""
        self._generate_coverage(collection_id)
        self._enter_cooling(collection_id)

    def _enter_cooling(self, collection_id: str) -> None:
        """Mark cooling and start the 180s timer.  Must hold _lock."""
        self._cooling[collection_id] = True
        self._dirty[collection_id] = False

        old = self._timers.pop(collection_id, None)
        if old is not None:
            old.cancel()

        timer = threading.Timer(self._debounce_seconds, self._on_timer, args=[collection_id])
        timer.daemon = True
        self._timers[collection_id] = timer
        timer.start()
        logger.info("[Coverage] COOLING START col=%r timer=%ds", collection_id, self._debounce_seconds)

    def _on_timer(self, collection_id: str) -> None:
        """Timer fired — exit cooling, decide whether to regenerate."""
        self._timers.pop(collection_id, None)
        with self._lock:
            self._cooling[collection_id] = False
            dirty = self._dirty.get(collection_id, False)
            logger.info("[Coverage] TIMER FIRED col=%r dirty=%s", collection_id, dirty)

            if not dirty:
                logger.info("[Coverage] → IDLE col=%r (clean, nothing to do)", collection_id)
                return

            active = self._count_active_upload_tasks(collection_id)
            if active > 0:
                logger.info("[Coverage] → WAIT col=%r (%d active upload tasks, keep dirty)", collection_id, active)
                return

            self._dirty[collection_id] = False
            logger.info("[Coverage] → REGENERATE col=%r (dirty, no active tasks)", collection_id)
            self._do_generate(collection_id)

    # ══════════════════════════════════════════════════════════════════
    # Internal — coverage generation
    # ══════════════════════════════════════════════════════════════════

    def _generate_coverage(self, collection_id: str) -> None:
        """Query Qdrant → build prompt → LLM → persist."""
        file_infos = self._collect_file_infos(collection_id)

        if not file_infos:
            logger.info("[Coverage] CLEAR col=%r (0 files → coverage='')", collection_id)
            try:
                self.db.update_collection_config(collection_id, {"coverage": ""})
            except Exception:
                logger.exception("[Coverage] failed to clear coverage for %r", collection_id)
            return

        n_summary = sum(1 for fi in file_infos if fi.get("chunk0_text"))
        logger.info("[Coverage] GENERATING col=%r files=%d (with_text=%d)",
                    collection_id, len(file_infos), n_summary)

        # Build prompt — compact aspect inventory for Decomposer routing
        lines = []
        for fi in file_infos:
            line = f"- {fi['filename']}"
            if fi.get("chunk0_text"):
                line += f"\n    {fi['chunk0_text']}"
            lines.append(line)

        file_block = "\n".join(lines)

        prompt = (
            f"The collection '{collection_id}' currently contains these files:\n\n"
            f"{file_block}\n\n"
            f"Produce a compact ASPECT INVENTORY — list the concrete topics and "
            f"information aspects this collection's documents cover, so a search "
            f"router can match queries against them.\n\n"
            f"Guidelines:\n"
            f"- Use '|' to separate distinct aspects. Within each aspect, use '()' "
            f"to pack related keywords and specifics.\n"
            f"- Merge similar files into one aspect. A good aspect is something a "
            f"user might search for as a single atomic query.\n"
            f"- Be specific and dense. Prefer concrete terms over generic labels.\n"
            f"- Write in English. No markdown, no JSON, no quotes, no explanations.\n"
            f"- Target ~250 characters, maximum 400."
        )

        try:
            new_coverage = self.llm.generate(
                prompt,
                system=(
                    "You produce compact aspect inventories of document collections. "
                    "Given a file list with content summaries, output a single line "
                    "listing the concrete information aspects covered, separated by '|'. "
                    "Within each aspect, pack relevant keywords in '()'. "
                    "Merge similar files; keep distinct topics separate. "
                    "Be specific — prefer technical terms over generic labels. "
                    "Output ONLY the aspect inventory line, nothing else."
                ),
                max_tokens=512, thinking=False,
            ).strip()
            new_coverage = new_coverage.strip('"').strip("'").strip()
            if len(new_coverage) > 400:
                new_coverage = new_coverage[:400]
            logger.info("[Coverage] RESULT col=%r → %r", collection_id, new_coverage)
        except Exception:
            logger.exception("[Coverage] LLM failed for %r", collection_id)
            return

        try:
            self.db.update_collection_config(collection_id, {"coverage": new_coverage})
            logger.info("[Coverage] SAVED col=%r", collection_id)
        except Exception:
            logger.exception("[Coverage] failed to persist for %r", collection_id)

    def _collect_file_infos(self, collection_id: str) -> list[dict]:
        """Return [{"filename": ..., "chunk0_text": ...}, ...].

        Uses _get_chunk0 which prefers chunk.metadata["summary"] over raw text,
        so enrichment-enabled collections naturally get better signal.
        """
        sources = self._list_sources(collection_id)
        if not sources:
            return []

        config = self._read_config(collection_id)
        chunk_mode = config.get("chunk_mode", "normal")

        infos = []
        for src in sources:
            # _get_chunk0 already prefers chunk.metadata["summary"] over
            # raw text, so enrichment-enabled collections get better signal
            # without any extra queries.
            chunk0 = self._get_chunk0(collection_id, src, chunk_mode, config)
            infos.append({"filename": src, "chunk0_text": chunk0})

        return infos

    def _list_sources(self, collection_id: str) -> list[str]:
        """Return all unique source filenames via scroll."""
        sources: set[str] = set()
        offset = None
        while True:
            results, offset = self.db.scroll_points(
                collection_id, limit=200, offset=offset,
                with_payload=["source"],
            )
            for r in results:
                src = (r.get("payload") or {}).get("source", "")
                if src:
                    sources.add(src)
            if offset is None:
                break
        return sorted(sources)

    def _get_chunk0(self, collection_id: str, source: str, chunk_mode: str, config: dict) -> str:
        """Return representative text for *source* used in the coverage prompt.

        Priority:
        1. `summary` field from chunk metadata — enrichment-generated one-liner
           describing the document.  Best signal for document type classification.
        2. First chunk's raw text (child chunk for pc mode, chunk_index=0 otherwise).
        """
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        must = [FieldCondition(key="source", match=MatchValue(value=source))]
        if chunk_mode == "parent_child":
            must.append(FieldCondition(key="chunk_type", match=MatchValue(value="child")))

        scroll_filter = Filter(must=must)
        results, _ = self.db.scroll_points(
            collection_id, limit=100, scroll_filter=scroll_filter,
            with_payload=["text", "chunk_index", "chunk_type", "summary"],
        )

        best = None
        best_idx = 999999
        for r in results:
            payload = r.get("payload") or {}
            ci = payload.get("chunk_index", 0)
            if ci < best_idx:
                best_idx = ci
                best = payload

        if best:
            # Prefer enrichment summary — it's a curated one-liner about the doc
            summary = (best.get("summary") or "").strip()
            if summary:
                return summary[:_CHUNK0_CHARS]
            return (best.get("text") or "")[:_CHUNK0_CHARS]
        return ""

    # ══════════════════════════════════════════════════════════════════
    # Internal — helpers
    # ══════════════════════════════════════════════════════════════════

    def _count_active_upload_tasks(self, collection_id: str) -> int:
        """Return number of active tasks that may change the file list."""
        try:
            from src.tasks.task_manager import task_manager
            return len(task_manager.get_active_tasks(
                collection=collection_id,
                task_types=["upload", "doc_summary"],
            ))
        except Exception:
            logger.warning("[Catalog] cannot query active tasks for %r", collection_id, exc_info=True)
            return 0

    @staticmethod
    def _read_config(collection_id: str) -> dict:
        try:
            from src.services import services
            return services.db.get_collection_config(collection_id)
        except Exception:
            return {}
