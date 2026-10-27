"""SummaryManager — manages document summaries, collection summaries, and conflicts
in a dedicated Qdrant collection called __summaries__.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import uuid

from qdrant_client.models import FieldCondition, Filter, MatchValue

from src.db.qdrant import QdrantManager

# Fixed namespace for generating deterministic point IDs
_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "sinkduce-summaries")


class SummaryManager:
    COLLECTION_NAME = "__summaries__"

    def __init__(self, db: QdrantManager, vector_size: int = 1024):
        self.db = db
        self._requested_vector_size = vector_size
        self.vector_size = vector_size  # may be updated by ensure_collection
        self.ensure_collection()

    # ── Collection management ────────────────────────────────

    def ensure_collection(self) -> None:
        """Create the __summaries__ collection if it does not exist.
        If it already exists, read the actual vector_size from it.
        """
        if self.db.collection_exists(self.COLLECTION_NAME):
            # Read actual vector_size from existing collection
            try:
                info = self.db.client.get_collection(self.COLLECTION_NAME)
                actual_size = info.config.params.vectors.size
                if actual_size and actual_size > 0:
                    self.vector_size = actual_size
            except Exception:
                pass
        else:
            self.db.create_collection(self.COLLECTION_NAME, vector_size=self._requested_vector_size)
            logger.info("Created __summaries__ collection (vector_size=%d)", self._requested_vector_size)

    # ── Doc summaries ───────────────────────────────────────
    #
    # Payload (additive): type, collection_id, source, data, facts, insights,
    # include_in_summary, and optional version_id / file_id.
    #
    # Point IDs:
    #   legacy:  doc:{col}:{source}              — one per source (compat)
    #   version: doc:{col}:{source}:{version_id} — one per version when set
    #
    # store with version_id dual-writes legacy point so old readers still work.

    def _doc_summary_id(
        self,
        collection_id: str,
        source: str,
        version_id: str | None = None,
    ) -> str:
        if version_id:
            return str(
                uuid.uuid5(_NS, f"doc:{collection_id}:{source}:{version_id}")
            )
        return str(uuid.uuid5(_NS, f"doc:{collection_id}:{source}"))

    def store_doc_summary(
        self,
        collection_id: str,
        source: str,
        data: list[str],
        facts: list[str],
        insights: list[str],
        include_in_summary: bool = True,
        *,
        version_id: str | None = None,
        file_id: str | None = None,
        mirror_legacy: bool = True,
    ) -> None:
        """Upsert a doc summary. With *version_id*, writes a versioned point
        and (by default) also mirrors to the legacy source-only point so
        callers that omit version_id still see the latest write.
        """
        vid = (version_id or "").strip() or None
        fid = (file_id or "").strip() or None
        if not fid and (source or "").startswith("__file__:"):
            fid = source[len("__file__:") :]

        payload: dict = {
            "type": "doc_summary",
            "collection_id": collection_id,
            "source": source,
            "data": data,
            "facts": facts,
            "insights": insights,
            "include_in_summary": include_in_summary,
        }
        if vid:
            payload["version_id"] = vid
        if fid:
            payload["file_id"] = fid

        ids: list[str] = []
        payloads: list[dict] = []
        if vid:
            ids.append(self._doc_summary_id(collection_id, source, vid))
            payloads.append(dict(payload))
            if mirror_legacy:
                # Legacy point tracks the write that just happened (current path).
                ids.append(self._doc_summary_id(collection_id, source, None))
                payloads.append(dict(payload))
        else:
            ids.append(self._doc_summary_id(collection_id, source, None))
            payloads.append(payload)

        self.db.upsert_points(
            collection=self.COLLECTION_NAME,
            ids=ids,
            vectors=[[0.0] * self.vector_size] * len(ids),
            payloads=payloads,
        )

    def set_doc_summary_include(self, collection_id: str, source: str, include: bool) -> bool:
        """Update include_in_summary on all summary points for *source*."""
        points = self._scroll_doc_summaries_for_source(collection_id, source)
        if not points:
            return False
        ids = []
        payloads = []
        for p in points:
            pl = dict(p.get("payload") or {})
            pl["include_in_summary"] = include
            pid = p.get("id")
            if pid is None:
                vid = pl.get("version_id")
                pid = self._doc_summary_id(
                    collection_id, source, vid if vid else None
                )
            ids.append(str(pid))
            payloads.append(pl)
        # Also ensure legacy id is covered
        legacy_id = self._doc_summary_id(collection_id, source, None)
        if legacy_id not in ids and points:
            pl0 = dict(points[0].get("payload") or {})
            pl0["include_in_summary"] = include
            ids.append(legacy_id)
            payloads.append(pl0)
        self.db.upsert_points(
            collection=self.COLLECTION_NAME,
            ids=ids,
            vectors=[[0.0] * self.vector_size] * len(ids),
            payloads=payloads,
        )
        return True

    def _scroll_doc_summaries_for_source(
        self, collection_id: str, source: str, *, limit: int = 100
    ) -> list[dict]:
        scroll_filter = Filter(
            must=[
                FieldCondition(key="type", match=MatchValue(value="doc_summary")),
                FieldCondition(
                    key="collection_id", match=MatchValue(value=collection_id)
                ),
                FieldCondition(key="source", match=MatchValue(value=source)),
            ]
        )
        points, _ = self.db.scroll_points(
            collection=self.COLLECTION_NAME,
            scroll_filter=scroll_filter,
            limit=limit,
        )
        return points or []

    def get_doc_summary(
        self,
        collection_id: str,
        source: str,
        version_id: str | None = None,
    ) -> dict | None:
        """Return a doc summary, or None if not found.

        *version_id* set: match payload.version_id; if none match, fall back to
        a point with no version_id (pre-B legacy).
        *version_id* omitted: return any point for source (legacy dual-write
        keeps the latest write on the source-only id, so scroll finds it).
        """
        vid = (version_id or "").strip() or None
        points = self._scroll_doc_summaries_for_source(collection_id, source)
        if not points:
            return None

        payloads = [p.get("payload") or {} for p in points]

        if vid:
            for pl in payloads:
                if pl.get("version_id") == vid:
                    return pl
            # Pre-B data: single point without version_id
            for pl in payloads:
                if not (pl.get("version_id") or "").strip():
                    return pl
            return None

        # No pin: prefer a point that has version_id (newer dual-write) but any is OK
        for pl in payloads:
            if pl.get("version_id"):
                return pl
        return payloads[0]

    def get_doc_summaries(self, collection_id: str, included_only: bool = False) -> list[dict]:
        must = [
            FieldCondition(key="type", match=MatchValue(value="doc_summary")),
            FieldCondition(key="collection_id", match=MatchValue(value=collection_id)),
        ]
        must_not = None
        if included_only:
            # Use must_not=False to include docs without the field AND docs with True
            must_not = [FieldCondition(key="include_in_summary", match=MatchValue(value=False))]
        scroll_filter = Filter(must=must, must_not=must_not)
        points, _ = self.db.scroll_points(
            collection=self.COLLECTION_NAME,
            scroll_filter=scroll_filter,
            limit=1000,
        )
        return [p["payload"] for p in points]

    def delete_doc_summary(self, collection_id: str, source: str) -> None:
        """Delete legacy + all versioned summary points for *source*."""
        ids = [self._doc_summary_id(collection_id, source, None)]
        for p in self._scroll_doc_summaries_for_source(collection_id, source):
            pid = p.get("id")
            if pid is not None:
                ids.append(str(pid))
            pl = p.get("payload") or {}
            vid = pl.get("version_id")
            if vid:
                ids.append(self._doc_summary_id(collection_id, source, str(vid)))
        # Dedupe
        uniq = list(dict.fromkeys(ids))
        if uniq:
            try:
                self.db.delete_points(self.COLLECTION_NAME, ids=uniq)
            except Exception:
                logger.warning(
                    "delete_doc_summary partial failure col=%s src=%s",
                    collection_id,
                    source,
                    exc_info=True,
                )

    # ── Collection summary ──────────────────────────────────

    def _collection_summary_id(self, collection_id: str) -> str:
        return str(uuid.uuid5(_NS, f"collection:{collection_id}"))

    def store_collection_summary(
        self, collection_id: str, content: str, embedding: list[float] | None = None
    ) -> None:
        point_id = self._collection_summary_id(collection_id)
        if embedding is None:
            embedding = [0.0] * self.vector_size
        self.db.upsert_points(
            collection=self.COLLECTION_NAME,
            ids=[point_id],
            vectors=[embedding],
            payloads=[
                {
                    "type": "collection_summary",
                    "collection_id": collection_id,
                    "content": content,
                }
            ],
        )

    def get_collection_summary(self, collection_id: str) -> dict | None:
        scroll_filter = Filter(
            must=[
                FieldCondition(key="type", match=MatchValue(value="collection_summary")),
                FieldCondition(key="collection_id", match=MatchValue(value=collection_id)),
            ]
        )
        points, _ = self.db.scroll_points(
            collection=self.COLLECTION_NAME,
            scroll_filter=scroll_filter,
            limit=1,
        )
        if not points:
            return None
        return points[0]["payload"]

    def delete_collection_summary(self, collection_id: str) -> None:
        point_id = self._collection_summary_id(collection_id)
        self.db.delete_points(self.COLLECTION_NAME, ids=[point_id])

    # ── Project description ──────────────────────────────────

    def _project_description_id(self, collection_id: str) -> str:
        return str(uuid.uuid5(_NS, f"project_desc:{collection_id}"))

    def store_project_description(self, collection_id: str, content: str) -> None:
        point_id = self._project_description_id(collection_id)
        self.db.upsert_points(
            collection=self.COLLECTION_NAME,
            ids=[point_id],
            vectors=[[0.0] * self.vector_size],
            payloads=[
                {
                    "type": "project_description",
                    "collection_id": collection_id,
                    "content": content,
                }
            ],
        )

    def get_project_description(self, collection_id: str) -> dict | None:
        scroll_filter = Filter(
            must=[
                FieldCondition(key="type", match=MatchValue(value="project_description")),
                FieldCondition(key="collection_id", match=MatchValue(value=collection_id)),
            ]
        )
        points, _ = self.db.scroll_points(
            collection=self.COLLECTION_NAME,
            scroll_filter=scroll_filter,
            limit=1,
        )
        if not points:
            return None
        return points[0]["payload"]

    def delete_project_description(self, collection_id: str) -> None:
        point_id = self._project_description_id(collection_id)
        self.db.delete_points(self.COLLECTION_NAME, ids=[point_id])

    def get_all_project_descriptions(self) -> list[dict]:
        scroll_filter = Filter(
            must=[
                FieldCondition(key="type", match=MatchValue(value="project_description")),
            ]
        )
        points, _ = self.db.scroll_points(
            collection=self.COLLECTION_NAME,
            scroll_filter=scroll_filter,
            limit=1000,
        )
        # Sort by collection_id for deterministic ordering.
        # Qdrant scroll does NOT guarantee order; without sorting, the
        # collection catalog fed to the Blueprint LLM changes every run,
        # causing unstable section decomposition.
        result = [p["payload"] for p in points]
        result.sort(key=lambda p: p.get("collection_id", ""))
        return result

    # ── Conflicts ───────────────────────────────────────────

    def store_conflicts(self, collection_id: str, conflicts: list[dict]) -> None:
        if not conflicts:
            return
        ids = [
            str(uuid.uuid5(_NS, f"conflict:{collection_id}:{i}"))
            for i in range(len(conflicts))
        ]
        vectors = [[0.0] * self.vector_size] * len(conflicts)
        payloads = [
            {
                "type": "conflict",
                "collection_id": collection_id,
                **c,
            }
            for c in conflicts
        ]
        self.db.upsert_points(
            collection=self.COLLECTION_NAME,
            ids=ids,
            vectors=vectors,
            payloads=payloads,
        )

    def get_conflicts(self, collection_id: str) -> list[dict]:
        scroll_filter = Filter(
            must=[
                FieldCondition(key="type", match=MatchValue(value="conflict")),
                FieldCondition(key="collection_id", match=MatchValue(value=collection_id)),
            ]
        )
        points, _ = self.db.scroll_points(
            collection=self.COLLECTION_NAME,
            scroll_filter=scroll_filter,
            limit=1000,
        )
        return [p["payload"] for p in points]

    def delete_conflicts(self, collection_id: str) -> None:
        scroll_filter = Filter(
            must=[
                FieldCondition(key="type", match=MatchValue(value="conflict")),
                FieldCondition(key="collection_id", match=MatchValue(value=collection_id)),
            ]
        )
        self.db.client.delete(
            collection_name=self.COLLECTION_NAME,
            points_selector=scroll_filter,
        )

    # ── All collection summaries ────────────────────────────

    def get_all_collection_summaries(self) -> list[dict]:
        scroll_filter = Filter(
            must=[
                FieldCondition(key="type", match=MatchValue(value="collection_summary")),
            ]
        )
        points, _ = self.db.scroll_points(
            collection=self.COLLECTION_NAME,
            scroll_filter=scroll_filter,
            limit=1000,
        )
        return [p["payload"] for p in points]
