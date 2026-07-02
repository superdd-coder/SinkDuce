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

    def _doc_summary_id(self, collection_id: str, source: str) -> str:
        return str(uuid.uuid5(_NS, f"doc:{collection_id}:{source}"))

    def store_doc_summary(
        self,
        collection_id: str,
        source: str,
        data: list[str],
        facts: list[str],
        insights: list[str],
        include_in_summary: bool = True,
    ) -> None:
        point_id = self._doc_summary_id(collection_id, source)
        self.db.upsert_points(
            collection=self.COLLECTION_NAME,
            ids=[point_id],
            vectors=[[0.0] * self.vector_size],
            payloads=[
                {
                    "type": "doc_summary",
                    "collection_id": collection_id,
                    "source": source,
                    "data": data,
                    "facts": facts,
                    "insights": insights,
                    "include_in_summary": include_in_summary,
                }
            ],
        )

    def set_doc_summary_include(self, collection_id: str, source: str, include: bool) -> bool:
        """Update the include_in_summary flag for a doc summary. Returns True if found."""
        existing = self.get_doc_summary(collection_id, source)
        if existing is None:
            return False
        existing["include_in_summary"] = include
        point_id = self._doc_summary_id(collection_id, source)
        self.db.upsert_points(
            collection=self.COLLECTION_NAME,
            ids=[point_id],
            vectors=[[0.0] * self.vector_size],
            payloads=[existing],
        )
        return True

    def get_doc_summary(self, collection_id: str, source: str) -> dict | None:
        """Return a single doc summary dict, or None if not found."""
        scroll_filter = Filter(
            must=[
                FieldCondition(key="type", match=MatchValue(value="doc_summary")),
                FieldCondition(key="collection_id", match=MatchValue(value=collection_id)),
                FieldCondition(key="source", match=MatchValue(value=source)),
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
        point_id = self._doc_summary_id(collection_id, source)
        self.db.delete_points(self.COLLECTION_NAME, ids=[point_id])

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
