import json
from types import SimpleNamespace

import pytest


def registry(session="session-a", records=()):
    from src.chatbox.citations import EvidenceRegistry
    return EvidenceRegistry(session, records)


def test_evidence_keeps_original_excerpt_and_available_location():
    evidence = registry().register("A supported statement. " * 30,
        {"file_id": "file-a", "version_id": "v1", "collection": "col-a",
         "source_label": "Reference.pdf", "page_number": 4, "char_offset": 123})
    assert len(evidence["quote"]) > 200
    assert evidence["title"] == "Reference.pdf"
    assert evidence["location"] == {"page_number": 4, "char_offset": 123}
    assert evidence["source"]["version_id"] == "v1"
    assert "paragraph_index" not in evidence["location"]


def test_identity_deduplicates_but_separates_versions_files_and_sessions():
    r = registry()
    a = r.register("A fact", {"file_id": "a", "version_id": "v1"})
    assert r.register("A fact", {"file_id": "a", "version_id": "v1"}) == a
    assert r.register("A fact", {"file_id": "b", "version_id": "v1"})["id"] != a["id"]
    assert r.register("A fact", {"file_id": "a", "version_id": "v2"})["id"] != a["id"]
    assert registry("session-b").register("A fact", {"file_id": "a", "version_id": "v1"})["id"] != a["id"]


def test_reloaded_registry_resolves_only_real_ids_in_first_appearance_order():
    r = registry()
    a = r.register("First fact", {"file_id": "a"})
    b = r.register("Second fact", {"file_id": "b"})
    restored = registry(records=r.records())
    answer = f"Second[cite:{b['id']}] first[cite:{a['id']}] again[cite:{b['id']}]"
    assert restored.used(answer) == [b, a]
    with pytest.raises(ValueError, match="Unknown citation"):
        restored.used("Unsupported[cite:invented]", strict=True)
    assert restored.used("Unsupported[cite:invented]") == []


def test_markers_in_code_are_not_treated_as_claim_citations():
    r = registry()
    assert r.used("`[cite:invented]`\n\n```text\n[cite:invented]\n```", strict=True) == []
    with pytest.raises(ValueError, match="Unknown citation"):
        r.used("Fact[cite:invalid identity]", strict=True)


def test_tool_capture_registers_only_returned_attachment_text():
    r = registry()
    result = r.capture("read_chat_attachment", json.dumps({
        "attachment_id": "attachment-a", "filename": "Brief.pdf", "start_line": 12,
        "content": "The visible excerpt.", "position_map": [{"char_offset": 0, "page_number": 1}]}))
    e = r.records()[0]
    assert e["kind"] == "attachment"
    assert e["quote"] == "The visible excerpt."
    assert e["location"]["line"] == 12
    assert "page_number" not in e["location"]  # a global position map is not this excerpt's location
    assert e["id"] in result
    before = r.records()
    r.capture("read_chat_attachment", '{"error":"not allowed"}')
    assert r.records() == before


def test_multimodal_capture_retains_image_bytes_and_labels_page_evidence():
    r = registry()
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,eA=="}}
    result = r.capture("read_chat_attachment", [{"type": "text", "text": json.dumps({
        "attachment_id": "a", "filename": "Scan.pdf", "content": "",
        "attached_image_indices": [2], "images": [{"image_index": 2, "page_number": 3}]})}, image])
    assert result[1] == image
    evidence = r.records()[0]
    assert evidence["kind"] == "image"
    assert evidence["quote"] == ""
    assert evidence["location"]["page_number"] == 3


def test_capture_keeps_meeting_identity_when_sentence_numbers_overlap():
    r = registry()
    for mid in ("meeting-a", "meeting-b"):
        r.capture("lookup_meeting_transcript", json.dumps({"meeting_id": mid,
            "context": "[ref:1] A spoken fact.", "cites": [{"meeting_id": mid,
            "id": "1", "text": "A spoken fact.", "start_time": 12.5, "end_time": 15.0}]}))
    records = r.records()
    assert len(records) == 2
    assert records[0]["id"] != records[1]["id"]
    assert records[0]["location"]["start_time"] == 12.5


def test_store_history_recovers_citations_without_accepting_malformed_metadata():
    from src.chatbox.citations import registry_from_history
    r = registry()
    e = r.register("A prior fact", {"file_id": "a"})
    store = SimpleNamespace(get_messages=lambda sid, **kwargs: [
        SimpleNamespace(metadata={"citations": [e]}), SimpleNamespace(metadata={"citations": [None, {"id": "fake"}]})])
    restored = registry_from_history(store, "session-a")
    assert restored.used(f"Answer[cite:{e['id']}]") == [e]


def test_annotation_keeps_attachment_json_valid_for_history_image_hydration():
    r = registry()
    annotated = r.capture("read_chat_attachment", '{"attachment_id":"a","filename":"scan.pdf","content":"Original"}')
    assert json.loads(annotated)["content"] == "Original"
    assert json.loads(annotated)["evidence"][0]["id"] == r.records()[0]["id"]


def test_corrupt_duplicate_history_cannot_remove_an_already_valid_reference():
    r = registry()
    e = r.register("A fact", {"file_id": "a"})
    restored = registry(records=[e, {**e, "id": "fake"}])
    assert restored.used(f"Fact[cite:{e['id']}]") == [e]


def test_real_single_meeting_payload_resolves_returned_sentences_and_times(monkeypatch):
    from src.meeting import transcript_index
    monkeypatch.setattr(transcript_index, "load_sentences_for_meeting", lambda mid: [
        {"sentence_id": "spoken-a", "original_text": "A returned fact.", "start_time": 18, "end_time": 21},
        {"sentence_id": "spoken-b", "original_text": "Not returned.", "start_time": 22, "end_time": 24}])
    r = registry()
    r.capture("lookup_meeting_transcript", json.dumps({"meeting_id": "meeting-a", "context": "[ref:1] speaker: A returned fact."}))
    assert len(r.records()) == 1
    assert r.records()[0]["quote"] == "A returned fact."
    assert r.records()[0]["location"]["sentence_id"] == "spoken-a"
    assert r.records()[0]["location"]["start_time"] == 18


def test_group_meeting_context_does_not_mix_overlapping_sentence_numbers(monkeypatch):
    from src.meeting import transcript_index
    monkeypatch.setattr(transcript_index, "load_sentences_for_meeting", lambda mid: [{
        "sentence_id": "spoken-" + mid, "original_text": "Alpha fact." if mid == "a" else "Beta fact.", "start_time": 10 if mid == "a" else 20}])
    r = registry()
    r.capture("lookup_meeting_transcript", json.dumps({"context": "## Meeting: A\nn: 1\nid: a\n\n[ref:1] speaker: Alpha fact.\n\n## Meeting: B\nn: 2\nid: b\n\n[ref:1] speaker: Beta fact.",
        "cites": [{"meeting_id": "a", "n": 1, "ref_n": 1, "sentence_id": "spoken-a"}, {"meeting_id": "b", "n": 2, "ref_n": 1, "sentence_id": "spoken-b"}]}))
    records = {e["source"]["meeting_id"]: e for e in r.records()}
    assert records["a"]["quote"] == "Alpha fact."
    assert records["b"]["quote"] == "Beta fact."
    assert records["b"]["location"]["start_time"] == 20


def test_long_source_and_attachment_reads_register_locatable_smaller_excerpts():
    r = registry()
    text = "First paragraph. " * 80 + "\n\n" + "Second paragraph. " * 80 + "\n\n" + "Third paragraph. " * 80
    r.capture("lookup_collection", text, sources=[{"text": text, "metadata": {"file_id": "a", "char_offset": 100}}])
    records = r.records()
    assert len(records) >= 3
    assert all(len(e["quote"]) <= 1600 for e in records)
    for e in records:
        relative = e["location"]["char_offset"] - 100
        assert text[relative:relative + len(e["quote"])] == e["quote"]
    a = registry()
    a.capture("read_chat_attachment", json.dumps({"attachment_id": "a", "filename": "Long.txt", "content": text, "start_line": 40}))
    assert len(a.records()) >= 3
    assert a.records()[-1]["location"]["line"] >= 42


def test_attachment_reader_provides_the_actual_excerpt_page_and_offset(tmp_path):
    from src.chat_attachments.store import AttachmentStore
    from src.chat_attachments.service import execute_attachment_tool
    files = AttachmentStore(tmp_path / "files")
    a = files.create("session-a", "Pages.txt", b"First.\n\nSecond.")
    files.finish("session-a", a.id, content="First.\n\nSecond.", native=True, images=[], parser="local",
        position_map=[{"char_offset": 0, "page_number": 1}, {"char_offset": 8, "page_number": 2}])
    files.bind("session-a", [a.id])
    payload = execute_attachment_tool(files, "session-a", "read_chat_attachment", {"attachment_id": a.id, "start_line": 3})
    r = registry()
    r.capture("read_chat_attachment", payload)
    assert r.records()[0]["quote"] == "Second."
    assert r.records()[0]["location"]["page_number"] == 2
    assert r.records()[0]["location"]["char_offset"] == 8


def test_attachment_excerpts_stop_at_real_page_boundaries_and_keep_each_page():
    r=registry()
    first='First page details. '*30
    second='Second page structure. '*30
    r.capture('read_chat_attachment',json.dumps({'attachment_id':'a','filename':'Source.pdf','start_line':1,'content':first+second,'char_offset':0,
        'position_map':[{'char_offset':0,'page_number':5},{'char_offset':len(first),'page_number':6}]}))
    assert [(e['location'].get('page_number'),e['quote']) for e in r.records()]==[(5,first),(6,second)]


def test_live_evidence_is_bounded_but_history_ids_can_still_resolve():
    r = registry()
    first = None
    for index in range(300):
        e = r.register(f"Fact {index}", {"file_id": str(index)})
        first = first or e
    assert len(r.live_records()) <= 256
    assert r.used(f"Earlier[cite:{first['id']}]") == [first]
    assert len(registry(records=r.records()).live_records()) <= 32


def test_note_reads_keep_the_canonical_source_for_opening_the_original():
    r = registry()
    r.capture("get_note", json.dumps({"id": "note-a", "collection": "col-a", "title": "Research", "content": "A saved observation."}),
        args={"collection": "col-a", "note_id": "note-a"})
    evidence = r.records()[0]
    assert evidence["source"]["source"] == "__note__:note-a"
    assert evidence["quote"] == "A saved observation."
