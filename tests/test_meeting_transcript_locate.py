"""Batch locator LLM for transcript packs."""

from __future__ import annotations

import json


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def generate(self, prompt, **kwargs):
        self.calls.append(prompt)
        return json.dumps(self.payload)


def test_locate_packs_batches_ten_and_writes_context():
    from src.meeting.transcript_index import locate_packs

    packs = [{"sentences": [{"text": f"t{i}"}]} for i in range(12)]
    llm = _FakeLLM({"contexts": [{"id": 0, "context": "topic a"}]})
    locate_packs(packs, transcript="[1] [spk:0] hi", llm=llm, batch_size=10)
    assert len(llm.calls) == 2
    assert packs[0].get("context") == "topic a"
    # second batch id 0 maps to pack 10
    assert "<hot-words>" not in llm.calls[0]
    assert llm.calls[0].startswith("<transcript>")
