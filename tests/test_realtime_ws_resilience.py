"""Realtime transcription resilience: upstream-loss surfacing, silence keepalive,
    probe counters (seq / stats), and adapter self-heal.

背景（2026-09-16）：用户报「实时转写中间经常漏掉几句」。四个已从源码坐实的缺陷：

1. 客户端暂停 → 停发 PCM，而 DashScope SDK 有 23s 静默计时器
   （Recognition.SILENCE_TIMEOUT_S）会杀掉会话；恢复时第一帧抛错，
   整条 WS 被拆掉重连 → 恢复后头几句丢失。
2. 换引擎/换翻译的 2.2s 交接窗口里，socket 还开着但没人读，音频发进去即丢。
3. 上游断连（网络/SDK 静默超时）只在 on_error/on_close 里打日志，无人知晓。
4. 自动重连不递增 session 计数 → 句子 key 与上一会话撞车 → 旧句被覆盖。

另附探针：服务端给每条 transcript 带自增 seq、周期性推 counters，
前端按缺口报数，用于区分「云端没返」「返了没显示」「显示了又没了」。
"""

from __future__ import annotations

import asyncio
import itertools
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ── 探针与保活的测试替身 ───────────────────────────────────────────


class _FakeProvider:
    # 云端适配器才需要静音保活（见 base.RealtimeTranscriptionProvider）
    wants_silence_keepalive = True

    def __init__(self):
        self.frames: list[bytes] = []
        self.on_segment = None

    async def start(self, on_segment, hot_words=None, language_hints=None, translation_target=None):
        self.on_segment = on_segment

    async def send_frame(self, data):
        self.frames.append(data)

    async def stop(self):
        pass


def _setup(monkeypatch, tmp_path):
    import src.meeting.store as meeting_store
    from src.pairing import service as pairing_service
    from src.pairing import store as pairing_store

    monkeypatch.setattr(meeting_store, "MEETINGS_DIR", tmp_path)
    # 配对门卫拦 WS：给本测试一个隔离的钥匙目录并签发一把钥匙（否则 1008）
    monkeypatch.setattr(pairing_store, "PAIRING_DIR", tmp_path / "pairing")
    token = pairing_service.issue_token(name="rt-test", is_local=True)["token"]
    meeting = meeting_store.create_meeting("rt resilience")
    provider = _FakeProvider()
    fake_service = MagicMock()
    fake_service.get_active_realtime_provider_meta.return_value = {
        "id": "x",
        "adapter": "fake",
        "name": "Fake",
        "model": "m",
    }
    fake_service.get_active_realtime_provider.return_value = provider
    monkeypatch.setattr("src.meeting.routes.meeting_service", fake_service)
    meeting._rt_token = token  # type: ignore[attr-defined]
    return meeting, provider


def _open(meeting):
    from fastapi.testclient import TestClient

    from src.main import app

    client = TestClient(app)
    token = getattr(meeting, "_rt_token", "")
    ws = client.websocket_connect(
        f"/api/meetings/{meeting.id}/realtime-transcribe?token={token}"
    )
    ws.__enter__()
    assert ws.receive_json()["type"] == "provider"
    assert ws.receive_json()["type"] == "ready"
    return client, ws


def _recv_until(ws, pred, limit=80):
    """Read frames until pred matches.

    TestClient's receive_json() blocks with **no timeout**, so a missing message
    hangs the suite instead of failing it. With keepalive/stats patched to tens
    of milliseconds, a bounded number of reads is enough — and if the server
    stops sending, we hit the cap and fail loudly rather than hang.
    """
    for _ in range(limit):
        msg = ws.receive_json()
        if pred(msg):
            return msg
    raise AssertionError(f"expected message not received within {limit} frames")


def _assert_no_message(ws, pred, probe=6):
    """Assert no matching frame arrives within a few reads (drives keepalive)."""
    for _ in range(probe):
        msg = ws.receive_json()
        assert not pred(msg), f"unexpected message: {msg}"


def _seg(text="line", start=0.0):
    return SimpleNamespace(start=start, end=start + 1.0, text=text, speaker_id=None)


# ── 探针（后端）─────────────────────────────────────────────────────


class TestTranscriptSequence:
    def test_payloads_carry_monotonic_seq(self, monkeypatch, tmp_path):
        meeting, provider = _setup(monkeypatch, tmp_path)
        client, ws = _open(meeting)
        try:
            provider.on_segment(_seg("a"), False, "k1")
            first = ws.receive_json()
            provider.on_segment(_seg("b"), False, "k2")
            second = ws.receive_json()
        finally:
            ws.__exit__(None, None, None)

        assert first["type"] == "transcript" and first["seq"] == 1
        assert second["type"] == "transcript" and second["seq"] == 2

    def test_seq_survives_partial_then_final_update(self, monkeypatch, tmp_path):
        # 同一句的 partial→final 更新是两条消息、两个 seq：前端据此发现缺口，
        # 而句子层的覆盖由 key 决定（两者互不干扰）。
        meeting, provider = _setup(monkeypatch, tmp_path)
        client, ws = _open(meeting)
        try:
            provider.on_segment(_seg("hel"), False, "s1")
            provider.on_segment(_seg("hello"), True, "s1")
            a = ws.receive_json()
            b = ws.receive_json()
        finally:
            ws.__exit__(None, None, None)
        assert (a["seq"], b["seq"]) == (1, 2)
        assert a["is_final"] is False and b["is_final"] is True


class TestStatsProbe:
    def test_stats_reports_counters(self, monkeypatch, tmp_path):
        monkeypatch.setattr("src.meeting.routes.REALTIME_KEEPALIVE_TICK_S", 0.05)
        monkeypatch.setattr("src.meeting.routes.REALTIME_STATS_INTERVAL_S", 0.1)
        meeting, provider = _setup(monkeypatch, tmp_path)
        client, ws = _open(meeting)
        try:
            ws.send_bytes(b"\x00\x01")
            provider.on_segment(_seg(), True, "k1")
            stats = _recv_until(ws, lambda m: m.get("type") == "stats" and m.get("frames_in", 0) >= 1)
        finally:
            ws.__exit__(None, None, None)
        assert stats["segments_out"] >= 1
        assert stats["upstream_losses"] == 0


# ── 上游断连上报 ────────────────────────────────────────────────────


class TestCoverageProbe:
    """seq 只能发现「服务端发了但前端没收到」；抓不到「音频进去了、云端没返」。
    覆盖率（收进来的音频秒数 vs 转出转写的秒数）才是那个盲区的量。"""

    def test_stats_reports_audio_and_text_seconds(self, monkeypatch, tmp_path):
        monkeypatch.setattr("src.meeting.routes.REALTIME_KEEPALIVE_TICK_S", 0.05)
        monkeypatch.setattr("src.meeting.routes.REALTIME_STATS_INTERVAL_S", 0.1)
        meeting, provider = _setup(monkeypatch, tmp_path)
        client, ws = _open(meeting)
        try:
            # 4 帧 × 16000B = 2.0 秒音频（16kHz mono 16-bit = 32000 B/s）
            for _ in range(4):
                ws.send_bytes(b"\x00" * 16000)
            # 云端只返了一句 1.5 秒的转写
            provider.on_segment(
                SimpleNamespace(start=0.0, end=1.5, text="hello", speaker_id=None), True, "k1"
            )
            stats = _recv_until(
                ws, lambda m: m.get("type") == "stats" and m.get("audio_s", 0) >= 2.0
            )
        finally:
            ws.__exit__(None, None, None)
        assert stats["audio_s"] == pytest.approx(2.0, abs=0.05)
        assert stats["text_s"] == pytest.approx(1.5, abs=0.05)

    def test_repeated_final_for_same_key_not_double_counted(self, monkeypatch, tmp_path):
        # 云端会对同一句反复发 final（细化 end_time）；累计必须按 key 去重
        monkeypatch.setattr("src.meeting.routes.REALTIME_KEEPALIVE_TICK_S", 0.05)
        monkeypatch.setattr("src.meeting.routes.REALTIME_STATS_INTERVAL_S", 0.1)
        meeting, provider = _setup(monkeypatch, tmp_path)
        client, ws = _open(meeting)
        try:
            for end in (1.0, 1.2, 1.5):
                provider.on_segment(
                    SimpleNamespace(start=0.0, end=end, text="hello", speaker_id=None), True, "k1"
                )
            stats = _recv_until(
                ws, lambda m: m.get("type") == "stats" and m.get("text_s", 0) >= 1.5
            )
        finally:
            ws.__exit__(None, None, None)
        assert stats["text_s"] == pytest.approx(1.5, abs=0.05)

    def test_silence_frames_not_counted_as_audio(self, monkeypatch, tmp_path):
        # 保活喂的静音不是用户说话，不能算进覆盖率分母（否则覆盖率永远虚低）
        monkeypatch.setattr("src.meeting.routes.REALTIME_KEEPALIVE_TICK_S", 0.05)
        monkeypatch.setattr("src.meeting.routes.REALTIME_KEEPALIVE_IDLE_S", 0.1)
        monkeypatch.setattr("src.meeting.routes.REALTIME_STATS_INTERVAL_S", 0.1)
        meeting, provider = _setup(monkeypatch, tmp_path)
        client, ws = _open(meeting)
        try:
            stats = _recv_until(
                ws, lambda m: m.get("type") == "stats" and m.get("silence_injected", 0) >= 1
            )
        finally:
            ws.__exit__(None, None, None)
        assert stats["audio_s"] == 0.0


class TestUpstreamLoss:
    def test_provider_lost_surfaces_to_client(self, monkeypatch, tmp_path):
        meeting, provider = _setup(monkeypatch, tmp_path)
        client, ws = _open(meeting)
        try:
            assert callable(provider.on_upstream_lost)
            provider.on_upstream_lost("silence_timeout")
            msg = ws.receive_json()
        finally:
            ws.__exit__(None, None, None)
        assert msg["type"] == "provider_lost"
        assert msg["reason"] == "silence_timeout"

    def test_loss_counted_in_stats(self, monkeypatch, tmp_path):
        monkeypatch.setattr("src.meeting.routes.REALTIME_KEEPALIVE_TICK_S", 0.05)
        monkeypatch.setattr("src.meeting.routes.REALTIME_STATS_INTERVAL_S", 0.1)
        meeting, provider = _setup(monkeypatch, tmp_path)
        client, ws = _open(meeting)
        try:
            provider.on_upstream_lost("upstream_restart")
            stats = _recv_until(
                ws, lambda m: m.get("type") == "stats" and m.get("upstream_losses", 0) >= 1
            )
        finally:
            ws.__exit__(None, None, None)
        assert stats["upstream_losses"] >= 1

    def test_abc_notify_lost_is_safe_when_unset(self):
        from src.meeting.transcription.base import RealtimeTranscriptionProvider

        class _Impl(RealtimeTranscriptionProvider):
            async def start(self, on_segment, hot_words=None, language_hints=None, translation_target=None):
                pass

            async def send_frame(self, audio_data):
                pass

            async def stop(self):
                pass

        impl = _Impl()
        impl._notify_lost("whatever")  # 未注册回调：不得抛错


# ── 静默看门狗（防 23s SDK 静默超时）────────────────────────────────


class TestSilenceKeepalive:
    def test_feeds_silence_when_client_idle(self, monkeypatch, tmp_path):
        monkeypatch.setattr("src.meeting.routes.REALTIME_KEEPALIVE_TICK_S", 0.05)
        monkeypatch.setattr("src.meeting.routes.REALTIME_KEEPALIVE_IDLE_S", 0.1)
        meeting, provider = _setup(monkeypatch, tmp_path)
        client, ws = _open(meeting)
        try:
            deadline = time.time() + 3.0
            while not provider.frames and time.time() < deadline:
                time.sleep(0.05)
        finally:
            ws.__exit__(None, None, None)
        assert provider.frames, "idle session should be fed silence"
        assert provider.frames[0] == b"\x00" * 16000

    def test_does_not_feed_while_client_streams(self, monkeypatch, tmp_path):
        # 客户端**持续**推流时不得插静音（否则静音会被当成说话内容送进云端）。
        # 推流时长必须跨过空闲阈值，才真正约束「只看空闲」这个语义。
        monkeypatch.setattr("src.meeting.routes.REALTIME_KEEPALIVE_TICK_S", 0.02)
        monkeypatch.setattr("src.meeting.routes.REALTIME_KEEPALIVE_IDLE_S", 0.5)
        monkeypatch.setattr("src.meeting.routes.REALTIME_STATS_INTERVAL_S", 0.05)
        meeting, provider = _setup(monkeypatch, tmp_path)
        client, ws = _open(meeting)
        try:
            deadline = time.time() + 1.2  # > 2× idle 阈值
            while time.time() < deadline:
                ws.send_bytes(b"\x01\x02")
                time.sleep(0.02)
            stats = _recv_until(
                ws, lambda m: m.get("type") == "stats" and m.get("frames_in", 0) >= 20
            )
        finally:
            ws.__exit__(None, None, None)
        assert stats["silence_injected"] == 0
        assert all(f != b"\x00" * 16000 for f in provider.frames)


# ── 适配器自愈 ──────────────────────────────────────────────────────


class _FakeRecognition:
    """模拟 SDK：_running False 时 send 抛错（23s 静默超时后的真实行为）。"""

    instances: list["_FakeRecognition"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._running = True
        self.sent: list[bytes] = []
        self.start_kwargs: dict = {}
        _FakeRecognition.instances.append(self)

    def start(self, **kwargs):
        self.start_kwargs = kwargs
        self._running = True

    def send_audio_frame(self, buf):
        if not self._running:
            raise RuntimeError("Speech recognition has stopped.")
        self.sent.append(buf)

    def stop(self):
        self._running = False


class TestAdapterSelfHeal:
    @pytest.fixture(autouse=True)
    def _clean(self):
        _FakeRecognition.instances = []
        yield
        _FakeRecognition.instances = []

    def _provider(self, monkeypatch):
        import src.meeting.transcription.dashscope_realtime as mod

        monkeypatch.setattr(mod, "Recognition", _FakeRecognition)
        provider = mod.DashScopeRealtimeTranscription(
            SimpleNamespace(api_key="k", model="fun-asr-realtime")
        )
        return mod, provider

    def test_send_restarts_dead_session_and_retries(self, monkeypatch):
        _mod, provider = self._provider(monkeypatch)
        lost: list[str] = []
        provider.on_upstream_lost = lost.append

        asyncio.run(provider.start(lambda *a: None))
        first = _FakeRecognition.instances[0]
        asyncio.run(provider.send_frame(b"a"))
        assert first.sent == [b"a"]

        # 模拟 23s 静默超时：SDK 自己退出 worker，_running 变 False
        first._running = False
        asyncio.run(provider.send_frame(b"b"))

        assert len(_FakeRecognition.instances) == 2, "dead session must be rebuilt"
        assert _FakeRecognition.instances[1].sent == [b"b"], "the frame must be retried"
        assert lost == ["upstream_restart"]

    def test_send_does_not_restart_on_healthy_session(self, monkeypatch):
        _mod, provider = self._provider(monkeypatch)
        lost: list[str] = []
        provider.on_upstream_lost = lost.append

        asyncio.run(provider.start(lambda *a: None))
        asyncio.run(provider.send_frame(b"a"))
        asyncio.run(provider.send_frame(b"b"))

        assert len(_FakeRecognition.instances) == 1
        assert _FakeRecognition.instances[0].sent == [b"a", b"b"]
        assert lost == []

    def test_restart_preserves_model_and_hotword_settings(self, monkeypatch):
        _mod, provider = self._provider(monkeypatch)
        asyncio.run(provider.start(lambda *a: None, language_hints=["zh"]))
        first = _FakeRecognition.instances[0]
        first._running = False
        asyncio.run(provider.send_frame(b"x"))

        rebuilt = _FakeRecognition.instances[1]
        assert rebuilt.kwargs.get("model") == "fun-asr-realtime"
        assert rebuilt.start_kwargs.get("language_hints") == ["zh"]

    def test_retry_failure_propagates(self, monkeypatch):
        # 重建后仍然发不出去 → 必须把异常抛给调用方（路由据此拆连接），不能吞
        _mod, provider = self._provider(monkeypatch)
        asyncio.run(provider.start(lambda *a: None))
        first = _FakeRecognition.instances[0]
        first._running = False

        original = _FakeRecognition.send_audio_frame

        def _always_fail(self, buf):
            self._running = False
            raise RuntimeError("Speech recognition has stopped.")

        monkeypatch.setattr(_FakeRecognition, "send_audio_frame", _always_fail)
        try:
            with pytest.raises(RuntimeError):
                asyncio.run(provider.send_frame(b"z"))
        finally:
            monkeypatch.setattr(_FakeRecognition, "send_audio_frame", original)
