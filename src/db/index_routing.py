"""Keep public collection IDs stable across atomic Qdrant index replacements."""
from __future__ import annotations

import asyncio
import logging
from collections import Counter, defaultdict
from contextlib import contextmanager
from threading import Condition, RLock
from contextvars import ContextVar

from qdrant_client import models

LIVE_PREFIX = "__sinkduce_embedding_live_"
STAGE_PREFIX = "__sinkduce_embedding_stage_"
logger = logging.getLogger(__name__)


class CollectionWork:
    def __init__(self):
        self.condition = Condition()
        self.writers = 0
        self.rebuilding = False
        self.commit = RLock()
        self.revision = 0

    @contextmanager
    def writer(self):
        with self.condition:
            self.condition.wait_for(lambda: not self.rebuilding)
            self.writers += 1
        try:
            yield
        finally:
            self.release_writer()

    async def acquire_upload(self):
        while True:
            with self.condition:
                if not self.rebuilding:
                    self.writers += 1
                    return
            await asyncio.sleep(0.2)

    def release_writer(self):
        with self.condition:
            self.writers -= 1
            self.condition.notify_all()

    @contextmanager
    def rebuild(self):
        with self.condition:
            self.condition.wait_for(lambda: not self.rebuilding)
            self.rebuilding = True
        try:
            with self.condition:
                self.condition.wait_for(lambda: not self.writers)
            yield
        finally:
            with self.condition:
                self.rebuilding = False
                self.condition.notify_all()


_pinned = ContextVar("qdrant_index_snapshot", default=None)
_work = defaultdict(CollectionWork)


def collection_work(collection):
    return _work[collection]


class IndexRoutingClient:
    """Route even low-level file metadata edits through the live Qdrant alias."""
    _reads = {"get_collection", "query_points", "count", "retrieve", "scroll", "collection_exists"}
    _writes = {"upsert", "update_vectors", "delete_vectors", "delete", "set_payload", "overwrite_payload",
               "delete_payload", "clear_payload", "create_payload_index", "batch_update_points"}

    def __init__(self, raw):
        self.raw = raw
        self._aliases = set()
        self._readers = Counter()
        self._retired = set()

    def aliases(self):
        return {a.alias_name: a.collection_name for a in self.raw.get_aliases().aliases
                if a.alias_name.startswith(LIVE_PREFIX)}

    @contextmanager
    def snapshot(self, collection):
        current = _pinned.get()
        if collection.startswith("__") or (current and current[:2] == (self, collection)):
            yield
            return
        lock = collection_work(collection).commit
        with lock:
            alias = self._route(collection)
            physical = self.aliases().get(alias, alias)
            self._readers[physical] += 1
        token = _pinned.set((self, collection, physical))
        try:
            yield
        finally:
            _pinned.reset(token)
            with lock:
                self._readers[physical] -= 1
                if not self._readers[physical]:
                    del self._readers[physical]
                    if physical in self._retired:
                        self.retire(collection, physical)

    def retire(self, collection, physical):
        """Let searches that already embedded their query finish in their original index."""
        with collection_work(collection).commit:
            if self._readers[physical]:
                self._retired.add(physical)
                return
            self._retired.discard(physical)
            try:
                self.raw.delete_collection(physical)
            except Exception:
                logger.warning("Could not remove retired index %s for %s", physical, collection, exc_info=True)

    def _route(self, collection):
        if collection.startswith("__"):
            return collection
        alias = LIVE_PREFIX + collection
        if alias not in self._aliases:
            if alias not in self.aliases():
                if not self.raw.collection_exists(collection):
                    return collection
                try:
                    self.raw.update_collection_aliases([models.CreateAliasOperation(
                        create_alias=models.CreateAlias(collection_name=collection, alias_name=alias))])
                except Exception:
                    if alias not in self.aliases():
                        raise
            self._aliases.add(alias)
        return alias

    def publish(self, collection, stage):
        alias = self._route(collection)
        if alias == collection:
            raise ValueError("The collection was deleted during the rebuild.")
        previous = self.aliases()[alias]
        try:
            self.raw.update_collection_aliases([
                models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=alias)),
                models.CreateAliasOperation(create_alias=models.CreateAlias(collection_name=stage, alias_name=alias)),
            ])
        except Exception:
            if self.aliases().get(alias) != stage:
                raise
        return previous

    def list_names(self):
        physical = [c.name for c in self.raw.get_collections().collections if not c.name.startswith(STAGE_PREFIX)]
        return list(dict.fromkeys([*physical, *(a.removeprefix(LIVE_PREFIX) for a in self.aliases())]))

    def delete_logical(self, collection):
        with collection_work(collection).commit:
            alias = LIVE_PREFIX + collection
            target = self.aliases().get(alias, collection)
            self.raw.delete_collection(target)
            self._aliases.discard(alias)
            if target != collection and self.raw.collection_exists(collection):
                self.raw.delete_collection(collection)
            collection_work(collection).revision += 1

    def __getattr__(self, name):
        method = getattr(self.raw, name)
        if name not in self._reads and name not in self._writes:
            return method

        def routed(collection_name, *args, **kwargs):
            if collection_name.startswith("__"):
                return method(collection_name, *args, **kwargs)
            state = collection_work(collection_name)
            if name in self._writes:
                with state.commit:
                    state.revision += 1
                    return method(self._route(collection_name), *args, **kwargs)
            snapshot = _pinned.get()
            if snapshot and snapshot[:2] == (self, collection_name):
                return method(snapshot[2], *args, **kwargs)
            return method(self._route(collection_name), *args, **kwargs)
        return routed
