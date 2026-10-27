"""Realtime local ASR per-session isolation (2026-09-19 并发审查 P0).

背景：本地 FunASR realtime provider 曾经被进程级缓存（cached_provider），
两台设备同时录制不同会议时，第二个 ``start()`` 直接重置第一个会话的识别
状态（串台/被 stop）。修复后：模型对象按 model_dir 全进程共享，会话状态
全部在实例上；本文件把这两条钉死。
"""

from __future__ import annotations

import sys
import types

import pytest

import src.meeting.transcription.funasr_onnx_realtime as mod
from src.config import TranscriptionProviderConfig


class _FakeParaformer:
    """构造计数共享、实例可身份比较的假 ONNX 模型。"""

    instances: list["_FakeParaformer"] = []

    def __init__(self, model_dir, **kwargs):
        self.model_dir = model_dir
        self.kwargs = kwargs
        _FakeParaformer.instances.append(self)


@pytest.fixture(autouse=True)
def _fake_env(monkeypatch, tmp_path):
    """注入假模型模块与假模型目录；清空共享缓存。"""
    monkeypatch.setattr(mod, "ensure_onnx_model_dir", lambda *a, **k: tmp_path / "m")
    monkeypatch.setattr(
        "src.meeting.transcription.onnx.threads.configure_host_math_threads",
        lambda: None,
    )
    monkeypatch.setattr(
        "src.meeting.transcription.onnx.threads.realtime_asr_threads", lambda: 2
    )
    fake_pkg = types.ModuleType("funasr_onnx")
    fake_bin = types.ModuleType("funasr_onnx.paraformer_online_bin")
    fake_bin.Paraformer = _FakeParaformer
    fake_pkg.paraformer_online_bin = fake_bin
    monkeypatch.setitem(sys.modules, "funasr_onnx", fake_pkg)
    monkeypatch.setitem(sys.modules, "funasr_onnx.paraformer_online_bin", fake_bin)
    mod._shared_models.clear()
    _FakeParaformer.instances.clear()
    yield
    mod._shared_models.clear()


def _make() -> mod.FunASROnnxRealtimeTranscription:
    return mod.FunASROnnxRealtimeTranscription(
        TranscriptionProviderConfig(id="p1", adapter="funasr_onnx_realtime")
    )


def test_two_sessions_share_one_model_object():
    s1, s2 = _make(), _make()
    assert s1._model is s2._model
    assert len(_FakeParaformer.instances) == 1


def test_sessions_share_infer_lock_but_not_session_state():
    s1, s2 = _make(), _make()
    # 推理锁必须共享：一个 ONNX 会话不是线程安全的
    assert s1._infer_lock is s2._infer_lock
    # 会话状态必须私有：这是「两台设备同时录制互相踩碎」的直接防线
    assert s1._param_dict is not s2._param_dict
    assert s1._buffer is not s2._buffer


def test_start_reset_on_one_session_does_not_touch_the_other():
    import asyncio

    async def scenario():
        s1, s2 = _make(), _make()
        await s1.start(on_segment=lambda *a: None)
        s1._buffer.extend(b"\x01\x02\x03\x04")
        s1._accumulated_text = "第一台设备的半句话"
        # 第二台 start：修复前会把 s1 的 buffer/文本一并清掉（provider 单例）
        await s2.start(on_segment=lambda *a: None)
        assert s1._accumulated_text == "第一台设备的半句话"
        assert len(s1._buffer) == 4
        # 第二台的 stop 不能杀掉第一台的会话（修复前 _running=False 全局生效）
        await s2.stop()
        assert s1._running is True

    asyncio.run(scenario())


def test_service_builds_fresh_realtime_session_per_call(monkeypatch):
    """get_active_realtime_provider 本地分支不再缓存 provider 实例。"""
    from src.meeting.service import meeting_service

    cfg = TranscriptionProviderConfig(
        id="p1", adapter="funasr_onnx_realtime", is_active=True
    )
    monkeypatch.setattr(
        "src.config.get_config",
        lambda: type("C", (), {"transcription": type("T", (), {
            "active_realtime_provider": cfg,
            "get_local_realtime_provider": lambda self: cfg,
        })()})(),
    )
    built = []
    monkeypatch.setattr(
        mod, "ensure_onnx_model_dir", lambda *a, **k: built and built[0] or __import__("pathlib").Path("/tmp/x")
    )
    p1 = meeting_service.get_active_realtime_provider()
    p2 = meeting_service.get_active_realtime_provider()
    assert p1 is not p2, "provider 实例必须每连接新建（模型共享在更底层完成）"
