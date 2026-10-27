"""Keep model-generated retrieval notes separate from primary source quotations."""

from src.rag.chunker import _strip_image_fences
from src.rag.index_text import source_name


def primary_passage(chunk) -> str:
    """Figure OCR/descriptions are machine readings, not primary prose."""
    return _strip_image_fences(getattr(chunk, "text", "") or "").strip()


def grounded_findings(result: dict, candidates: list, selected: list[int]) -> str:
    """Accept exact primary-source quotations; otherwise retain source excerpts.

    This validates provenance, not the truth of an inference. Inferences are
    deliberately left to answer synthesis instead of being certified here.
    Older/custom graders can still return a literal primary-source passage.
    """
    if not selected:
        return ""
    passages = {i: primary_passage(candidates[i]) for i in selected}
    quotes = []
    evidence = result.get("evidence")
    if isinstance(evidence, list):
        for item in evidence:
            if not isinstance(item, dict):
                continue
            index, quote = item.get("index"), item.get("quote")
            if (type(index) is int and index in passages and isinstance(quote, str)
                    and len(quote.strip()) >= 12 and quote.strip() in passages[index]):
                pair = (index, quote.strip())
                if pair not in quotes:
                    quotes.append(pair)
    legacy = result.get("retained_info")
    if not quotes and isinstance(legacy, str) and legacy.strip():
        if any(legacy.strip() in text for text in passages.values() if text):
            return legacy.strip()
    if not quotes:
        # Missing or fabricated quotations must not remove relevant original
        # chunks, trigger another model call, or preserve the fabricated fact.
        quotes = [(i, text[:1200]) for i, text in passages.items() if text][:4]
    if not quotes:
        return "Relevant figures retained; their original images require inspection."
    return "\n".join(
        f'- Source excerpt ({source_name(candidates[i].metadata) or "document"}): {quote}'
        for i, quote in quotes
    )
