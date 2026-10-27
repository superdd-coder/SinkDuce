"""OCR intra-op threads must scale with engine-pool size.

A single engine serves the common small file (≤10 images) and must not be
pinned to one core; the full 3-engine pool keeps Docker's ~6 CPUs calm.
"""

from src.parsers.rapid_ocr import intra_op_threads


def test_docker_single_engine_gets_three_threads():
    assert intra_op_threads(1, desktop=False) == 3


def test_docker_two_engines_share_thread_budget():
    assert intra_op_threads(2, desktop=False) == 2


def test_docker_full_pool_keeps_one_thread_each():
    assert intra_op_threads(3, desktop=False) == 1


def test_desktop_keeps_two_threads_at_full_pool():
    assert intra_op_threads(3, desktop=True) == 2


def test_make_engine_threads_follow_engine_count(monkeypatch):
    captured: dict = {}

    class FakeRapidOCR:
        def __init__(self, params=None):
            captured.update(params or {})

    monkeypatch.setattr("rapidocr.RapidOCR", FakeRapidOCR)
    monkeypatch.setattr("src.parsers.rapid_ocr._desktop_ocr", lambda: False)

    from src.parsers.rapid_ocr import _make_engine

    _make_engine(1)
    assert captured["EngineConfig.onnxruntime.intra_op_num_threads"] == 3

    captured.clear()
    _make_engine(3)
    assert captured["EngineConfig.onnxruntime.intra_op_num_threads"] == 1
