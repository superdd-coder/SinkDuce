"""Tests for sparse vocabulary rebuild from texts."""

import pytest
from src.rag.sparse_encoder import SparseEncoder


class TestRebuildFromTexts:
    """Pure unit tests — no Qdrant needed."""

    def test_empty(self):
        encoder, vectors = SparseEncoder.rebuild_from_texts([])
        assert encoder._doc_count == 0
        assert vectors == []

    def test_single_document(self):
        encoder, vectors = SparseEncoder.rebuild_from_texts(["hello world"])
        assert encoder._doc_count == 1
        assert encoder.avg_dl == 2.0  # "hello" + "world"
        assert len(vectors) == 1
        assert len(vectors[0]) == 2  # two terms
        # all weights should be positive
        assert all(w > 0 for w in vectors[0].values())

    def test_idf_common_vs_rare(self):
        """A term appearing in all docs should have lower IDF than one in one doc."""
        texts = [
            "common rare1",
            "common rare2",
            "common rare3",
        ]
        encoder, _vectors = SparseEncoder.rebuild_from_texts(texts)
        assert encoder._doc_count == 3

        id_common = encoder.term_to_id["common"]
        id_rare1 = encoder.term_to_id["rare1"]

        import math
        df_common = encoder.doc_freqs[id_common]  # 3
        df_rare1 = encoder.doc_freqs[id_rare1]    # 1

        idf_common = math.log((3 - df_common + 0.5) / (df_common + 0.5) + 1)
        idf_rare1 = math.log((3 - df_rare1 + 0.5) / (df_rare1 + 0.5) + 1)

        assert idf_rare1 > idf_common, "rare term should have higher IDF"

        # BM25 weight for the rare term should be nonzero in doc 0
        vec0 = _vectors[0]
        assert vec0[id_rare1] > 0

    def test_cjk_bigram_tokens(self):
        """Chinese text should produce character bigram tokens."""
        encoder, vectors = SparseEncoder.rebuild_from_texts(["合同条款"])
        # Should have: 合, 同, 条, 款, 合同, 同条, 条款
        assert len(encoder.term_to_id) >= 5  # at least the single chars + bigrams

    def test_term_id_stable_across_calls(self):
        """Same input should produce consistent term_to_id mapping."""
        e1, _ = SparseEncoder.rebuild_from_texts(["alpha beta"])
        e2, _ = SparseEncoder.rebuild_from_texts(["alpha beta"])
        assert e1.term_to_id == e2.term_to_id
        assert e1.doc_freqs == e2.doc_freqs

    def test_avg_dl(self):
        """Average doc length should be computed correctly."""
        texts = ["a b c", "d e"]  # lengths 3, 2
        encoder, _ = SparseEncoder.rebuild_from_texts(texts)
        assert encoder.avg_dl == 2.5
        assert encoder._doc_count == 2

    def test_avg_dl_empty(self):
        encoder, _ = SparseEncoder.rebuild_from_texts([])
        assert encoder.avg_dl == 0.0


class TestSparseEncoderLoad:
    def test_missing_vocab_clears_previous_collection(self):
        encoder = SparseEncoder()
        encoder.build_vocab(["alpha beta from another collection"])
        assert encoder.term_to_id

        db = type("DB", (), {})()
        db.get_collection_config = lambda collection: {}  # noqa: ARG005
        encoder.load(db, "__sinkduce_meeting_transcripts__")
        assert encoder.term_to_id == {}
        assert encoder.doc_freqs == {}
        assert encoder._doc_count == 0
        assert encoder.encode_query("alpha") == {}


def test_sparse_dot_product_applies_bm25_idf_once():
    import math

    encoder, vectors = SparseEncoder.rebuild_from_texts(["alpha", "beta", "beta"])
    query = encoder.encode_query("alpha")
    score = sum(query.get(term, 0) * weight for term, weight in vectors[0].items())
    assert score == pytest.approx(math.log((3 - 1 + .5) / (1 + .5) + 1))


@pytest.mark.parametrize("text", ["甲，乙", "甲 beta 乙", "甲\n乙"])
def test_cjk_bigrams_do_not_cross_punctuation_or_latin_text(text):
    from src.rag.sparse_encoder import _tokenize

    assert "甲乙" not in _tokenize(text)


def test_incremental_documents_use_current_idf_without_rewriting_old_vectors():
    import math

    encoder = SparseEncoder()
    alpha_vector = encoder.encode(["alpha"])[0]
    encoder.encode(["beta", "beta"])
    score = sum(encoder.encode_query("alpha").get(term, 0) * weight
                for term, weight in alpha_vector.items())
    assert score == pytest.approx(math.log((3 - 1 + .5) / (1 + .5) + 1))


def test_vocabulary_persists_vector_format_and_legacy_is_compatible():
    from unittest.mock import MagicMock

    db = MagicMock()
    fresh = SparseEncoder()
    fresh.encode(["alpha", "beta", "beta"])
    fresh.save(db, "col")
    saved = db.update_collection_config.call_args.args[1]["sparse_vocab"]
    assert saved["vector_format"] == "bm25_tf_v2"
    db.get_collection_config.return_value = {"sparse_vocab": saved}
    loaded = SparseEncoder()
    loaded.load(db, "col")
    assert loaded.encode_query("alpha") == fresh.encode_query("alpha")
    # Old persisted vectors already contain IDF; no second factor is applied.
    legacy = dict(saved)
    legacy.pop("vector_format")
    db.get_collection_config.return_value = {"sparse_vocab": legacy}
    loaded.load(db, "col")
    assert loaded.encode_query("alpha") == {saved["term_to_id"]["alpha"]: 1}
    loaded.save(db, "col")
    assert db.update_collection_config.call_args.args[1]["sparse_vocab"]["vector_format"] == "bm25_idf_v1"
