"""Transcript citation helpers ([stt_0001], Tagger JSON).

Used by meeting summary / tagger. Not chat session CRUD.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


def num_to_stt(num: int | str) -> str:
    """1 → 'stt_0001', 123 → 'stt_0123'."""
    return f"stt_{int(num):04d}"


def normalize_brackets(md: str) -> str:
    """Convert CJK / fullwidth brackets to ASCII so ref regex can match."""
    return (
        md.replace("【", "[")
        .replace("】", "]")
        .replace("〔", "[")
        .replace("〕", "]")
        .replace("［", "[")
        .replace("］", "]")
        .replace("｛", "{")
        .replace("｝", "}")
    )


def normalize_refs(md: str) -> str:
    """Convert numeric refs [67] → [stt_0067] in LLM markdown."""
    def _convert(m: re.Match) -> str:
        inner = m.group(1)
        tokens = [t.strip() for t in inner.split(",") if t.strip()]
        converted: list[str] = []
        for token in tokens:
            rm = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", token)
            if rm:
                converted.append(
                    f"stt_{int(rm.group(1)):04d}-{int(rm.group(2)):04d}"
                )
                continue
            nm = re.match(r"^(\d+)$", token)
            if nm:
                converted.append(f"stt_{int(nm.group(1)):04d}")
                continue
            converted.append(token)
        return "[" + ",".join(converted) + "]"

    return re.sub(
        r"\[(\d+(?:\s*[-–]\s*\d+)?(?:\s*,\s*\d+(?:\s*[-–]\s*\d+)?)*)\]",
        _convert,
        md,
    )


def clean_refs(md: str, valid_ids: list[str]) -> str:
    """Strip [stt_XXX] tags whose sentence IDs are not in *valid_ids*."""
    valid_set = set(valid_ids)

    def _expand_range(start_str: str, end_str: str) -> list[str]:
        try:
            s = int(start_str)
            e = int(end_str)
            if e < s or e - s > 50:
                return [f"stt_{start_str}"]
            return [f"stt_{n:04d}" for n in range(s, e + 1)]
        except ValueError:
            return [f"stt_{start_str}"]

    def _clean_one(m: re.Match) -> str:
        inner = m.group(1) or m.group(2)
        tokens = [t.strip() for t in inner.split(",") if t.strip()]
        expanded: list[str] = []
        for token in tokens:
            rm = re.match(r"^stt_(\d+)\s*[-–]\s*(\d+)$", token)
            if rm:
                expanded.extend(_expand_range(rm.group(1), rm.group(2)))
                continue
            cm = re.match(r"^stt_(\d{5,})$", token)
            if cm:
                digits = cm.group(1)
                digits = digits[: len(digits) - len(digits) % 4]
                chunks = [digits[j : j + 4] for j in range(0, len(digits), 4)]
                expanded.extend([f"stt_{c}" for c in chunks])
                continue
            if re.match(r"^stt_\d+$", token):
                expanded.append(token)
                continue

        kept = [i for i in expanded if any(v.endswith(i) for v in valid_set)]
        if not kept:
            return ""
        return "[" + ",".join(kept) + "]"

    return re.sub(
        r"\[(?:ref:)?\s*(stt_\d+(?:\s*[-–]\s*\d+)?(?:\s*,\s*stt_\d+(?:\s*[-–]\s*\d+)?)*)\s*\]"
        r"|(?<!\w)(stt_\d+)(?!\w)",
        _clean_one,
        md,
    )


def parse_tagger_response(raw: str) -> dict[str, list[str]]:
    """Parse Tagger LLM output into {"sentence_ids": [...]} as stt_XXXX."""
    raw_stripped = raw.strip()

    def _normalize_ids(raw_ids: list) -> list[str]:
        result: list[str] = []
        for i in raw_ids:
            if isinstance(i, (int, float)):
                result.append(num_to_stt(int(i)))
            elif isinstance(i, str) and i.isdigit():
                result.append(num_to_stt(int(i)))
            else:
                result.append(str(i))
        return result

    idx = raw_stripped.rfind('{"sentence_ids"')
    if idx < 0:
        idx = raw_stripped.rfind("{")
    if idx < 0:
        logger.warning(
            "[TAGGER] No JSON object found in LLM response (%d chars)",
            len(raw_stripped),
        )
        return {"sentence_ids": []}

    last_err = ""
    try:
        decoder = json.JSONDecoder()
        data, _ = decoder.raw_decode(raw_stripped[idx:])
        raw_ids = data.get("sentence_ids", [])
        if isinstance(raw_ids, list):
            return {"sentence_ids": _normalize_ids(raw_ids)}
    except json.JSONDecodeError as e:
        last_err = str(e)

    json_match = re.search(r"\{[\s\S]*?\}", raw_stripped[idx:])
    if json_match:
        try:
            data = json.loads(json_match.group())
            raw_ids = data.get("sentence_ids", [])
            if isinstance(raw_ids, list) and raw_ids:
                ids = _normalize_ids(raw_ids)
                logger.info("[TAGGER] Recovered via regex fallback (%d ids)", len(ids))
                return {"sentence_ids": ids}
        except json.JSONDecodeError:
            pass

    logger.warning(
        "[TAGGER] Failed to parse LLM response (%d chars, starts: %.200r, err: %s, ends: %.200r)",
        len(raw_stripped),
        raw_stripped[:200],
        last_err,
        raw_stripped[-200:],
    )
    return {"sentence_ids": []}
