"""异步任务队列管理器 - 支持文件上传队列化和进度追踪"""

from __future__ import annotations

import asyncio
import logging
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

    Uploads get a dedicated queue capped at 2-3 concurrent so parsing
    runs in parallel while ``_enrich_lock`` serializes only enrichment.
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
        """Create and enqueue a new task.  Thread-safe: can be called from any thread."""
        task_id = str(uuid.uuid4())
        task = Task(
            id=task_id,
            filename=filename,
            collection=collection,
            message="Queued for processing",
        )
        self.tasks[task_id] = task
        self._task_args[task_id] = (task_type, kwargs)
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._enqueue_task(task_id, task_type, kwargs), self._loop
            )
        else:
            asyncio.create_task(self._enqueue_task(task_id, task_type, kwargs))
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
            await self._upload_queue.put((task_id, task_type, kwargs))
        else:
            await self._general_queue.put((task_id, task_type, kwargs))

    # ── Upload queue (limited concurrent) ──────────────────────────────

    async def _process_upload_queue(self):
        """Process uploads with limited concurrency.

        Parsing overlaps across uploads; ``_enrich_lock`` serializes the
        actual enrichment step so thread pools don't stack up.
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

    async def _execute_upload_task(self, task_id: str, task_type: str, kwargs: dict):
        """Execute an upload, decrementing the upload counter on completion."""
        try:
            await self._execute_task(task_id, task_type, kwargs)
        finally:
            self._upload_running -= 1

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
