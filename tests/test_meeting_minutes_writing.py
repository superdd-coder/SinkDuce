"""Meeting writing contracts keep concise minutes grounded and parser-compatible."""
import re

from src import prompts


def test_meeting_minutes_share_concise_editorial_rules_without_conflicting_exhaustiveness():
    rules = prompts.MEETING_MINUTES_WRITING_RULES
    assert not re.search(r'[\u4e00-\u9fff]', rules)
    assert '2–3 pages' in rules and '650–1000' in rules
    assert 'commitments' in rules and 'open questions' in rules
    assert '```svg' in rules and 'viewBox' in rules
    for prompt in (prompts.MEETING_GENERAL_SUMMARY_PROMPT, prompts.MEETING_SUMMARIZER_V3_PROMPT):
        assert rules in prompt
        assert 'preserving ALL' not in prompt and 'keep it even if minor' not in prompt
        assert '## Todo' in prompt and '[spk:ID]' in prompt and '[ref:N]' in prompt
        assert '## Data & Facts' in prompt and '## Detail' in prompt
    general = prompts.MEETING_GENERAL_SUMMARY_PROMPT.format(transcript='[1] [spk:0] Approve the sample.', notes='', hot_words='')
    focused = prompts.MEETING_SUMMARIZER_V3_PROMPT.format(transcript='[1] [spk:0] Approve the sample.',
        hot_words='', other_sections='', section_name='Sample', section_description='Sample review', merged_sentences='[FOCUS] [1] [spk:0] Approve the sample.')
    assert '[1] [spk:0] Approve the sample.' in general and '[FOCUS]' in focused


def test_rich_minutes_finalization_keeps_diagram_table_and_task_ownership():
    from src.meeting.refs import finalize_summary_markdown
    from src.meeting.todo_candidates import parse_section_todo_candidates
    drawing = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 100"><text x="12" y="30">Review → Ship</text></svg>'
    raw = ('## Summary\nApprove the sample after a label check. [ref:1]\n\n'
           '## Data & Facts\n| Item | Result |\n| --- | --- |\n| Samples | 17 checked [ref:2] |\n\n'
           '## Todo\n- [spk:0] Check the labels — Confirm the result before shipping. [priority: high]\n\n'
           '## Detail\n### Release path\n\n```svg\n' + drawing + '\n```\n\nSource: [ref:1-2]')
    finalized = finalize_summary_markdown(raw, ['stt_0001', 'stt_0002'])
    assert drawing in finalized and '| Samples | 17 checked [stt_0002] |' in finalized
    tasks = parse_section_todo_candidates(finalized)
    assert len(tasks) == 1 and tasks[0]['assignee_label'] == '[spk:0]'
    assert tasks[0]['title'] == 'Check the labels' and tasks[0]['priority'] == 'high'
