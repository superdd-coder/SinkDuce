from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

DocumentID = Annotated[str, Field(min_length=1, strict=True)]


class QueryCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: DocumentID
    text: str
    answerable: bool = True
    evidence_groups: list[list[DocumentID]] = Field(default_factory=list)
    category: str = "unspecified"
    reference_answer: str = ""


class Dataset(BaseModel):
    manifest: dict
    corpus: dict[str, dict]
    queries: dict[str, QueryCase]
    qrels: dict[str, dict[str, int]]
    fingerprint: str


class StageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: list[DocumentID] = Field(default_factory=list)
    context_ids: list[DocumentID] = Field(default_factory=list)
    context: str = ""
    parameters: dict = Field(default_factory=dict)
    errors: list[dict] = Field(default_factory=list)
    latency_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    trace: list[dict] = Field(default_factory=list)


def _read_jsonl(path: Path) -> dict[str, dict]:
    items = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        id_ = row.get("_id")
        if not isinstance(id_, str) or not id_ or id_ in items:
            raise ValueError(f"Invalid or duplicate _id in {path.name}")
        if not isinstance(row.get("text"), str):
            raise ValueError(f"Missing text in {path.name}")
        items[id_] = row
    return items


def load_dataset(path: str | Path) -> Dataset:
    """Load BEIR-style corpus/queries/qrels plus a versioned local manifest."""
    path = Path(path)
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    for key in ("name", "version", "kind", "granularity"):
        if not manifest.get(key):
            raise ValueError(f"Dataset manifest requires {key}")
    corpus = _read_jsonl(path / "corpus.jsonl")
    raw_queries = _read_jsonl(path / "queries.jsonl")
    if not corpus or not raw_queries:
        raise ValueError("Corpus and queries must be nonempty")
    qrels: dict[str, dict[str, int]] = {id_: {} for id_ in raw_queries}
    with (path / "qrels.tsv").open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file, delimiter="\t"):
            query_id, doc_id = row["query-id"], row["corpus-id"]
            score = int(row["score"])
            if query_id not in raw_queries or doc_id not in corpus or score < 0:
                raise ValueError("Qrels contain unknown IDs or negative relevance")
            if doc_id in qrels[query_id]:
                raise ValueError("Duplicate qrel")
            qrels[query_id][doc_id] = score
    queries = {}
    for id_, row in raw_queries.items():
        answerable = row.get("answerable", True)
        if not isinstance(answerable, bool):
            raise ValueError("answerable must be a boolean")
        positive = {id_ for id_, grade in qrels[id_].items() if grade > 0}
        if bool(positive) != answerable:
            raise ValueError("Answerability and positive gold qrels disagree")
        groups = row.get("evidence_groups", [])
        if not isinstance(groups, list) or any(
            not isinstance(group, list) or not group
            or any(doc_id not in positive for doc_id in group) for group in groups
        ):
            raise ValueError("Evidence groups require nonempty positive gold alternatives")
        queries[id_] = QueryCase(id=id_, text=row["text"], answerable=answerable, evidence_groups=groups,
                                 category=row.get("category", "unspecified"),
                                 reference_answer=row.get("reference_answer", ""))
    digest = hashlib.sha256()
    for name in ("manifest.json", "corpus.jsonl", "queries.jsonl", "qrels.tsv"):
        digest.update(name.encode())
        digest.update((path / name).read_bytes())
    return Dataset(manifest=manifest, corpus=corpus, queries=queries, qrels=qrels,
                   fingerprint=digest.hexdigest())
