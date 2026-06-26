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
