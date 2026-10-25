"""Freeze cached plant samples for a separate, human-labelled reference audit."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from genesis_curation.contracts.records import dump, fingerprint, load
from genesis_curation.web import studies


def freeze(pilot: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise ValueError("Freeze requires a new output; never replace an audited cohort")
    samples, snapshots = [], []
    manifest = load(Path(__file__).parent / "fixtures/study/public-pilot.json")
    by_accession = {}
    for path in sorted(pilot.glob("public-*/evaluation.json")):
        for entry in load(path)["studies"]:
            if entry.get("study"):
                by_accession[entry["accession"]] = (path.parent / "catalog", entry["study"])
    for item in manifest["studies"]:
        registry, key = by_accession[item["accession"]]
        study = studies.get(registry, key)
        snapshots.append({**study, "accession": item["accession"], "split": item["split"]})
        ranked = sorted(study["records"], key=lambda r: r["name"])
        routine = ranked[0]
        remaining = [r for r in ranked if r["id"] != routine["id"]]

        def risk(row: dict[str, Any], study: dict[str, Any] = study) -> tuple[int, str]:
            count = sum(
                f["status"] in {"unknown", "conflicting"} and bool(f["evidence"])
                for f in row["fields"]
            )
            count += 100 if row["id"] in study["preparation"]["failures"] else 0
            return -count, row["name"]

        difficult = sorted(remaining, key=risk)[0] if remaining else None
        for row in [routine] + ([difficult] if difficult else []):
            samples.append(
                {
                    "id": row["id"],
                    "state": row,
                    "study": item["accession"],
                    "split": "evaluation" if item["split"] == "reserved" else "development",
                    "task_fields": sorted(
                        {
                            t["field"]
                            for t in study["tasks"]
                            if any(target["subject"] == row["id"] for target in t["targets"])
                        }
                    ),
                }
            )
    result = {
        "version": 1,
        "samples": samples,
        "studies": snapshots,
        "selection": (
            "first accession plus highest unresolved-evidence/failure count; accession breaks ties"
        ),
        "labels": "NOT_YET_REVIEWED",
    }
    result["manifest_hash"] = fingerprint(result)
    dump(output, result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = freeze(args.pilot, args.output)
    print(
        {
            "samples": len(result["samples"]),
            "studies": len(result["studies"]),
            "manifest_hash": result["manifest_hash"],
        }
    )
