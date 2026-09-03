"""异步任务队列管理器 - 支持文件上传队列化和进度追踪"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine
from datetime import datetime, timezone

logger = logging.getLogger("task_manager")

# Per-task cancellation events — checked by cooperative long-running operations
_cancel_events: dict[str, threading.Event] = {}
_current_task = threading.local()


def set_current_task(task_id: str):
    """Bind *task_id* to the calling thread so long-running ops can check cancellation."""
    _current_task.value = task_id


def clear_current_task():
    _current_task.value = ""


def check_cancelled():
    """Raise if the current thread's task has been cancelled."""
    tid = getattr(_current_task, "value", "")
    if tid:
        ev = _cancel_events.get(tid)
        if ev and ev.is_set():
            raise RuntimeError(f"Task {tid} cancelled")


class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    id: str
    filename: str
    collection: str = "default"
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0
    message: str = ""
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "collection": self.collection,
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

    def to_dict_with_type(self, task_type: str) -> dict[str, Any]:
        d = self.to_dict()
        d["task_type"] = task_type
        return d


class TaskManager:
    """Async task queue with separate channels for upload and light tasks.

    Uploads get a dedicated queue (default 5 concurrent) that gates
    parse/filter. After parse the slot is released so a slow OCR/Vision
    job cannot freeze later files. Summary, Context, and Vision share
    ``enrichment.max_parallel_context`` (default 50).
    Lightweight tasks share an unbounded concurrent general queue.
    """

    _UPLOAD_TYPES = {"upload"}

    def __init__(self, max_concurrent: int = 5, timeout: int = 3600):
        self.tasks: dict[str, Task] = {}
        self._task_args: dict[str, tuple[str, dict]] = {}  # task_id -> (task_type, kwargs)
        self._async_tasks: dict[str, asyncio.Task] = {}  # task_id -> asyncio.Task
        self._upload_queue: asyncio.Queue = asyncio.Queue()   # serial, 1 at a time
        self._general_queue: asyncio.Queue = asyncio.Queue()  # concurrent, up to max
        self.max_concurrent = max_concurrent
        self.timeout = timeout
        self._general_running = 0
        self._upload_running = 0
        self._upload_slot_released: set[str] = set()
        self._processors: list[asyncio.Task] = []
        self._handlers: dict[str, Callable[..., Coroutine]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def register_handler(self, task_type: str, handler: Callable[..., Coroutine]):
        """Register a task handler."""
        self._handlers[task_type] = handler

    async def start(self):
        """Start queue processors."""
        self._loop = asyncio.get_running_loop()
        if not self._processors:
            self._processors = [
                asyncio.create_task(self._process_upload_queue()),
                asyncio.create_task(self._process_general_queue()),
            ]

    async def stop(self):
        """Stop all queue processors."""
        for p in self._processors:
            p.cancel()
        for p in self._processors:
            try:
                await p
            except asyncio.CancelledError:
                pass
        self._processors.clear()

    def create_task(self, filename: str, task_type: str = "upload", collection: str = "default", **kwargs) -> Task:
        """Create and enqueue a new task.  Thread-safe: can be called from any thread.

        Prefer :meth:`create_task_async` from async request handlers so the
        enqueue is awaited on the running loop (avoids delayed dequeue when
        the handler later blocks the loop).
        """
        task = self._make_task(filename, collection)
        self._task_args[task.id] = (task_type, kwargs)
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._enqueue_task(task.id, task_type, kwargs), self._loop
            )
        else:
            asyncio.create_task(self._enqueue_task(task.id, task_type, kwargs))
        return task

    async def create_task_async(
        self,
        filename: str,
        task_type: str = "upload",
        collection: str = "default",
        **kwargs,
    ) -> Task:
        """Create and await enqueue on the current event loop (async-safe)."""
        task = self._make_task(filename, collection)
        self._task_args[task.id] = (task_type, kwargs)
        await self._enqueue_task(task.id, task_type, kwargs)
        return task

    def _make_task(self, filename: str, collection: str) -> Task:
        task_id = str(uuid.uuid4())
        task = Task(
            id=task_id,
            filename=filename,
            collection=collection,
            message="Queued for processing",
        )
        self.tasks[task_id] = task
        return task

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a pending or processing task.

        Sets a cancellation event that long-running operations
        (enrichment, embedding) check cooperatively.
        """
        task = self.tasks.get(task_id)
        if not task:
            return False
        if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            return False
        # Signal cooperative cancellation
        ev = _cancel_events.get(task_id)
        if ev:
            ev.set()
        atask = self._async_tasks.get(task_id)
        if atask and not atask.done():
            atask.cancel()
        task.status = TaskStatus.FAILED
        task.error = "Cancelled by user"
        task.message = "Cancelled"
        task.completed_at = datetime.now(timezone.utc)
        # A queued (not yet dequeued) upload never reaches the executor's
        # finally — clear its ledger row here so recovery won't resurrect it.
        ttype, _ = self._task_args.get(task_id, ("", {}))
        if ttype in self._UPLOAD_TYPES:
            self._finish_upload_task_ledger(task_id)
        return True

    def clear_completed_tasks(self) -> None:
        """Remove all completed or failed tasks."""
        to_remove = [tid for tid, t in self.tasks.items() if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)]
        for tid in to_remove:
            del self.tasks[tid]
            self._task_args.pop(tid, None)

    def retry_task(self, task_id: str) -> Task | None:
        """Re-enqueue a failed task."""
        task = self.tasks.get(task_id)
        if not task or task.status != TaskStatus.FAILED:
            return None
        args = self._task_args.get(task_id)
        if not args:
            return None
        task_type, kwargs = args
        task.status = TaskStatus.PENDING
        task.progress = 0.0
        task.message = "Re-queued"
        task.error = None
        task.result = None
        task.started_at = None
        task.completed_at = None
        asyncio.create_task(self._enqueue_task(task_id, task_type, kwargs))
        return task

    async def _enqueue_task(self, task_id: str, task_type: str, kwargs: dict):
        """Route task to the appropriate queue."""
        if task_type in self._UPLOAD_TYPES:
            # Ledger first so a crash between queueing and execution is recoverable
            self._persist_upload_task(task_id, kwargs)
            await self._upload_queue.put((task_id, task_type, kwargs))
        else:
            await self._general_queue.put((task_id, task_type, kwargs))

    def _persist_upload_task(self, task_id: str, kwargs: dict) -> None:
        """Best-effort durable ledger write for upload tasks (see task_persistence)."""
        task = self.tasks.get(task_id)
        if task is None:
            return
        try:
            from src.tasks.task_persistence import record_upload_task
            record_upload_task(task.id, task.collection, {
                "filename": task.filename, **kwargs,
            })
        except Exception as e:
            logger.warning("Failed to persist upload task %s: %s", task_id, e)

    @staticmethod
    def _finish_upload_task_ledger(task_id: str) -> None:
        """Best-effort ledger clear once an upload task reached a terminal state."""
        try:
            from src.tasks.task_persistence import finish_upload_task
            finish_upload_task(task_id)
        except Exception as e:
            logger.warning("Failed to clear upload task ledger for %s: %s", task_id, e)

    # ── Upload queue (limited concurrent) ──────────────────────────────

    async def _process_upload_queue(self):
        """Process uploads with limited concurrency.

        Parse is limited here; handlers release the slot after parse so
        OCR / Vision / enrich of one file cannot stall the next.
        """
        logger.info("Upload queue processor started (max %d concurrent)", self.max_concurrent)
        while True:
            try:
                task_id, task_type, kwargs = await self._upload_queue.get()
                logger.info("Dequeued upload task %s", task_id)

                while self._upload_running >= self.max_concurrent:
                    await asyncio.sleep(0.1)

                self._upload_running += 1
                atask = asyncio.create_task(
                    self._execute_upload_task(task_id, task_type, kwargs))
                self._async_tasks[task_id] = atask

            except asyncio.CancelledError:
                logger.info("Upload queue processor cancelled")
                break
            except Exception as e:
                logger.error("Upload queue processor error: %s", e, exc_info=True)

    def release_upload_slot(self, task_id: str) -> None:
        """Free this upload's concurrency slot after parse/filter.

        OCR, Vision, Summary, and Context keep running on the same task.
        They are capped by their own pools and the ingest LLM limiter.
        One slow image job must not freeze later files in the upload queue.
        Idempotent; safe to call from the handler on the event loop.
        """
        if not task_id or task_id in self._upload_slot_released:
            return
        self._upload_slot_released.add(task_id)
        if self._upload_running > 0:
            self._upload_running -= 1
            logger.info(
                "Released upload slot early for %s (running=%d)",
                task_id, self._upload_running,
            )

    async def _execute_upload_task(self, task_id: str, task_type: str, kwargs: dict):
        """Execute an upload, decrementing the upload counter on completion."""
        try:
            await self._execute_task(task_id, task_type, kwargs)
        finally:
            if task_id not in self._upload_slot_released:
                self._upload_running -= 1
            else:
                self._upload_slot_released.discard(task_id)
            # Terminal (completed / failed / timed out / cancelled) — the
            # ledger row must go or recovery would re-run the task.
            self._finish_upload_task_ledger(task_id)

    # ── Upload task crash recovery ─────────────────────────────────────

    @staticmethod
    def _version_is_current(collection_id: str, file_id: str, version_id: str) -> bool:
        """True unless meta.db shows a newer current_version for the file.

        A missing meta.db means the collection was deleted while the
        process was down → False (drop the task). An unreadable meta.db
        returns True so the handler surfaces the real error instead of
        silently dropping the file.
        """
        try:
            from src.file_mgmt.store import _db_path
            path = _db_path(collection_id)
            if not path.exists():
                return False
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
            try:
                row = conn.execute(
                    "SELECT current_version_id FROM files WHERE file_id=?",
                    (file_id,),
                ).fetchone()
            finally:
                conn.close()
            return bool(row) and row[0] == version_id
        except Exception as e:
            logger.warning(
                "Version check failed for %s/%s (%s); will retry ingest",
                file_id, version_id, e,
            )
            return True

    async def recover_upload_tasks(self, settle_delay: float = 15.0) -> int:
        """Re-enqueue upload tasks left in the durable ledger by a crash.

        Only the newest pending task per (collection, file) is recovered —
        superseded versions and duplicate uploads collapse into one. Tasks
        whose file version is no longer current are dropped. Call after
        ``start()`` once services are initialized; *settle_delay* lets
        Qdrant and local models come up before the first recovered embed.
        """
        from src.tasks import task_persistence
        rows = task_persistence.pending_upload_tasks()
        if not rows:
            return 0
        logger.info("Recovering %d persisted upload task(s)", len(rows))

        newest: dict[tuple[str, str], dict] = {}
        for row in rows:
            payload = row["payload"]
            key = (
                row["collection_id"],
                payload.get("file_id") or payload.get("file_path") or row["task_id"],
            )
            prev = newest.get(key)
            if prev is None or row["created_at"] >= prev["created_at"]:
                newest[key] = row

        keep_ids = {row["task_id"] for row in newest.values()}
        for row in rows:
            if row["task_id"] not in keep_ids:
                # Superseded duplicate — drop so it can't win a later
                # recovery once the newer row completes and is cleared.
                logger.info(
                    "Dropping superseded duplicate upload task %s", row["task_id"]
                )
                self._finish_upload_task_ledger(row["task_id"])
        survivors = list(newest.values())
        recovered = 0
        # Settle BEFORE enqueueing: recovered tasks are the most likely to
        # race a still-booting Qdrant, and a failed recovered task would
        # lose its ledger row (terminal cleanup) — orphaning the file again.
        if survivors and settle_delay > 0:
            await asyncio.sleep(settle_delay)
        for row in survivors:
            task_id = row["task_id"]
            if row["file_id"] and row["version_id"] and not self._version_is_current(
                row["collection_id"], row["file_id"], row["version_id"]
            ):
                logger.info(
                    "Dropping stale upload task %s (version superseded or collection gone)",
                    task_id,
                )
                self._finish_upload_task_ledger(task_id)
                continue
            payload = dict(row["payload"])
            filename = payload.pop("filename", "recovered-upload")
            if task_id not in self.tasks:
                self.tasks[task_id] = Task(
                    id=task_id,
                    filename=filename,
                    collection=row["collection_id"],
                    message="Requeued after restart",
                )
            self._task_args[task_id] = ("upload", payload)
            asyncio.get_running_loop().create_task(
                self._enqueue_task(task_id, "upload", payload)
            )
            recovered += 1

        logger.info("Recovered %d upload task(s) from ledger", recovered)
        return recovered

    # ── General queue (concurrent) ─────────────────────────────────────

    async def _process_general_queue(self):
        """Concurrent processor: up to max_concurrent tasks in parallel."""
        logger.info("General queue processor started")
        while True:
            try:
                task_id, task_type, kwargs = await self._general_queue.get()
                logger.info("Dequeued general task %s type=%s", task_id, task_type)

                while self._general_running >= self.max_concurrent:
                    await asyncio.sleep(0.1)

                self._general_running += 1
                logger.info("Executing general task %s type=%s (running=%d)",
                            task_id, task_type, self._general_running)
                atask = asyncio.create_task(
                    self._execute_general_task(task_id, task_type, kwargs))
                self._async_tasks[task_id] = atask

            except asyncio.CancelledError:
                logger.info("General queue processor cancelled")
                break
            except Exception as e:
                logger.error("General queue processor error: %s", e, exc_info=True)

    async def _execute_general_task(self, task_id: str, task_type: str, kwargs: dict):
        """Execute a general-queue task, tracking _general_running."""
        try:
            await self._execute_task(task_id, task_type, kwargs)
        finally:
            self._general_running -= 1

    # ── Task execution ─────────────────────────────────────────────────

    async def _execute_task(self, task_id: str, task_type: str, kwargs: dict):
        """Execute a single task with timeout."""
        task = self.tasks.get(task_id)
        if not task:
            return

        task.status = TaskStatus.PROCESSING
        task.started_at = datetime.now(timezone.utc)
        task.message = "Processing..."
        # Create cancellation event for cooperative cancellation
        cancel_event = threading.Event()
        _cancel_events[task_id] = cancel_event
        logger.info("[TASK %s] Starting execution: type=%s kwargs=%s",
                    task_id, task_type,
                    {k: v for k, v in kwargs.items() if k != "file_path"})

        try:
            handler = self._handlers.get(task_type)
            if not handler:
                raise ValueError(f"No handler registered for task type: {task_type}")

            kwargs["collection"] = task.collection
            loop = asyncio.get_running_loop()

            async def _run():
                if asyncio.iscoroutinefunction(handler):
                    return await handler(task, **kwargs)
                return await loop.run_in_executor(None, lambda: handler(task, **kwargs))

            result = await asyncio.wait_for(_run(), timeout=self.timeout)

            task.status = TaskStatus.COMPLETED
            task.progress = 100.0
            task.message = "Completed"
            task.result = result
            task.completed_at = datetime.now(timezone.utc)
            logger.info("[TASK %s] COMPLETED: %s", task_id, result)

        except asyncio.TimeoutError:
            task.status = TaskStatus.FAILED
            task.error = f"Task timed out after {self.timeout}s"
            task.message = f"Failed: timed out after {self.timeout}s"
            task.completed_at = datetime.now(timezone.utc)
            logger.error("[TASK %s] TIMED OUT after %ds", task_id, self.timeout)

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.message = f"Failed: {str(e)}"
            task.completed_at = datetime.now(timezone.utc)
            logger.error("[TASK %s] FAILED: %s", task_id, e, exc_info=True)

        finally:
            self._async_tasks.pop(task_id, None)
            _cancel_events.pop(task_id, None)

    def get_task(self, task_id: str) -> Task | None:
        """获取任务状态"""
        return self.tasks.get(task_id)

    def get_all_tasks(self, collection: str | None = None) -> list[Task]:
        """获取所有任务，可按collection过滤"""
        tasks = self.tasks.values()
        if collection:
            tasks = [t for t in tasks if t.collection == collection]
        return list(tasks)

    def get_pending_tasks(self, collection: str | None = None) -> list[Task]:
        """获取待处理任务"""
        tasks = [t for t in self.tasks.values() if t.status == TaskStatus.PENDING]
        if collection:
            tasks = [t for t in tasks if t.collection == collection]
        return tasks

    def get_processing_tasks(self, collection: str | None = None) -> list[Task]:
        """获取正在处理的任务"""
        tasks = [t for t in self.tasks.values() if t.status == TaskStatus.PROCESSING]
        if collection:
            tasks = [t for t in tasks if t.collection == collection]
        return tasks

    def get_active_tasks(self, collection: str | None = None, task_type: str | None = None,
                         task_types: list[str] | None = None) -> list[dict]:
        """Get active (pending/processing) tasks, optionally filtered by collection and type(s).

        Use ``task_types`` to match multiple types in one atomic scan.
        """
        result = []
        for task_id, task in self.tasks.items():
            if task.status not in (TaskStatus.PENDING, TaskStatus.PROCESSING):
                continue
            if collection and task.collection != collection:
                continue
            ttype, _ = self._task_args.get(task_id, ("unknown", {}))
            if task_types:
                if ttype not in task_types:
                    continue
            elif task_type and ttype != task_type:
                continue
            result.append(task.to_dict_with_type(ttype))
        return result

    def has_active_task(self, collection: str, task_type: str) -> bool:
        """Check if there's an active task of given type for a collection."""
        for task_id, task in self.tasks.items():
            if task.status not in (TaskStatus.PENDING, TaskStatus.PROCESSING):
                continue
            if task.collection != collection:
                continue
            ttype, _ = self._task_args.get(task_id, ("unknown", {}))
            if ttype == task_type:
                return True
        return False


# 全局任务管理器实例
task_manager = TaskManager()
