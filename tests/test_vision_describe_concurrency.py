"""Vision describe should compete for the same ingest Parallel slots as Summary."""

from __future__ import annotations

import threading
import time

from src.parsers.base import ImageInfo
from src.parsers.image_utils import describe_images


class _Enrich:
    def __init__(self, n: int):
        self.max_parallel_context = n


class _Cfg:
    def __init__(self, n: int):
        self.enrichment = _Enrich(n)


def _images(n: int) -> list[ImageInfo]:
    return [
        ImageInfo(image_id=f"{i:032x}", image_bytes=b"x", image_format="png")
        for i in range(n)
    ]


def _patch_vision(monkeypatch, peak_holder: dict, sleep_s: float = 0.08):
    lock = threading.Lock()

    class FakeLLM:
        def describe_image(self, *_a, **_k):
            with lock:
                peak_holder["cur"] += 1
                peak_holder["peak"] = max(peak_holder["peak"], peak_holder["cur"])
            time.sleep(sleep_s)
            with lock:
                peak_holder["cur"] -= 1
            return "a diagram"

    monkeypatch.setattr("src.config.get_config", lambda: _Cfg(peak_holder["limit"]))
    monkeypatch.setattr(
        "src.providers.llm.create_llm_for_provider", lambda *_a, **_k: FakeLLM()
    )
    monkeypatch.setattr(
        "src.parsers.image_utils.normalize_raster_image",
        lambda *_a, **_k: (b"x", "png"),
    )


def test_describe_images_uses_settings_parallel_slots(monkeypatch):
    """A file with 8 images should run 8-wide when Parallel is 8, not cap at 5."""
    peak_holder = {"cur": 0, "peak": 0, "limit": 8}
    _patch_vision(monkeypatch, peak_holder)
    out = describe_images(_images(8), provider=object(), model_id="vision-x", prompt="p")
    assert len(out) == 8
    assert peak_holder["peak"] == 8


def test_describe_images_still_caps_at_ingest_limiter(monkeypatch):
    peak_holder = {"cur": 0, "peak": 0, "limit": 2}
    _patch_vision(monkeypatch, peak_holder, sleep_s=0.05)
    out = describe_images(_images(6), provider=object(), model_id="vision-x", prompt="p")
    assert len(out) == 6
    assert peak_holder["peak"] == 2


def test_vision_file_pool_is_not_a_five_file_gate():
    from src.tasks import handlers

    assert handlers._vision_executor._max_workers == handlers._enrich_executor._max_workers
