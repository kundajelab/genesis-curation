"""Load frozen baseline/candidate predictions into the isolated audit tables only."""

from __future__ import annotations

import argparse
import copy
from functools import lru_cache
from pathlib import Path
from typing import Any

from genesis_curation.contracts.records import fingerprint, load
from genesis_curation.curation import harmonize
from genesis_curation.registry import store
from genesis_curation.web import audit, studies


@lru_cache
def run(folder: Path) -> dict[str, Any]:
    report = load(folder / "report.json")
    return studies.get(folder / "catalog", report["result"]["study"])


def pending(study: dict[str, Any], sid: str) -> list[str]:
    return sorted(
        {
            t["field"]
            for t in study["tasks"]
            if any(target["subject"] == sid for target in t["targets"])
        }
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["registry", "frozen", "candidate_root"]:
        parser.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path)
    parser.add_argument("--title", default="Plant metadata reference audit")
    parser.add_argument("--fields", nargs="+", choices=sorted(harmonize.FIELDS))
    args = parser.parse_args()
    frozen = load(args.frozen)
    samples = copy.deepcopy(frozen["samples"])
    for sample in samples:
        if args.baseline_root is not None:
            baseline = run(args.baseline_root)
            prediction = next(r for r in baseline["records"] if r["id"] == sample["id"])
            if fingerprint(prediction["sources"]) != fingerprint(sample["state"]["sources"]):
                raise ValueError("Baseline predictions must use the identical frozen sources")
            sample["state"] = prediction
            sample["task_fields"] = pending(baseline, sample["id"])
        folder = (
            args.candidate_root
            if (args.candidate_root / "report.json").exists()
            else args.candidate_root / sample["study"]
        )
        candidate = run(folder)
        sample["candidate"] = next(r for r in candidate["records"] if r["id"] == sample["id"])
        sample["candidate_task_fields"] = pending(candidate, sample["id"])
        if args.fields:
            sample["audit_fields"] = args.fields
    store.initialize(args.registry)
    key = audit.create(args.registry, samples, args.title, "reference-import")
    print(
        {
            "audit": key,
            "samples": len(samples),
            "answers": "AWAITING_HUMAN_LABELS",
            "manifest_hash": frozen["manifest_hash"],
        }
    )
