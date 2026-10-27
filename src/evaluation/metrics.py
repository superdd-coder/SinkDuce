"""IR metrics from independent qrels, with TREC default linear nDCG gain."""

import math


def retrieval_metrics(ranking: list[str], qrels: dict[str, int], k: int) -> dict:
    if k <= 0:
        raise ValueError("k must be positive")
    ranked = list(dict.fromkeys(ranking))[:k]
    positive = {id_ for id_, grade in qrels.items() if grade > 0}
    if not positive:
        return dict.fromkeys(("recall", "precision", "mrr", "ndcg", "hit"))
    hits = [i for i, id_ in enumerate(ranked, 1) if id_ in positive]
    dcg = sum(qrels.get(id_, 0) / math.log2(i + 2) for i, id_ in enumerate(ranked))
    ideal = sum(grade / math.log2(i + 2)
                for i, grade in enumerate(sorted(qrels.values(), reverse=True)[:k]))
    return {"recall": len(hits) / len(positive), "precision": len(hits) / k,
            "mrr": 1 / hits[0] if hits else 0.0, "ndcg": dcg / ideal if ideal else 0.0,
            "hit": float(bool(hits))}


def evidence_coverage(ids: list[str], groups: list[list[str]]) -> float | None:
    if not groups:
        return None
    present = set(ids)
    return sum(bool(present.intersection(group)) for group in groups) / len(groups)
