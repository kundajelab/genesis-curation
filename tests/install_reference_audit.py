"""Load frozen baseline/candidate predictions into the isolated audit tables only."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path

from genesis_curation.contracts.records import load
from genesis_curation.registry import store
from genesis_curation.web import audit, studies

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["registry", "frozen", "candidate_root"]:
        parser.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    args = parser.parse_args()
    frozen = load(args.frozen)
    samples = copy.deepcopy(frozen["samples"])
    for sample in samples:
        folder = args.candidate_root / sample["study"]
        report = load(folder / "report.json")
        candidate = studies.get(folder / "catalog", report["result"]["study"])
        sample["candidate"] = next(r for r in candidate["records"] if r["id"] == sample["id"])
        sample["candidate_task_fields"] = sorted(
            {
                t["field"]
                for t in candidate["tasks"]
                if any(target["subject"] == sample["id"] for target in t["targets"])
            }
        )
    store.initialize(args.registry)
    key = audit.create(
        args.registry, samples, "Plant metadata: 20-sample reference audit", "reference-import"
    )
    print(
        {
            "audit": key,
            "samples": len(samples),
            "answers": "AWAITING_HUMAN_LABELS",
            "manifest_hash": frozen["manifest_hash"],
        }
    )
