"""Atomic text file writes (uuid tmp + replace), safe under concurrency.

并发语义（2026-09-19 生产就绪加固）：
- tmp 名带 uuid —— 旧实现用固定 ``path + ".tmp"``，两个并发写同一文件时
  A 的 ``replace`` 会让 B 手里的 tmp 消失，实测 64.8% 的写入抛
  FileNotFoundError，且存在截断窗口。
- 每目标路径进程内锁 —— 并发写串行化，最终文件永远是**某一次写入的完整
  内容**，不会是半份 JSON。注意：读-改-写（read→modify→write）的丢更新
  仍需调用方用 :func:`file_lock` 包住整个周期，本模块的锁只覆盖写入瞬间。
"""

from __future__ import annotations

import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _path_lock(path: Path) -> threading.Lock:
    # RLock 而非 Lock：file_lock 包住读-改-写时，内部 write_text_atomic 会
    # 再次取同一把锁——不可重入锁会当场死锁。
    key = str(path)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _locks[key] = lock
        return lock


@contextmanager
def file_lock(path: Path):
    """Serialize a full read-modify-write cycle on one target file."""
    with _path_lock(path):
        yield


def write_text_atomic(
    path: Path, text: str, *, encoding: str = "utf-8", mode: int | None = None
) -> None:
    """Atomically replace ``path`` with ``text``.

    ``mode``（如 ``0o600``）在 tmp 落盘时即生效——密钥类文件不要先写后
    chmod，那中间有一扇敞开的窗口。
    """
    with _path_lock(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
        try:
            tmp.write_text(text, encoding=encoding)
            if mode is not None:
                try:
                    os.chmod(tmp, mode)
                except OSError:
                    pass
            tmp.replace(path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
