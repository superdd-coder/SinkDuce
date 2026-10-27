"""译文数字保真（2026-09-17）。

**背景**：会议纪要翻译曾把「N million」译成「N×10 万」——**少一个数量级**，
而同一份译文里其它换算都对。根因是翻译 prompt 原先没有任何数字保真规则。

**两条纪律**（本文件与实现都遵守）：
1. 按 AGENTS.md，prompt 与代码里**不写真实业务数据**；示例一律用通用占位数字
   （与仓库既有 `Project X` / `5 million USD` 惯例一致）。
2. 数字保真不能只靠 prompt —— prompt 是软约束，财务数字需要代码层校验 + 重试。

跑法：``pytest tests/test_number_fidelity.py``
"""

from __future__ import annotations

from src.meeting.number_fidelity import (
    extract_quantities,
    format_hint,
    missing_quantities,
)


class TestExtraction:
    def test_english_million(self):
        assert extract_quantities("USD 5 million") == [5_000_000]

    def test_chinese_wan(self):
        assert extract_quantities("约为500万") == [5_000_000]

    def test_chinese_wan_ten_times_less(self):
        """少一个数量级的写法 —— 数值不同，必须能区分。"""
        assert extract_quantities("约为50万") == [500_000]

    def test_fractional_numeral(self):
        assert extract_quantities("0.5 million") == [500_000]

    def test_billion_and_thousand(self):
        assert extract_quantities("3 billion") == [3_000_000_000]
        assert extract_quantities("250 thousand") == [250_000]

    def test_short_suffix_forms(self):
        assert extract_quantities("750M") == [750_000_000]
        assert extract_quantities("120K") == [120_000]

    def test_chinese_yi(self):
        assert extract_quantities("约2亿") == [200_000_000]

    def test_thousands_separator(self):
        assert extract_quantities("5,000,000") == [5_000_000]

    def test_small_numbers_are_ignored(self):
        """百分比/小数比率不参与（避免噪声误报）。"""
        assert extract_quantities("36% equity") == []
        assert extract_quantities("1.5x") == []
        assert extract_quantities("version 3.2.1") == []

    def test_multiple_quantities_sorted(self):
        got = extract_quantities("3 million and 5 million and 2.5 million")
        assert got == [2_500_000, 3_000_000, 5_000_000]

    def test_no_numbers(self):
        assert extract_quantities("no figures here") == []


class TestFidelity:
    def test_tenfold_error_is_caught(self):
        """核心回归：数量级错（N million → N×10 万）必须检出。"""
        src = "The book value is approximately USD 5 million"
        bad = "账面价值约为50万"
        assert missing_quantities(src, bad) == [5_000_000]

    def test_correct_translation_passes(self):
        src = "The book value is approximately USD 5 million"
        good = "账面价值约为500万"
        assert missing_quantities(src, good) == []

    def test_partial_error_points_at_the_wrong_one(self):
        """三个数里错一个 —— 只报错的那个。"""
        src = "USD 3 million, USD 2.5 million and USD 5 million"
        got = "300万、250万及50万"  # 最后一个错
        assert missing_quantities(src, got) == [5_000_000]

    def test_order_does_not_matter(self):
        src = "USD 3 million then USD 5 million"
        got = "500万，然后300万"
        assert missing_quantities(src, got) == []

    def test_extra_numbers_in_translation_are_tolerated(self):
        """译文多出的数字不报（可能来自标点/编号的合理差异）。"""
        assert missing_quantities("USD 5 million", "约500万（第 3 项）") == []

    def test_currency_and_approximation_do_not_matter(self):
        src = "approximately USD 5 million"
        assert missing_quantities(src, "约500万美元") == []


class TestHint:
    def test_hint_prefers_chinese_units(self):
        assert "500万" in format_hint([5_000_000])
        assert "2亿" in format_hint([200_000_000])

    def test_hint_falls_back_to_grouped(self):
        assert "1,234" in format_hint([1234])


class TestPromptRule:
    """prompt 必须写明规则，且示例是**通用占位数字**（不含真实业务数据）。"""

    def test_prompt_states_number_rule(self):
        from src.prompts import MEETING_TRANSLATION_SYSTEM

        assert "Numbers must keep their exact value" in MEETING_TRANSLATION_SYSTEM

    def test_prompt_examples_are_generic(self):
        from src.prompts import MEETING_TRANSLATION_SYSTEM

        # 通用示例（与仓库既有 "5 million USD" 惯例一致）
        assert "5 million" in MEETING_TRANSLATION_SYSTEM
        assert "500万" in MEETING_TRANSLATION_SYSTEM
        assert "0.5 million" in MEETING_TRANSLATION_SYSTEM
        assert "50万" in MEETING_TRANSLATION_SYSTEM


class TestRetryWiring:
    """重试链路必须真的接上（不只是校验函数能跑）。"""

    def _run(self, first: str, second: str):
        import threading as _th
        from unittest.mock import patch

        from src.meeting import service as svc_mod
        from src.meeting import translation as tr

        src_md = "The book value is approximately USD 5 million"

        class FakeLLM:
            def __init__(self):
                self.calls = 0
                self.retry_saw_hint = False

            def generate_stream_tagged(self, prompt, **kw):
                self.calls += 1
                if self.calls == 1:
                    yield (first, False)
                else:
                    self.retry_saw_hint = "500万" in prompt
                    yield (second, False)

        llm = FakeLLM()
        svc = svc_mod.MeetingService.__new__(svc_mod.MeetingService)
        svc._translation_stream_lock = _th.Lock()
        svc._active_translation_streams = {}
        st = tr._TranslationStream("m1", "tab_general", "CN")

        class SyncThread:
            def __init__(self, target=None, **kw):
                self._t = target

            def start(self):
                self._t()

        with (
            patch.object(svc_mod, "_resolve_meeting_llm", lambda ref=None: llm),
            patch.object(svc_mod.store, "save_translation_md", lambda *a, **k: None),
            patch.object(tr.threading, "Thread", SyncThread),
        ):
            svc._start_translation_thread(st, src_md)
        return llm, st

    def test_wrong_number_triggers_retry_and_gets_fixed(self):
        llm, st = self._run("账面价值约为50万", "账面价值约为500万")
        assert llm.calls == 2, "数字不一致必须触发一次重试"
        assert llm.retry_saw_hint, "重试 prompt 必须指出缺失的数量（否则模型无从修正）"
        assert "500万" in st.final

    def test_correct_number_does_not_retry(self):
        llm, st = self._run("账面价值约为500万", "（不该被调用）")
        assert llm.calls == 1, "数字正确时不得重试（省一次 LLM 调用）"
        assert "500万" in st.final

    def test_persistent_mismatch_retries_only_once(self):
        """重试仍不对时保留重试稿，且**只重试一次**（不无限打转）。"""
        llm, st = self._run("账面价值约为50万", "账面价值约为5万")
        assert llm.calls == 2, "只重试一次"
        assert st.final and "5万" in st.final
