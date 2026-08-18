from types import SimpleNamespace

from src.rag.contextual import (
    provider_model_is_visual,
    resolve_agentic_query_target,
    resolve_enrichment_target,
    resolve_ingest_vision,
    resolve_named_slot,
    resolve_note_distill_target,
)


def _provider(**kwargs):
    data = {
        "id": "p1",
        "is_default": False,
        "default_model": "chat-a",
        "model": "chat-a",
        "visual_model_ids": [],
        "function_call_model_ids": [],
    }
    data.update(kwargs)
    return SimpleNamespace(**data)


def _cfg(providers, **enrich):
    enrichment = SimpleNamespace(
        enrichment_model=enrich.get("enrichment_model", ""),
        meeting_model=enrich.get("meeting_model", ""),
        agentic_query_model=enrich.get("agentic_query_model", ""),
        note_distill_model=enrich.get("note_distill_model", ""),
    )
    return SimpleNamespace(
        llm=SimpleNamespace(providers=providers),
        enrichment=enrichment,
        visual_model_id=enrich.get("visual_model_id", ""),
    )


def test_named_slot_falls_back_to_default_card(monkeypatch):
    default = _provider(id="def", is_default=True, default_model="base")
    other = _provider(id="other", default_model="alt")
    monkeypatch.setattr("src.config.get_config", lambda: _cfg([default, other]))
    p, model = resolve_named_slot("")
    assert p.id == "def"
    assert model is None


def test_named_slot_uses_provider_and_model(monkeypatch):
    default = _provider(id="def", is_default=True)
    other = _provider(id="other", default_model="alt")
    monkeypatch.setattr("src.config.get_config", lambda: _cfg([default, other]))
    p, model = resolve_named_slot("other|special")
    assert p.id == "other"
    assert model == "special"


def test_library_llm_collection_override_wins(monkeypatch):
    default = _provider(id="def", is_default=True)
    lib = _provider(id="lib")
    monkeypatch.setattr(
        "src.config.get_config",
        lambda: _cfg([default, lib], enrichment_model="def|global-sum"),
    )
    p, model = resolve_enrichment_target(
        {"enriching_llm_provider": "lib", "enriching_llm_model": "col-sum"}
    )
    assert p.id == "lib"
    assert model == "col-sum"


def test_agentic_query_does_not_require_tools(monkeypatch):
    default = _provider(id="def", is_default=True, function_call_model_ids=[])
    aq = _provider(id="aq", function_call_model_ids=[])
    monkeypatch.setattr(
        "src.config.get_config",
        lambda: _cfg([default, aq], agentic_query_model="aq|grader"),
    )
    p, model = resolve_agentic_query_target()
    assert p.id == "aq"
    assert model == "grader"


def test_note_distill_slot(monkeypatch):
    default = _provider(id="def", is_default=True)
    dist = _provider(id="dist")
    monkeypatch.setattr(
        "src.config.get_config",
        lambda: _cfg([default, dist], note_distill_model="dist|distill-v1"),
    )
    p, model = resolve_note_distill_target()
    assert p.id == "dist"
    assert model == "distill-v1"


def test_ingest_vision_only_accepts_marked_visual(monkeypatch):
    p = _provider(id="v", visual_model_ids=["see-1"], is_default=True)
    monkeypatch.setattr(
        "src.config.get_config",
        lambda: _cfg([p], visual_model_id="see-1"),
    )
    found, mid = resolve_ingest_vision()
    assert found.id == "v"
    assert mid == "see-1"
    monkeypatch.setattr(
        "src.config.get_config",
        lambda: _cfg([p], visual_model_id="not-visual"),
    )
    assert resolve_ingest_vision() == (None, "")


def test_ingest_vision_prefers_provider_id_over_same_name(monkeypatch):
    first = _provider(id="dash", visual_model_ids=["qwen-vl"], is_default=True)
    second = _provider(id="or", visual_model_ids=["qwen-vl"])
    monkeypatch.setattr(
        "src.config.get_config",
        lambda: _cfg([first, second], visual_model_id="or|qwen-vl"),
    )
    found, mid = resolve_ingest_vision()
    assert found.id == "or"
    assert mid == "qwen-vl"
    monkeypatch.setattr(
        "src.config.get_config",
        lambda: _cfg([first, second], visual_model_id="qwen-vl"),
    )
    found, mid = resolve_ingest_vision()
    assert found.id == "dash"
    assert mid == "qwen-vl"


def test_distill_flattens_images_when_model_is_not_visual(monkeypatch):
    from src.notes import service as notes_service

    default = _provider(id="def", is_default=True, visual_model_ids=[])
    monkeypatch.setattr(
        "src.config.get_config",
        lambda: _cfg([default], note_distill_model="def|chat-a"),
    )
    monkeypatch.setattr(
        notes_service,
        "get_llm",
        lambda: SimpleNamespace(generate=lambda *a, **k: "Distilled body"),
    )
    seen = {}

    def _prep(text):
        seen["text"] = text
        return "FLAT " + text

    monkeypatch.setattr(
        "src.parsers.image_utils.prepare_text_for_non_visual_llm",
        _prep,
    )
    out = notes_service._run_distill_llm(":::image\nimage_id: x\n:::\n", "n1")
    assert out == "Distilled body"
    assert seen["text"].startswith(":::image")


def test_provider_model_is_visual():
    p = _provider(visual_model_ids=["see-1"], default_model="see-1")
    assert provider_model_is_visual(p, None) is True
    assert provider_model_is_visual(p, "chat-a") is False
    assert provider_model_is_visual(None, "see-1") is False


def test_oneshot_slot_snapshot_prefixes_provider():
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1]
        / "frontend/src/components/llm-provider/oneshot-slots.ts"
    ).read_text(encoding="utf-8")
    assert "export function oneshotSlotSnapshot" in src
    assert "agentic_query_model" in src
    assert "note_distill_model" in src
    assert "enrichment_model" in src
    assert "meeting_model" in src


def test_get_config_rereads_disk(tmp_path, monkeypatch):
    from src import config as cfg

    path = tmp_path / "config.yaml"
    path.write_text("enrichment:\n  meeting_model: first\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_PATH", path)
    assert cfg.get_config().enrichment.meeting_model == "first"
    path.write_text("enrichment:\n  meeting_model: second\n", encoding="utf-8")
    assert cfg.get_config().enrichment.meeting_model == "second"
