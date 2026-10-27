"""会议"正在录制"的活跃判定 + 旁观者不可进入（2026-09-17）。

**用户要求**：「当会议正在录制的时候，其他人在这个会议列表上应该也要看得到录制中，
然后不可以点进去。」

**为什么不能直接用 `status == "recording"`**：它只说明"开始录过"——自己页面刷新后
那个状态还挂着（所以才有孤儿回收）。用它当"正在录"的判据会出两个错：
1. 旁观者看到"录制中"但其实早断了（假阳性）；
2. 更糟的是**旁观者一打开会议列表就可能把别人正在进行的录制掐断**——孤儿回收
   逻辑会挑中这台机器上"没在录但 status=recording"的会议去 finalize。

所以引入 `store.recording_is_live()`：按 PCM 文件的写入新鲜度判断"此刻是否真在录"。

跑法：``pytest tests/test_meeting_recording_live.py``
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import pytest


@pytest.fixture
def meet_dir(tmp_path, monkeypatch):
    from src.meeting import store as ms

    root = tmp_path / "meetings"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ms, "MEETINGS_DIR", root)
    yield root


def _touch_pcm(root, meeting_id: str, *, age_s: float):
    from src.meeting.store import RECORDING_PCM_NAME

    d = root / meeting_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / RECORDING_PCM_NAME
    p.write_bytes(b"\x00" * 64)
    if age_s:
        old = time.time() - age_s
        os.utime(p, (old, old))
    return p


class TestRecordingIsLive:
    def test_recent_pcm_means_live(self, meet_dir):
        _touch_pcm(meet_dir, "m1", age_s=2)
        from src.meeting.store import recording_is_live

        assert recording_is_live("m1") is True

    def test_stale_pcm_means_not_live(self, meet_dir):
        """60 分钟前写过的 PCM = 早断了，是可回收的孤儿，不是"正在录"。"""
        _touch_pcm(meet_dir, "m1", age_s=3600)
        from src.meeting.store import recording_is_live

        assert recording_is_live("m1") is False

    def test_no_pcm_means_not_live(self, meet_dir):
        from src.meeting.store import recording_is_live

        assert recording_is_live("nope") is False

    def test_window_is_configurable(self, meet_dir):
        _touch_pcm(meet_dir, "m1", age_s=120)
        from src.meeting.store import recording_is_live

        assert recording_is_live("m1") is False
        assert recording_is_live("m1", window_s=300) is True

    def test_boundary_just_inside_window(self, meet_dir):
        _touch_pcm(meet_dir, "m1", age_s=10)
        from src.meeting.store import RECORDING_LIVE_WINDOW_S, recording_is_live

        assert RECORDING_LIVE_WINDOW_S >= 30, "窗口太短会被正常的 500ms 采集节奏误判"
        assert recording_is_live("m1") is True


class TestListAttachesLiveness:
    def test_recording_live_added_to_payload(self, meet_dir, monkeypatch):
        from src.identity.actor import _current, _set_actor
        from src.identity import Actor
        from src.meeting import routes as mr

        _touch_pcm(meet_dir, "m1", age_s=1)
        rows = [{"id": "m1"}]
        token = _set_actor(Actor())  # owner：permissions 一定通过
        try:
            mr._attach_meeting_permissions(rows)
        finally:
            _current.reset(token)
        assert rows[0]["recording_live"] is True

    def test_probe_failure_is_conservative(self, meet_dir, monkeypatch):
        """liveness 探针炸了 → False（宁可少锁一次，也不误锁）。"""
        from src.identity.actor import _current, _set_actor
        from src.identity import Actor
        from src.meeting import routes as mr
        from src.meeting import store as ms

        monkeypatch.setattr(
            ms, "recording_is_live",
            lambda mid, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        rows = [{"id": "m1"}]
        token = _set_actor(Actor())
        try:
            mr._attach_meeting_permissions(rows)
        finally:
            _current.reset(token)
        assert rows[0]["recording_live"] is False
