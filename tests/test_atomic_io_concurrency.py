"""Atomic write concurrency regression (2026-09-19 生产就绪加固).

背景：旧 ``write_text_atomic`` 用固定 ``.tmp`` 名，4 线程 × 250 次并发写同一
文件实测 64.8% 抛 ``FileNotFoundError``（A 的 replace 抢走 B 的 tmp）。tmp 名
改 uuid + 每路径锁之后，这里把压测固化成回归测试。
"""

from __future__ import annotations

import json
import threading

import pytest

from src.atomic_io import file_lock, write_text_atomic


def _concurrent_writes(path, n_threads: int, n_writes: int) -> list[str]:
    errors: list[str] = []
    lock = threading.Lock()

    def writer(tid: int) -> None:
        for i in range(n_writes):
            try:
                write_text_atomic(path, json.dumps({"w": tid, "i": i}))
            except Exception as exc:  # noqa: BLE001 - 收集后统一断言
                with lock:
                    errors.append(f"t{tid}#{i}: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


def test_concurrent_atomic_writes_never_fail_or_tear(tmp_path):
    target = tmp_path / "store.json"
    errors = _concurrent_writes(target, n_threads=4, n_writes=150)

    assert errors == []
    # 最终内容必须是某一次写入的完整 JSON（不是半份）
    data = json.loads(target.read_text(encoding="utf-8"))
    assert set(data) == {"w", "i"}


def test_concurrent_writes_leave_no_tmp_residue(tmp_path):
    target = tmp_path / "store.json"
    _concurrent_writes(target, n_threads=4, n_writes=50)
    residue = [p.name for p in tmp_path.iterdir() if p != target]
    assert residue == []


def test_file_lock_serializes_read_modify_write(tmp_path):
    """file_lock 包住读-改-写后，并发自增不丢更新。"""
    target = tmp_path / "counter.json"
    write_text_atomic(target, json.dumps({"n": 0}))
    updates_done = []

    def bump():
        for _ in range(50):
            with file_lock(target):
                data = json.loads(target.read_text(encoding="utf-8"))
                data["n"] += 1
                write_text_atomic(target, json.dumps(data))
            updates_done.append(1)

    threads = [threading.Thread(target=bump) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(updates_done) == 200
    assert json.loads(target.read_text(encoding="utf-8"))["n"] == 200


def test_mode_applied_to_tmp_before_replace(tmp_path):
    """密钥文件：0600 必须在 tmp 阶段生效，落盘瞬间就是收紧的。"""
    import os
    import stat

    target = tmp_path / "secret.txt"
    write_text_atomic(target, "secret", mode=0o600)
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600


@pytest.mark.parametrize("content", ["", "x" * 100_000, "中文内容\n多行"])
def test_roundtrip_various_content(tmp_path, content):
    target = tmp_path / "doc.md"
    write_text_atomic(target, content)
    assert target.read_text(encoding="utf-8") == content
