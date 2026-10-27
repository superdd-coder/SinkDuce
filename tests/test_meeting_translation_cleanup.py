"""译文与 summary 的生命周期（2026-09-17）。

**用户报的 bug**：重新转录后生成了新的 Summary，但**旧的译文没被清掉**。

**根因**：译文存在独立文件 `{tab_id}_{LANG}.md`，而"清派生数据"的
`delete_pipeline_data()` 只删了 `sentences.json` 与各 `{tab_id}.md`，**没碰译文**。
于是重新转录/重新生成之后，用户在语言下拉里切到任一语言，看到的还是**上一版
summary** 的翻译（绿点亮着，内容对不上）。

**顺带修的三条同类路径**：单节删除、单节重新生成、手改正文——它们都覆盖/移除了
一个 summary，译文同样会变孤儿。

⚠️ **绝不能按前缀通配删**：同一目录住着 `notes.md`（用户手写的会议笔记，不可再生）
和 `note_distillation_tab_general.md`。所以全部走
`TRANSLATION_LANG_NAMES` 白名单逐个精确匹配。

跑法：``pytest tests/test_meeting_translation_cleanup.py``
"""

from __future__ import annotations

import pytest

from src.meeting import store


@pytest.fixture
def meet(tmp_path, monkeypatch):
    root = tmp_path / "meetings"
    mid = "m_trans"
    (root / mid).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "MEETINGS_DIR", root)
    return mid


def _write(mid_dir, name: str, text: str = "x"):
    p = mid_dir / name
    p.write_text(text, encoding="utf-8")
    return p


class TestDeleteTranslationMds:
    def test_deletes_only_that_section(self, meet, tmp_path):
        d = tmp_path / "meetings" / meet
        _write(d, "tab_01_CN.md")
        _write(d, "tab_01_EN.md")
        _write(d, "tab_01.md")        # 原文：不能删
        _write(d, "tab_02_CN.md")     # 别的小节：不能删
        n = store.delete_translation_mds(meet, "tab_01")
        assert n == 2
        left = sorted(p.name for p in d.iterdir())
        assert left == ["tab_01.md", "tab_02_CN.md"]

    def test_no_translations_is_noop(self, meet, tmp_path):
        d = tmp_path / "meetings" / meet
        _write(d, "tab_01.md")
        assert store.delete_translation_mds(meet, "tab_01") == 0
        assert (d / "tab_01.md").exists()

    def test_never_touches_notes_or_distillation(self, meet, tmp_path):
        """**最重要的防线**：用户笔记不可再生，任何通配都可能误伤它。"""
        d = tmp_path / "meetings" / meet
        _write(d, "notes.md", "# 我的手写笔记")
        _write(d, "note_distillation_tab_general.md", "distilled")
        _write(d, "tab_general.md", "summary")
        _write(d, "tab_general_CN.md", "翻译")
        store.delete_all_translation_mds(meet)
        assert (d / "notes.md").read_text() == "# 我的手写笔记"
        assert (d / "note_distillation_tab_general.md").exists()
        assert (d / "tab_general.md").exists()
        assert not (d / "tab_general_CN.md").exists()


class TestDeleteAllTranslationMds:
    def test_covers_sections_no_longer_in_tabs(self, meet, tmp_path):
        """重新转录会重建 tabs —— 即将消失的小节，其译文也必须清掉。

        这正是"按会议整体清"而不是"遍历 tabs"的原因：遍历会漏掉那个要消失的。
        """
        d = tmp_path / "meetings" / meet
        _write(d, "tab_general_CN.md")
        _write(d, "tab_99_KO.md")   # 一个已不在 tabs 里的小节
        _write(d, "tab_01_JA.md")
        assert store.delete_all_translation_mds(meet) == 3
        assert [p.name for p in d.iterdir()] == []

    def test_all_supported_langs(self, meet, tmp_path):
        from src.prompts import TRANSLATION_LANG_NAMES

        d = tmp_path / "meetings" / meet
        for lang in TRANSLATION_LANG_NAMES:
            _write(d, f"tab_general_{lang}.md")
        assert store.delete_all_translation_mds(meet) == len(TRANSLATION_LANG_NAMES)


class TestDeletePipelineDataCleansTranslations:
    def test_retranscribe_path_removes_stale_translations(self, meet, tmp_path, monkeypatch):
        """**用户报的那条路**：delete_pipeline_data 必须把译文一起清掉。"""
        d = tmp_path / "meetings" / meet
        _write(d, "tab_general.md")
        _write(d, "tab_general_CN.md")
        _write(d, "tab_general_EN.md")
        _write(d, "tab_01.md")
        _write(d, "tab_01_JA.md")
        _write(d, "notes.md", "keep me")

        # 让 get_meeting 返回带 tabs 的会议（走真实分支）
        monkeypatch.setattr(store, "get_meeting", lambda mid: type("M", (), {
            "tabs": [{"tab_id": "tab_general"}, {"tab_id": "tab_01"}],
        })())
        monkeypatch.setattr(
            "src.meeting.transcript_index.purge_meeting_transcripts",
            lambda mid: None,
        )

        store.delete_pipeline_data(meet)

        left = sorted(p.name for p in d.iterdir())
        assert left == ["notes.md"], f"应只剩用户笔记，实际 {left}"

    def test_cleanup_failure_does_not_break_pipeline(self, meet, monkeypatch):
        """清理译文失败不该让整个重新转录挂掉（它是收尾，不是主流程）。"""
        monkeypatch.setattr(
            store, "delete_all_translation_mds",
            lambda mid: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr(store, "get_meeting", lambda mid: None)
        monkeypatch.setattr(
            "src.meeting.transcript_index.purge_meeting_transcripts",
            lambda mid: None,
        )
        store.delete_pipeline_data(meet)  # 不抛


class TestSectionMdWriteClearsTranslations:
    """**用户报的那条路**：重新生成 Summary 之后，旧译文还在。

    上一版修复只在「重新转录 / 删除小节 / 手改正文」上清译文，**漏了「重新生成
    Summary」**——那条路不经过 `delete_pipeline_data`，而是直接重写小节正文
    （`generation.py` 里 `store.save_section_md(meeting_id, "tab_general", general_md)`）。

    所以收口放到 `save_section_md` —— 它是"写小节正文"的**唯一漏斗**：
    重新生成总览 / 重新生成小节 / 重新转录 / 手改正文全走它。
    """

    def _write(self, d, name, text):
        p = d / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_rewriting_summary_clears_its_translations(self, meet, tmp_path):
        d = tmp_path / "meetings" / meet
        self._write(d, "tab_general.md", "# 旧的总览")
        self._write(d, "tab_general_CN.md", "# 旧译文")
        self._write(d, "tab_general_EN.md", "# old translation")

        store.save_section_md(meet, "tab_general", "# 新的总览")

        assert (d / "tab_general.md").read_text(encoding="utf-8") == "# 新的总览"
        assert not (d / "tab_general_CN.md").exists(), "旧中文译文应当被清掉"
        assert not (d / "tab_general_EN.md").exists(), "旧英文译文应当被清掉"

    def test_identical_content_keeps_translations(self, meet, tmp_path):
        """内容没变就别清（重跑出完全相同正文时保留译文，避免无谓损失）。"""
        d = tmp_path / "meetings" / meet
        same = "# 一模一样"
        self._write(d, "tab_general.md", same)
        self._write(d, "tab_general_CN.md", "# 译文")

        store.save_section_md(meet, "tab_general", same)

        assert (d / "tab_general_CN.md").exists(), "内容未变时不该清译文"

    def test_first_write_has_nothing_to_clear(self, meet, tmp_path):
        """首次写入（原本没有正文）不该报错。"""
        d = tmp_path / "meetings" / meet
        store.save_section_md(meet, "tab_general", "# 首次")
        assert (d / "tab_general.md").exists()

    def test_other_sections_translations_untouched(self, meet, tmp_path):
        d = tmp_path / "meetings" / meet
        self._write(d, "tab_general.md", "old")
        self._write(d, "tab_01_CN.md", "另一节的译文")
        self._write(d, "tab_general_CN.md", "这一节的译文")

        store.save_section_md(meet, "tab_general", "new")

        assert (d / "tab_01_CN.md").exists(), "别的小节的译文不该被动"
        assert not (d / "tab_general_CN.md").exists()

    def test_user_notes_survive(self, meet, tmp_path):
        """再次强调：`notes.md` 是用户手写笔记，任何清理都不能碰它。"""
        d = tmp_path / "meetings" / meet
        self._write(d, "tab_general.md", "old")
        self._write(d, "notes.md", "# 我的手写笔记")
        self._write(d, "note_distillation_tab_general.md", "distilled")

        store.save_section_md(meet, "tab_general", "new")

        assert (d / "notes.md").read_text(encoding="utf-8") == "# 我的手写笔记"
        assert (d / "note_distillation_tab_general.md").exists()

    def test_cleanup_failure_does_not_block_the_write(self, meet, tmp_path, monkeypatch):
        """清理炸了也要把正文写下去（正文是主，译文是派生）。"""
        d = tmp_path / "meetings" / meet
        self._write(d, "tab_general.md", "old")
        monkeypatch.setattr(
            store, "delete_translation_mds",
            lambda mid, tid: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        store.save_section_md(meet, "tab_general", "new")
        assert (d / "tab_general.md").read_text(encoding="utf-8") == "new"
