"""Active ASR model caps how many language_hints the Meeting UI may send."""

from __future__ import annotations


def test_dashscope_realtime_fun_asr_is_single_hint():
    from src.meeting.transcription.dashscope_realtime import (
        DashScopeRealtimeTranscription,
        MODEL_FUN_ASR_REALTIME,
        MODEL_QWEN_REALTIME,
    )
    from src.meeting.transcription.base import language_hint_limit

    assert language_hint_limit(DashScopeRealtimeTranscription, MODEL_FUN_ASR_REALTIME) == 1
    assert language_hint_limit(DashScopeRealtimeTranscription, MODEL_QWEN_REALTIME) == 4
    assert language_hint_limit(DashScopeRealtimeTranscription, None) == 1


def test_dashscope_file_fun_asr_is_single_hint():
    from src.meeting.transcription.dashscope_file import (
        DashScopeFileTranscription,
        MODEL_FUN_ASR,
        MODEL_QWEN_FILETRANS,
    )
    from src.meeting.transcription.base import language_hint_limit

    assert language_hint_limit(DashScopeFileTranscription, MODEL_FUN_ASR) == 1
    assert language_hint_limit(DashScopeFileTranscription, MODEL_QWEN_FILETRANS) == 4
    assert language_hint_limit(DashScopeFileTranscription, None) == 1


def test_local_and_openai_adapters_are_single_hint():
    from src.meeting.transcription.base import language_hint_limit
    from src.meeting.transcription.funasr_onnx_file import FunASROnnxFileTranscription
    from src.meeting.transcription.funasr_onnx_realtime import FunASROnnxRealtimeTranscription
    from src.meeting.transcription.openai_compat_file import OpenAICompatFileTranscription
    from src.meeting.transcription.openrouter_file import OpenRouterFileTranscription

    assert language_hint_limit(FunASROnnxFileTranscription, None) == 1
    assert language_hint_limit(FunASROnnxRealtimeTranscription, None) == 1
    assert language_hint_limit(OpenAICompatFileTranscription, "whisper-1") == 1
    assert language_hint_limit(OpenRouterFileTranscription, None) == 1


def _codes(cls) -> set[str]:
    return {h["code"] for h in cls.SUPPORTED_LANGUAGE_HINTS}


def test_dashscope_language_menus_include_sea_codes():
    from src.meeting.transcription.dashscope_file import DashScopeFileTranscription
    from src.meeting.transcription.dashscope_realtime import DashScopeRealtimeTranscription

    needed = {"vi", "th", "id", "ms"}
    assert needed <= _codes(DashScopeRealtimeTranscription)
    assert needed <= _codes(DashScopeFileTranscription)


def test_whisper_language_menus_include_sea_codes():
    from src.meeting.transcription.openai_compat_file import OpenAICompatFileTranscription
    from src.meeting.transcription.openrouter_file import OpenRouterFileTranscription

    needed = {"vi", "th", "id", "ms"}
    assert needed <= _codes(OpenAICompatFileTranscription)
    assert needed <= _codes(OpenRouterFileTranscription)


def test_local_onnx_language_menus_match_model():
    from src.meeting.transcription.funasr_onnx_file import FunASROnnxFileTranscription
    from src.meeting.transcription.funasr_onnx_realtime import FunASROnnxRealtimeTranscription

    # SenseVoice: zh/en/ja/ko/yue. Not SEA languages.
    assert {"auto", "zh", "en", "ja", "ko", "yue"} <= _codes(FunASROnnxFileTranscription)
    assert not {"vi", "th", "id", "ms"} <= _codes(FunASROnnxFileTranscription)
    # Local realtime is Paraformer-zh-streaming — zh/en only.
    assert _codes(FunASROnnxRealtimeTranscription) == {"auto", "zh", "en"}


def test_clip_language_hints_drops_auto_and_honors_max():
    from src.meeting.transcription.base import clip_language_hints

    assert clip_language_hints(["auto", "zh", "en"], max_hints=1) == ["zh"]
    assert clip_language_hints(["zh", "en", "ja", "ko", "fr"], max_hints=4) == [
        "zh",
        "en",
        "ja",
        "ko",
    ]
    assert clip_language_hints(["auto"], max_hints=1) is None
    assert clip_language_hints(None, max_hints=4) is None
