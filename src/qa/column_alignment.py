"""One list-level alignment stage for reply rounds and additional fields."""

from difflib import SequenceMatcher

from src.prompts import QA_COLUMN_SYSTEM


def _normalized(value):
    return " ".join(value.split())


def _reply_evidence(values, historical):
    pairs = [
        (_normalized(value), _normalized(historical.get(key, "")))
        for key, value in values.items()
    ]
    retained = [(value, old) for value, old in pairs if value and old]
    ratios = [SequenceMatcher(None, value, old).ratio() for value, old in retained]
    active = sum(bool(value or old) for value, old in pairs)
    score = sum(ratios) / active if active else 0
    exact = bool(retained) and all(value == old for value, old in pairs)
    # A changed lone cell is insufficient evidence for an entire correspondence round.
    supported = (
        exact and any(len(value) >= 20 for value, _ in retained)
        or len(retained) >= 2
        and min(ratios, default=0) >= 0.85
        and not any(old and not value for value, old in pairs)
    )
    return supported, score


def _reply_target_supported(current, historical, target):
    evidence = {
        column["id"]: _reply_evidence(current["samples"], column["samples"])
        for column in historical
        if column["role"] == "reply"
    }
    supported, score = evidence.get(target, (False, 0))
    if not supported:
        return False
    competitors = [value for cid, value in evidence.items() if cid != target]
    # Similar or identical older rounds are not resolved by the model's confidence alone.
    return not any(eligible for eligible, _ in competitors) and score - max(
        (other_score for _, other_score in competitors), default=0
    ) >= 0.12


def _sampled(columns):
    return [
        {**column, "samples": dict(list(column["samples"].items())[:8])}
        for column in columns
    ]


def align(plan, meta, columns, samples, cache, checkpoint):
    from .pipeline import ColumnResult, cached_calls

    rows = [r for r in plan["rows"] if plan["matches"][r["key"]] not in (None, "new")]
    fixed = {v for v in plan["column_matches"].values() if v and v != "new"}
    uncertain = [k for k, v in plan["column_matches"].items() if not v]
    incoming = [
        {
            "key": k,
            "label": plan["headers"][int(k)],
            "role": "reply",
            "samples": {plan["matches"][r["key"]]: r["values"][int(k)] for r in rows},
        }
        for k in uncertain
    ]
    historical = [
        {
            **c,
            "role": "reply",
            "samples": {
                plan["matches"][r["key"]]: samples.get((plan["matches"][r["key"]], c["id"]), "")
                for r in rows
            },
        }
        for c in columns
        if c["id"] not in fixed
    ]
    field_matches, used = {}, set()
    fields = meta["fields"]
    for index in plan["mapping"].get("extras", []):
        label = plan["headers"][index]
        exact = [f for f in fields if f["label"] == label and f["id"] not in used]
        if len(exact) == 1:
            field_matches[str(index)] = exact[0]["id"]
            used.add(exact[0]["id"])
        else:
            incoming.append(
                {
                    "key": f"extra:{index}",
                    "label": label,
                    "role": "extra",
                    "samples": {plan["matches"][r["key"]]: r["values"][index] for r in rows},
                }
            )
    historical += [
        {
            "id": f["id"],
            "label": f["label"],
            "role": "extra",
            "samples": {
                plan["matches"][r["key"]]: str(
                    meta["values"].get(plan["matches"][r["key"]], {}).get(f["id"], {}).get("value")
                    or ""
                )
                for r in rows
            },
        }
        for f in fields
        if f["id"] not in used
    ]
    # No old field of this role means an additional field can safely get a fresh identity.
    incoming = [
        i
        for i in incoming
        if i["role"] == "reply" or any(h["role"] == i["role"] for h in historical)
    ]
    if not incoming:
        plan["field_matches"] = field_matches
        return
    output = cached_calls(
        [
            (
                QA_COLUMN_SYSTEM,
                {"stage": "columns", "incoming": _sampled(incoming), "historical": _sampled(historical)},
                ColumnResult,
            )
        ],
        cache,
        checkpoint,
    )[0]
    if output:
        by_key = {i["key"]: i for i in incoming}
        by_id = {h["id"]: h for h in historical}
        taken = set()
        for col in output.columns:
            current, old = by_key.get(col.key), by_id.get(col.target)
            if not current or col.certainty != "high" or col.target in taken:
                continue
            if col.target != "new" and (old is None or old["role"] != current["role"]):
                continue
            values = current["samples"]
            support = any(value.strip() for value in values.values())
            if support and col.target != "new":
                support = _reply_target_supported(current, historical, col.target) if current["role"] == "reply" else any(
                    value
                    and old["samples"].get(key)
                    and SequenceMatcher(None, value, old["samples"][key]).ratio() >= 0.6
                    for key, value in values.items()
                )
            if not support:
                continue
            if current["role"] == "reply":
                plan["column_matches"][col.key] = col.target
            elif col.target != "new":
                field_matches[col.key.split(":", 1)[1]] = col.target
            if col.target != "new":
                taken.add(col.target)
    plan["field_matches"] = field_matches
