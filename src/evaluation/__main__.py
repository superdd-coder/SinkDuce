"""Run with an initialized pipeline factory, or score saved stage traces."""

import argparse
import importlib
import json
from pathlib import Path

from src.evaluation.models import StageResult, load_dataset
from src.evaluation.service import evaluate


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("score", "run"):
        command = commands.add_parser(name)
        command.add_argument("--dataset", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--k", type=int, nargs="+", default=[5, 10, 20])
        if name == "score":
            command.add_argument("--run", type=Path, required=True)
        else:
            command.add_argument("--runner", required=True, help="module:factory receiving Dataset")
    args = parser.parse_args(argv)
    dataset = load_dataset(args.dataset)
    if args.command == "score":
        rows = {}
        for line in args.run.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            query_id = row.pop("query_id")
            if query_id in rows or query_id not in dataset.queries:
                raise ValueError("Saved run has duplicate or unknown query IDs")
            rows[query_id] = StageResult(**row)

        def runner(query):
            return rows.get(query.id, StageResult(errors=[{
                "stage": "execution", "error": "missing_run_query",
            }]))
    else:
        module, name = args.runner.split(":", 1)
        runner = getattr(importlib.import_module(module), name)(dataset)
    report = evaluate(dataset, runner, ks=args.k, measure_latency=args.command == "run")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                           encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
