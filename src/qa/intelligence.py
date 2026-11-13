"""Validated AI suggestions. Provider work happens outside SQLite write locks."""

import json
from typing import Literal

from pydantic import BaseModel, Field

from src.prompts import QA_DATA_USER, QA_MAPPING_SYSTEM, QA_SUMMARY_SYSTEM

from . import excel, store
from .models import FieldMapping, TableRegion

MAX_PACKET = 140_000


class HeaderSuggestion(FieldMapping):
    header_row: int = Field(ge=1, le=2020)
    sheet: str | None = None
    region: TableRegion | None = None
    row_kinds: dict[
        str, Literal["question", "section", "note", "header", "continuation", "review"]
    ] = Field(default_factory=dict)


class AnnotationRow(BaseModel):
    key: str
    summary: str
    reply_summary: str = ""


class Annotations(BaseModel):
    rows: list[AnnotationRow]


def get_llm():
    from src.services import services

    return services.llm


def generate_json(system, packet, schema):
    llm = get_llm()
    if llm is None:
        raise ValueError("ai_unavailable")
    payload = json.dumps(packet, ensure_ascii=False)
    if len(payload) > MAX_PACKET:
        raise ValueError("ai_input_limit")
    raw = llm.generate(
        QA_DATA_USER.format(payload=payload),
        system=system,
        temperature=0,
        max_tokens=8192,
        response_format={"type": "json_object"},
        thinking=False,
    )
    return schema.model_validate_json(raw)


def mapping_packet(parsed, fixed_region):
    def sample(row):
        return {**row, "values": [value[:800] for value in row["values"]]}

    candidates = [
        {**c, "preview": [sample(r) for r in c.get("preview", [])]}
        for c in parsed.get("candidates", [])[:12]
    ]
    packet = {
        "sheet": parsed["sheet"],
        "preview": [sample(r) for r in parsed["preview"]],
        "candidates": candidates,
        "region": parsed.get("region"),
        "fixed_region": fixed_region,
        "list_choice_columns": [int(index) for index in parsed.get("validations", {})],
        "rows": [],
    }
    # Fit whole structural rows into the request; unsampled rows retain manual review.
    for row in parsed["rows"]:
        item = {
            "key": row["key"],
            "values": [v[:800] for v in row["values"]],
            "bold": row.get("bold", []),
            "merges": row.get("merges", []),
        }
        if (
            len(json.dumps(packet, ensure_ascii=False)) + len(json.dumps(item, ensure_ascii=False))
            > MAX_PACKET - 1000
        ):
            break
        packet["rows"].append(item)
    return packet


def map_headers(raw, parsed, fixed_region=False):
    try:
        suggestion = generate_json(
            QA_MAPPING_SYSTEM,
            mapping_packet(parsed, fixed_region),
            HeaderSuggestion,
        )
        if not fixed_region and (
            suggestion.header_row != parsed["header_row"]
            or suggestion.sheet
            and suggestion.sheet != parsed["sheet"]
            or suggestion.region
        ):
            parsed = excel.read_workbook(
                raw,
                suggestion.sheet or parsed["sheet"],
                suggestion.header_row,
                suggestion.region.model_dump() if suggestion.region else None,
            )
        mapping = suggestion.model_dump(exclude={"header_row", "sheet", "region", "row_kinds"})
        selected = [
            mapping["question"],
            *mapping["replies"],
            *mapping["ignored"],
        ]
        selected += [
            mapping[key] for key in ("number", "status", "group") if mapping[key] is not None
        ]
        start = parsed["region"]["start_column"] - 1
        if len(selected) != len(set(selected)) or any(
            not start <= i < len(parsed["headers"]) for i in selected
        ):
            raise ValueError("invalid_mapping")
        from .workbook_reader import classify_rows, header_role, resolve_group_mapping

        if header_role(parsed["headers"][mapping["question"]]) == "group" and any(
            header_role(label) == "question" for label in parsed["headers"][start:]
        ):
            raise ValueError("invalid_mapping")
        group = mapping["group"]
        if group is not None and header_role(parsed["headers"][group]) not in (
            "group",
            "type",
            "extra",
        ):
            raise ValueError("invalid_mapping")
        mapping = resolve_group_mapping(parsed, mapping)
        table, _ = classify_rows(parsed, mapping)
        known = {r["key"] for r in table if r["kind"] == "review"}
        parsed["row_actions"] = {
            key: kind for key, kind in suggestion.row_kinds.items() if key in known
        }
        parsed["mapping_confirmed"] = True
        return parsed, mapping, "ready"
    except Exception:  # noqa: BLE001 — provider/parser failures retain a manual or retry path
        # Never include provider errors or workbook contents in application logs.
        return parsed, excel.default_mapping(parsed), "manual"


def analyze(collection, lid, iid, plan_revision):
    from .pipeline import analyze as run

    return run(collection, lid, iid, plan_revision)


def annotate(collection, lid, qid, expected_revision):
    from . import service

    with store.connection(collection, "update") as conn:
        listing = store.get_list(conn, lid)
        if listing["revision"] != expected_revision or listing["archived"]:
            store.fail("stale_revision")
        vid = listing["current_version_id"]
        key = service.annotation_key(conn, vid, qid)
    snapshot = service.detail(collection, lid)
    question = next(q for q in snapshot["questions"] if q["id"] == qid)
    try:
        result = generate_json(
            QA_SUMMARY_SYSTEM,
            {
                "rows": [
                    {
                        "key": qid,
                        "text": question["text"],
                        "replies": [
                            {"label": r["label"], "body": r["body"]}
                            for r in question["replies"]
                            if r["included"]
                        ],
                    }
                ],
                "history": [],
                "existing_matches": {},
                "historical_columns": [],
            },
            Annotations,
        )
        suggestion = next(row for row in result.rows if row.key == qid)
    except Exception:  # noqa: BLE001 — derived suggestions are optional
        return snapshot
    with store.connection(collection, "update") as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        listing = store.get_list(conn, lid)
        if (
            listing["current_version_id"] == vid
            and not listing["archived"]
            and service.annotation_key(conn, vid, qid) == key
        ):
            service.save_annotation(conn, vid, qid, suggestion.model_dump())
    return service.detail(collection, lid)
