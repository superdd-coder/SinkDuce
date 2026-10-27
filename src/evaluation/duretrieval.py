"""Prepare DuRetrieval gold without installing a dataset/Parquet dependency.

JSON previews are cached and hashed. The preview service does not provide an
immutable revision contract, so its output is explicitly a pilot, never an
official leaderboard result. Full runs should convert a pinned source export.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

REPO = "mteb/DuRetrieval"
REVISION = "8d9b3d6a6a62b7fd2d4df0821d612366dc52d14e"


def _ids(rows):
    result = {}
    for row in rows:
        id_ = row.get("_id")
        if not isinstance(id_, str) or not id_ or id_ in result:
            raise ValueError("Invalid or duplicate official ID")
        if not isinstance(row.get("text"), str):
            raise ValueError("Missing passage/query text")
        result[id_] = row
    return result


def select_pilot(corpus, queries, qrels, *, query_limit=50, distractors=1000, seed=20261005):
    if query_limit < 1 or distractors < 0:
        raise ValueError("Invalid pilot size")
    documents, questions = _ids(corpus), _ids(queries)
    rng = random.Random(seed)
    selected = set(rng.sample(sorted(questions), min(query_limit, len(questions))))
    labels = [row for row in qrels if row["query-id"] in selected]
    gold = {row["corpus-id"] for row in labels if row["score"] > 0}
    if not gold.issubset(documents):
        raise ValueError("Pilot is missing positive evidence")
    remaining = sorted(set(documents) - gold)
    if len(remaining) < distractors:
        raise ValueError("Too few distractor passages")
    chosen = gold | set(rng.sample(remaining, distractors))
    # Keep any explicit non-relevant judgment so qrels remain valid.
    chosen |= {row["corpus-id"] for row in labels}
    return ([documents[id_] for id_ in sorted(chosen)],
            [questions[id_] for id_ in sorted(selected)], labels)


def write_dataset(corpus, queries, qrels, output, *, source, pilot):
    """Validate all gold before creating a new, non-overwriting dataset."""
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    documents, questions = _ids(corpus), _ids(queries)
    positive = {id_: set() for id_ in questions}
    seen = set()
    for row in qrels:
        qid, did, grade = row["query-id"], row["corpus-id"], row["score"]
        if qid not in questions or did not in documents or not isinstance(grade, int) or grade < 0:
            raise ValueError("Invalid or missing gold ID")
        if (qid, did) in seen:
            raise ValueError("Duplicate qrel")
        seen.add((qid, did))
        if grade > 0:
            positive[qid].add(did)
    if not documents or not questions or any(not ids for ids in positive.values()):
        raise ValueError("Every DuRetrieval query requires positive gold")
    output.mkdir(parents=True)
    manifest = {"name": "DuRetrieval", "version": "1", "kind": "pilot" if pilot else "public",
                "granularity": "passage", "language": "zh", "split": "dev", "source": source,
                "corpus_count": len(documents), "query_count": len(questions),
                "official_corpus_count": 100001, "official_query_count": 2000,
                "official_score": False}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
    for filename, rows in [("corpus.jsonl", documents.values()), ("queries.jsonl", questions.values())]:
        (output / filename).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                                       encoding="utf-8")
    with (output / "qrels.tsv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["query-id", "corpus-id", "score"], delimiter="\t")
        writer.writeheader()
        writer.writerows(qrels)


def preview_rows(response):
    rows = []
    for item in response["rows"]:
        if item.get("truncated_cells"):
            raise ValueError("Dataset preview contains truncated cells; use a full source export")
        rows.append(item["row"])
    return rows


class PreviewClient:
    """Public GETs only; cache exact bytes for repeatable offline preparation."""

    def __init__(self, cache):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self._rate_lock = threading.Lock()
        self._next_request = 0.

    def get(self, url):
        path = self.cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        if path.exists():
            return json.loads(path.read_bytes())
        for attempt in range(3):
            try:
                # Space uncached public requests across all page workers.
                with self._rate_lock:
                    now = time.monotonic()
                    if self._next_request > now:
                        time.sleep(self._next_request - now)
                    self._next_request = time.monotonic() + .85
                request = urllib.request.Request(url, headers={"User-Agent": "SinkDuce-benchmark-pilot"})
                with urllib.request.urlopen(request, timeout=20) as response:
                    raw = response.read()
                data = json.loads(raw)
                path.write_bytes(raw)
                return data
            except Exception as error:
                if attempt == 2:
                    raise
                delay = attempt + 1
                if isinstance(error, urllib.error.HTTPError):
                    if error.code not in (429, 500, 502, 503, 504):
                        raise
                    try:
                        delay = max(delay, float(error.headers.get("Retry-After", delay)))
                    except ValueError:
                        pass
                time.sleep(delay)

    def rows(self, config, offset=0, length=100):
        url = "https://datasets-server.huggingface.co/rows?" + urllib.parse.urlencode({
            "dataset": REPO, "config": config, "split": "dev", "offset": offset, "length": length})
        response = self.get(url)
        return preview_rows(response), response["num_rows_total"]

    def pages(self, config, offsets):
        with ThreadPoolExecutor(max_workers=4) as pool:
            return [row for rows, _ in pool.map(lambda offset: self.rows(config, offset), offsets)
                    for row in rows]

    def split(self, config):
        first, total = self.rows(config)
        return first + self.pages(config, range(100, total, 100))


def fetch_pilot(client, *, query_limit=50, distractors=1000, seed=20261005):
    metadata_url = f"https://huggingface.co/api/datasets/{REPO}"
    metadata = client.get(metadata_url)
    revision = metadata["sha"]
    if revision != REVISION:
        raise ValueError("Dataset head changed; review and pin the new source revision")
    queries, qrels = client.split("queries"), client.split("default")
    selected = set(random.Random(seed).sample(sorted(_ids(queries)), min(query_limit, len(queries))))
    gold = {row["corpus-id"] for row in qrels if row["query-id"] in selected and row["score"] > 0}
    # Scan official order until all selected gold is present; never shrink gold.
    corpus, total = client.rows("corpus")
    documents = _ids(corpus)
    offset = 100
    while not gold.issubset(documents):
        if offset >= total:
            raise ValueError("Official source does not contain all selected gold")
        rows = client.pages("corpus", range(offset, min(offset + 1000, total), 100))
        documents.update(_ids(rows))
        offset += 1000
        print(f"Gold scan: {min(offset, total)}/{total}, missing={len(gold - documents.keys())}", flush=True)
    # Additional seeded windows avoid taking distractors only from the gold prefix.
    rng = random.Random(seed)
    offsets = rng.sample(list(range(0, total, 100)), min((distractors + 99) // 100 + 1,
                                                      (total + 99) // 100))
    documents.update(_ids(client.pages("corpus", offsets)))
    selected_rows = select_pilot(list(documents.values()), queries, qrels,
                                 query_limit=query_limit, distractors=distractors, seed=seed)
    checksums = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in sorted(client.cache.glob("*.json"))}
    source = {"repository": REPO, "revision": revision, "transport": "dataset-server-json-preview",
              "revision_verified_for_preview": False,
              "retrieved_at": datetime.now(timezone.utc).isoformat(), "seed": seed,
              "query_selection": "seeded sample of official dev IDs", "distractors": distractors,
              "cached_responses_sha256": checksums}
    return *selected_rows, source


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--queries", type=int, default=50)
    parser.add_argument("--distractors", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20261005)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise FileExistsError(args.output)
    corpus, queries, qrels, source = fetch_pilot(PreviewClient(args.cache), query_limit=args.queries,
                                              distractors=args.distractors, seed=args.seed)
    write_dataset(corpus, queries, qrels, args.output, source=source, pilot=True)
    print(json.dumps({"dataset": str(args.output), "corpus": len(corpus), "queries": len(queries),
                      "qrels": len(qrels), "official_score": False}))


if __name__ == "__main__":
    main()
