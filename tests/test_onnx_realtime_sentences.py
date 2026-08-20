"""Local streaming ASR must not carry the previous sentence's last syllable."""

from __future__ import annotations

from pathlib import Path

import numpy as np


def test_onnx_realtime_flushes_and_clears_cache_on_silence():
    src = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "meeting"
        / "transcription"
        / "funasr_onnx_realtime.py"
    ).read_text(encoding="utf-8")
    assert "_flush_sentence" in src
    assert '"cache": {}' in src


def _bare_realtime(fake_model):
    from src.meeting.transcription.funasr_onnx_realtime import (
        FunASROnnxRealtimeTranscription,
    )

    rt = FunASROnnxRealtimeTranscription.__new__(FunASROnnxRealtimeTranscription)
    rt._model = fake_model
    rt._on_segment = None
    rt._param_dict = {"cache": {"keep": 1}, "is_final": False}
    rt._buffer = bytearray()
    rt._audio_pos_s = 0.0
    rt._lock = __import__("threading").Lock()
    rt._running = False
    rt._sentence_counter = 0
    rt._accumulated_text = ""
    rt._current_key = "local-1"
    rt._sentence_start_s = 0.0
    rt._silence_chunks = 0
    rt._last_text_end_s = 0.0
    rt._infer_lock = __import__("threading").Lock()
    return rt


def test_silence_final_resets_model_cache():
    from src.meeting.models import TranscriptSegment
    from src.meeting.transcription.funasr_onnx_realtime import _CHUNK_BYTES

    class Fake:
        def __init__(self):
            # 2 speech chunks + 2 silence chunks + 1 look-ahead flush + next utt
            self.seq = ["今", "天", "", "", "气", "很"]
            self.i = 0
            self.params: list[dict] = []

        def __call__(self, audio, param_dict=None):
            self.params.append(dict(param_dict or {}))
            text = self.seq[self.i] if self.i < len(self.seq) else ""
            self.i += 1
            return [{"preds": text}]

    fake = Fake()
    rt = _bare_realtime(fake)
    finals: list[TranscriptSegment] = []

    def on_seg(seg, is_final, _key):
        if is_final:
            finals.append(seg)

    import asyncio

    speech = (np.ones(_CHUNK_BYTES // 2, dtype=np.int16) * 1000).tobytes()
    silence = bytes(_CHUNK_BYTES)

    async def run():
        await rt.start(on_seg)
        await rt.send_frame(speech)
        await rt.send_frame(speech)
        await rt.send_frame(silence)
        await rt.send_frame(silence)
        assert finals
        # Look-ahead syllable is flushed into sentence 1, not the next utterance.
        assert finals[0].text == "今天气"
        assert rt._param_dict.get("cache") == {}
        await rt.send_frame(speech)

    asyncio.run(run())

    assert any(p.get("is_final") for p in fake.params)
    assert rt._accumulated_text == "很"
    assert not rt._accumulated_text.startswith("气")
