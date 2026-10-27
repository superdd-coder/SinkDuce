"""实时纪要的语言判定（2026-09-17 修）。

**用户报的 bug**：一场英文为主的会议，实时纪要整场输出韩文。

根因：``_language_note`` 原先用 ``any(...)`` —— 只要窗口里出现**一个**韩文字符
就把整场会议钉成韩文。ASR 输出夹带语气词（韩语 ``음``、中文 ``嗯``）极常见，
于是多语言会议必然误判（实测转写 37057 字符里仅 1 个汉字 → 判 Chinese）。

修法：**按比例判多数**，且设最小字符数门槛；都不达标时不声明语言（让模型跟随原文）。

跑法：``pytest tests/test_live_summary_language.py``
"""

from __future__ import annotations

import re

import pytest

from src.meeting.live_summary import _language_note


def lang_of(text: str) -> str | None:
    """判定出的语言名；未声明（跟随原文）→ None。"""
    m = re.search(r"language is (\w+)", _language_note([{"text": text}]))
    return m.group(1) if m else None


class TestSingleScriptMeetings:
    """单一语种会议必须判对（回归：新逻辑不能把这些弄坏）。"""

    def test_pure_chinese(self):
        assert lang_of("我们今天讨论一下项目进度和下一步计划，先看预算。") == "Chinese"

    def test_pure_korean(self):
        assert lang_of("오늘 회의를 시작하겠습니다. 잘 부탁드립니다.") == "Korean"

    def test_japanese_via_kana(self):
        """假名是日文独有 → 直接判日文（中文/韩文都不含假名）。"""
        assert lang_of("今日は会議を始めます。よろしくお願いします。") == "Japanese"

    def test_japanese_with_many_kanji(self):
        """日文里汉字多也要判日文（假名优先，不被汉字比例抢走）。"""
        assert lang_of("会議議題決定事項確認 これは日本語です") == "Japanese"

    def test_english(self):
        assert lang_of("Yes, hello, let's start the meeting.") is None


class TestMixedMeetingsDoNotMisLock:
    """**本次 bug 的正面回归**：语言夹带不能把整场钉错。"""

    def test_english_with_one_korean_filler(self):
        """一个韩语语气词 ``음`` 不得把英文会议判成韩文。"""
        assert lang_of("음 . oh, finally. hi, nice to see you again, boss.") is None

    def test_english_with_one_chinese_filler(self):
        """一个汉字 ``嗯`` 不得把英文会议判成中文。"""
        assert lang_of("Okay. 嗯 从 Mm-hmm. Okay, Amy.") is None

    def test_english_with_scattered_cjk_below_threshold(self):
        """零散 CJK 达不到门槛 → 不声明。"""
        assert lang_of("We discussed 项目 and then 预算 briefly.") is None

    def test_chinese_dominant_still_chinese(self):
        """汉字占 CJK 多数 → 判中文（真的中文会议不能被放过）。"""
        s = "我们先讨论项目进度 然后看预算 最后确认时间 ok let's go"
        assert lang_of(s) == "Chinese"

    def test_korean_dominant_still_korean(self):
        s = "오늘 회의 시작 합니다 그리고 예산을 봅시다 ok fine"
        assert lang_of(s) == "Korean"


class TestThresholds:
    def test_two_cjk_chars_below_min(self):
        """低于最小字符数（3）→ 不判定（防单个噪声词）。"""
        assert lang_of("음 어") is None

    def test_three_korean_chars_meets_min(self):
        assert lang_of("음 어 오") == "Korean"

    def test_one_chinese_char_never_enough(self):
        assert lang_of("hello 好 world") is None

    def test_empty_window(self):
        assert lang_of("") is None


class TestNoteShape:
    def test_note_instructs_the_language(self):
        note = _language_note([{"text": "我们今天开会讨论项目进度和预算安排"}])
        assert "Chinese" in note
        assert "never translate" not in note

    def test_undetermined_note_says_follow_transcript(self):
        note = _language_note([{"text": "just english here"}])
        assert "never translate" in note

    def test_multiple_segments_are_joined(self):
        """判定基于窗口内**全部**段落，不是只看第一段。"""
        win = [{"text": "hello there"}, {"text": "我们今天开会讨论项目进度和预算安排"}]
        assert "Chinese" in _language_note(win)
