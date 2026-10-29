"""Bounded public metadata pilot in an isolated registry; never supplies expert labels."""

from __future__ import annotations

import argparse
from pathlib import Path

from genesis_curation.contracts.records import dump
from genesis_curation.llm import providers
from genesis_curation.web import mining, runtime, worker

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--configs", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    args = parser.parse_args()
    if (args.root / "app.sqlite").exists():
        raise SystemExit("Use a new isolated evaluation root")
    runtime.initialize(args.root, "evaluation", "synthetic-mining-evaluation-password")
    config = providers.select(args.configs, args.provider)
    config_path = args.root / "providers.json"
    dump(config_path, {"schema_version": 1, "providers": [config]})
    run = mining.create(
        args.root,
        {
            "goal": "Find public bulk RNA-seq and ATAC-seq studies in "
            "Arabidopsis thaliana and Sorghum bicolor.",
            "limits": {"candidates": 8, "studies": 4, "samples": 20, "minutes": 20},
        },
        config,
        "evaluation",
    )
    while worker.once(args.root, config_path):
        run = mining.read(args.root, run["id"])
        print(mining.summary(run), flush=True)
    run = mining.read(args.root, run["id"])
    report = {
        **mining.summary(run),
        "candidates": run["candidates"],
        "ledger": run["ledger"],
        "human_accuracy": "NOT_INDEPENDENTLY_VALIDATED",
        "sequencing_downloads": "NOT_RUN",
    }
    dump(args.root / "report.json", report)
