"""Replay frozen plant evidence in isolated registries, without exposing audit labels."""

from __future__ import annotations

import argparse
from pathlib import Path

from evaluate_table_live import evaluate

from genesis_curation.contracts.records import dump, load


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["root", "frozen", "configs", "skills"]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--provider", required=True)
    args = parser.parse_args()
    if args.root.exists():
        raise ValueError("Replay requires a new directory")
    args.root.mkdir(parents=True)
    frozen = load(args.frozen)
    results = []
    for study in frozen["studies"]:
        snapshot = args.root / (study["accession"] + ".json")
        dump(snapshot, study)
        result = evaluate(
            args.root / study["accession"],
            snapshot,
            args.configs,
            args.skills,
            args.provider,
            32768,
            0,
        )
        result["accession"] = study["accession"]
        results.append(result)
        dump(
            args.root / "report.json",
            {
                "manifest_hash": frozen["manifest_hash"],
                "studies": results,
                "human_accuracy": "AWAITING_REFERENCE_AUDIT",
            },
        )
        print(
            {
                "accession": study["accession"],
                "rows": result["rows"],
                "seconds": round(result["elapsed_seconds"], 1),
                "failures": result["result"]["failed"],
                "decisions": result["pending_decisions"],
            },
            flush=True,
        )


if __name__ == "__main__":
    main()
