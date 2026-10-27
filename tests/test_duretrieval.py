"""Public gold remains independent of indexing and sample selection."""

import json
import urllib.error

import pytest


def source_rows():
    corpus = [{"_id": x, "text": x, "title": ""} for x in ["a", "b", "noise"]]
    queries = [{"_id": "q", "text": "a b"}]
    qrels = [{"query-id": "q", "corpus-id": "a", "score": 1},
             {"query-id": "q", "corpus-id": "b", "score": 1}]
    return corpus, queries, qrels


def test_du_adapter_retains_official_ids_and_every_positive(tmp_path):
    from src.evaluation import duretrieval
    from src.evaluation.models import load_dataset

    out = tmp_path / "dataset"
    duretrieval.write_dataset(*source_rows(), out, source={"revision": "fixed"}, pilot=True)
    dataset = load_dataset(out)
    assert dataset.qrels["q"] == {"a": 1, "b": 1}
    assert dataset.manifest["kind"] == "pilot"
    assert dataset.manifest["source"]["revision"] == "fixed"
    assert dataset.manifest["granularity"] == "passage"
    assert set(dataset.corpus) == {"a", "b", "noise"}


@pytest.mark.parametrize("bad", ["missing_gold", "duplicate_doc", "duplicate_qrel", "no_gold"])
def test_du_adapter_rejects_bad_gold_before_publishing(tmp_path, bad):
    from src.evaluation import duretrieval

    corpus, queries, qrels = source_rows()
    if bad == "missing_gold":
        corpus = corpus[1:]
    elif bad == "duplicate_doc":
        corpus.append(corpus[0])
    elif bad == "duplicate_qrel":
        qrels.append(qrels[0])
    else:
        qrels = []
    out = tmp_path / "dataset"
    with pytest.raises(ValueError):
        duretrieval.write_dataset(corpus, queries, qrels, out, source={}, pilot=True)
    assert not out.exists()


def test_du_adapter_will_not_overwrite_a_previous_dataset(tmp_path):
    from src.evaluation import duretrieval

    out = tmp_path / "dataset"
    out.mkdir()
    (out / "keep").write_text("original")
    with pytest.raises(FileExistsError):
        duretrieval.write_dataset(*source_rows(), out, source={}, pilot=True)
    assert (out / "keep").read_text() == "original"


def test_json_preview_rejects_truncated_text_instead_of_silently_ingesting():
    from src.evaluation import duretrieval

    with pytest.raises(ValueError, match="truncat"):
        duretrieval.preview_rows({"rows": [{"row": {"_id": "a", "text": "short"},
                                          "truncated_cells": ["text"]}]})


def test_json_preview_preserves_official_rows():
    from src.evaluation import duretrieval

    rows = source_rows()[0]
    assert duretrieval.preview_rows({"rows": [{"row": row, "truncated_cells": []}
                                             for row in rows]}) == rows


def test_pilot_selection_keeps_all_gold_and_labels_smaller_corpus():
    from src.evaluation import duretrieval

    corpus, queries, qrels = source_rows()
    selected = duretrieval.select_pilot(corpus, queries, qrels, query_limit=1,
                                      distractors=1, seed=7)
    assert {d["_id"] for d in selected[0]} == {"a", "b", "noise"}
    assert selected[2] == qrels


def test_pilot_selection_refuses_missing_positive_or_too_few_distractors():
    from src.evaluation import duretrieval

    corpus, queries, qrels = source_rows()
    with pytest.raises(ValueError):
        duretrieval.select_pilot(corpus[1:], queries, qrels, query_limit=1, distractors=1)
    with pytest.raises(ValueError):
        duretrieval.select_pilot(corpus, queries, qrels, query_limit=1, distractors=2)


def test_preview_rate_limit_uses_retry_after_and_caches_success(tmp_path, monkeypatch):
    from src.evaluation import duretrieval

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"rows": []}'

    attempts, sleeps = [], []

    def request(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise urllib.error.HTTPError("https://example.test", 429, "limited",
                                         {"Retry-After": "3"}, None)
        return Response()

    monkeypatch.setattr(duretrieval.urllib.request, "urlopen", request)
    monkeypatch.setattr(duretrieval.time, "sleep", sleeps.append)
    client = duretrieval.PreviewClient(tmp_path)
    assert client.get("https://example.test") == {"rows": []}
    assert 3 in sleeps
    assert client.get("https://example.test") == {"rows": []}
    assert len(attempts) == 2
