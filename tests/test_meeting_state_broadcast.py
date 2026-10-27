"""会议状态变化要实时广播到其他端（2026-09-17 用户要求）。

**用户报的两件事**：
1. 「有人在用 Meeting 录制的时候，这个 meeting 的状态应该要锁住，但是这个锁住的
   状态没有立刻更新到其他端上面，要刷新才会锁住」；
2. 「如果 Meeting 在转录中或者什么，原本有的那些 Meeting list 卡片会有的状态也要
   同步到其他端」。

**根因不在前端**——`planActions("meetings", …)` 早就正确（刷新列表 + 刷新详情），
缺的是**服务端根本没发事件**：状态是从很多地方改的（请求路由、后台转写/总结线程、
录制 PCM 落盘），而只有少数路由手写了 emit。

**修法**：把广播下沉到**唯一的写入漏斗** `store.update_meeting`——只在
`_VISIBLE_STATE_FIELDS`（列表卡片上看得见的那些）真变化时发一次。
逐个路由加必然漏，而漏掉的那条就是用户看到的"要刷新才同步"。

**为什么只挑那几个字段**：`update_meeting` 每次进度写入都会走，对任何字段都广播
会让一条转写进度刷屏（十几个事件/秒）。
"""

from __future__ import annotations

import pytest

from src.meeting import store
from src.meeting.models import Meeting, MeetingStatus, ProcessingState


@pytest.fixture
def meeting(tmp_path, monkeypatch):
    root = tmp_path / "meetings"
    mid = "m_state"
    (root / mid).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "MEETINGS_DIR", root)
    m = Meeting(id=mid, title="t")
    (root / mid / "meta.json").write_text(
        store._meeting_to_dict(m) and __import__("json").dumps(store._meeting_to_dict(m)),
        encoding="utf-8",
    )
    store._read_cache.clear() if hasattr(store, "_read_cache") else None
    return mid


class _Recorder:
    """接住 emit 调用（不经真实总线，便于断言）。"""

    def __init__(self, monkeypatch):
        self.calls: list[tuple[str, dict, dict]] = []
        import src.events.emit as emit_mod

        def fake_emit(event_type, *, scope=None, payload=None, origin=None):
            self.calls.append((event_type, scope or {}, payload or {}))
            return None

        monkeypatch.setattr(emit_mod, "emit", fake_emit)
        self._rec = self

    @property
    def types(self):
        return [c[0] for c in self.calls]


def _fresh(monkeypatch, tmp_path):
    root = tmp_path / "meetings"
    mid = "m_state"
    (root / mid).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "MEETINGS_DIR", root)
    m = Meeting(id=mid, title="t")
    import json

    (root / mid / "meta.json").write_text(json.dumps(store._meeting_to_dict(m)), encoding="utf-8")
    store._read_cache.clear()
    return mid


class TestVisibleStateBroadcast:
    def test_status_change_emits(self, monkeypatch, tmp_path):
        mid = _fresh(monkeypatch, tmp_path)
        rec = _Recorder(monkeypatch)
        store.update_meeting(mid, status=MeetingStatus.recording)
        assert rec.types == ["meeting.updated"]
        assert rec.calls[0][1] == {"meeting_id": mid}
        assert "status" in rec.calls[0][2]["changed"]

    def test_recording_transition_matches_user_scenario(self, monkeypatch, tmp_path):
        """用户场景①：有人开始录 → 别的端要立刻看到（锁住那行）。"""
        mid = _fresh(monkeypatch, tmp_path)
        rec = _Recorder(monkeypatch)
        store.update_meeting(mid, status=MeetingStatus.recording)
        assert rec.calls[0][2]["status"] == "recording"

    def test_transcribing_and_summarizing_emit(self, monkeypatch, tmp_path):
        """用户场景②：转录中 / 总结中这些卡片状态也要同步。"""
        mid = _fresh(monkeypatch, tmp_path)
        rec = _Recorder(monkeypatch)
        store.update_meeting(mid, status=MeetingStatus.transcribing)
        store.update_meeting(mid, status=MeetingStatus.completed)
        store.update_meeting(mid, processing_state=ProcessingState.summarizing.value)
        store.update_meeting(mid, processing_state=ProcessingState.extracting.value)
        assert rec.types == ["meeting.updated"] * 4
        payloads = [c[2] for c in rec.calls]
        assert any("processing_state" in p["changed"] for p in payloads)

    def test_no_event_when_nothing_visible_changed(self, monkeypatch, tmp_path):
        """改标题/写进度不该打扰对端（否则一条转写进度能刷屏）。"""
        mid = _fresh(monkeypatch, tmp_path)
        rec = _Recorder(monkeypatch)
        store.update_meeting(mid, title="renamed")
        store.update_meeting(mid, summary="…长文本…")
        assert rec.types == [], "非可见状态字段不该广播"

    def test_same_value_does_not_re_emit(self, monkeypatch, tmp_path):
        """幂等：写同一个 status 不该反复广播。"""
        mid = _fresh(monkeypatch, tmp_path)
        store.update_meeting(mid, status=MeetingStatus.transcribing)
        rec = _Recorder(monkeypatch)
        store.update_meeting(mid, status=MeetingStatus.transcribing)
        assert rec.types == []

    def test_emit_failure_does_not_break_write(self, monkeypatch, tmp_path):
        """广播是**告知**不是主流程：它炸了不能影响状态落盘。"""
        mid = _fresh(monkeypatch, tmp_path)
        import src.events.emit as emit_mod

        monkeypatch.setattr(
            emit_mod, "emit",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bus down")),
        )
        m = store.update_meeting(mid, status=MeetingStatus.completed)
        assert m.status == MeetingStatus.completed
        assert store.get_meeting(mid).status == MeetingStatus.completed

    def test_background_thread_write_also_emits(self, monkeypatch, tmp_path):
        """后台线程（转写/总结跑完）也必须广播——这是"要刷新才同步"的主要漏点。"""
        import threading

        mid = _fresh(monkeypatch, tmp_path)
        rec = _Recorder(monkeypatch)
        done = threading.Event()

        def work():
            store.update_meeting(mid, status=MeetingStatus.completed)
            done.set()

        t = threading.Thread(target=work, daemon=True)
        t.start()
        assert done.wait(5), "后台写入超时"
        t.join(2)
        assert rec.types == ["meeting.updated"], "后台线程写入没有广播"


class TestRecordingPcmTriggersBroadcast:
    def test_first_pcm_flips_status_and_emits(self, monkeypatch, tmp_path):
        """录制开始 = 第一次 PCM 落盘（status 就是那时翻的）→ 必须广播。"""
        mid = _fresh(monkeypatch, tmp_path)
        rec = _Recorder(monkeypatch)
        store.append_recording_pcm(mid, b"\x00" * 128)
        assert rec.types == ["meeting.updated"]
        assert rec.calls[0][2]["status"] == "recording"

    def test_subsequent_pcm_does_not_spam(self, monkeypatch, tmp_path):
        """之后每 500ms 一次的 PCM 不该反复广播。"""
        mid = _fresh(monkeypatch, tmp_path)
        store.append_recording_pcm(mid, b"\x00" * 128)  # 第一次：翻状态
        rec = _Recorder(monkeypatch)
        for _ in range(5):
            store.append_recording_pcm(mid, b"\x00" * 128)
        assert rec.types == [], "录制过程中的 PCM 不该每次都广播"
