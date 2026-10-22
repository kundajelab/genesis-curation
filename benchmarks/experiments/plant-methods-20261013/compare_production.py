"""Check a completed native ATAC baseline against recorded production measurements.

This compares scientific observations on the same recorded reads/reference.
It does not assert byte-identical BAMs, runtime equivalence or biological truth.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from genesis_curation.benchmark.spec import sha256


def compare(native: Path, scores: Path, genesis: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError("Choose a new comparison record")
    measured = json.loads((native / "results.json").read_text())
    plan = json.loads((native / "experiment.json").read_text())
    common = json.loads((scores / "results.json").read_text())
    manifest_path = genesis / "manifest.json"
    if common["source_records"].get(str(manifest_path)) != sha256(manifest_path):
        raise ValueError("Production input manifest differs from the scored version")
    production = json.loads(manifest_path.read_text())
    if measured["status"] != "COMPLETE" or plan["spec"]["assay"] != "bulk-ATAC":
        raise ValueError("Use a completed native bulk-ATAC comparison")
    if plan["spec"]["adapter"] != "methods":
        raise ValueError("Use the native methods adapter")
    for path in ("benchmark/metrics.py", "atac/fragments.py"):
        if plan["source"]["recipe_sha256"][path] != common["recipe"]["recipe_sha256"][path]:
            raise ValueError("Common metric or fragment definitions differ")
    libraries = {value["id"]: value for value in plan["dataset"]["libraries"]}
    metadata = {value["library_id"]: value for value in production["libraries"]}
    observed = common["observations"]["genesis"]
    if libraries.keys() != metadata.keys() or libraries.keys() != observed.keys():
        raise ValueError("Compared library identities differ")
    ref = plan["dataset"]["reference"]
    assets = plan["assets"]
    inputs = {}
    for name, lib in libraries.items():
        meta = metadata[name]
        if lib["layout"] != "PE" or len(meta["lanes"]) != 1:
            raise ValueError("This check requires one prepared paired input per library")
        identities = {}
        for mate in ("read1", "read2"):
            digest = assets[lib[mate]]["sha256"]
            if digest != meta["lanes"][0][mate + "_sha256"]:
                raise ValueError("Recorded FASTQ bytes differ: " + name + "/" + mate)
            identities[mate] = digest
        for key in ("fasta", "tss"):
            digest = assets[ref[key]]["sha256"]
            if digest != meta["reference"][key + "_sha256"]:
                raise ValueError("Recorded reference/annotation bytes differ: " + key)
            identities[key] = digest
        inputs[name] = identities
    baseline = plan["spec"]["baseline"]
    cases = [row for row in measured["results"] if row["arm"] == baseline]
    repetitions = plan["spec"]["performance"]["repetitions"]
    if sorted(row["repetition"] for row in cases) != list(range(1, repetitions + 1)):
        raise ValueError("Baseline repetitions are missing or duplicated")
    checks = []
    for row in cases:
        if row["status"] != "COMPLETE" or row["libraries"].keys() != observed.keys():
            raise ValueError("Baseline libraries are incomplete")
        for name, expected in observed.items():
            value = row["libraries"][name]
            # Fixed-region counts refer to different peak sets if callers differ.
            # Compare those counts only after proving region identity.
            fields = [
                "layout",
                "fragment_definition",
                "mapq_distribution",
                "fragment_length_distribution",
                "peak_regions",
                "peak_widths",
                "peak_scores",
                "tss",
            ]
            fixed_match = value["fixed_regions"] == expected["fixed_regions"]
            if fixed_match:
                fields += ["fixed_counts"]
            for field in fields:
                checks.append(
                    {
                        "library": name,
                        "repetition": row["repetition"],
                        "observation": field,
                        "status": "EQUAL" if value[field] == expected[field] else "DIFFERENT",
                    }
                )
            counts = sorted(set(value["counts"]) | set(expected["counts"]))
            for key in counts:
                # Counts are sparse counters: an omitted counter means no events.
                # This does not coerce missing measured ratios to zero.
                if "fixed" in key and not fixed_match:
                    status = "NOT_COMPARABLE"
                else:
                    status = (
                        "EQUAL"
                        if value["counts"].get(key, 0) == expected["counts"].get(key, 0)
                        else "DIFFERENT"
                    )
                checks.append(
                    {
                        "library": name,
                        "repetition": row["repetition"],
                        "observation": "counts/" + key,
                        "status": status,
                        "native": value["counts"].get(key, 0),
                        "production": expected["counts"].get(key, 0),
                    }
                )
            if not fixed_match:
                checks.append(
                    {
                        "library": name,
                        "repetition": row["repetition"],
                        "observation": "fixed_regions",
                        "status": "DIFFERENT",
                    }
                )
    result = {
        "schema_version": 1,
        "status": "EQUAL" if all(row["status"] == "EQUAL" for row in checks) else "DIFFERENT",
        "scope": (
            "Recorded scientific observations for the native baseline and production workflow; "
            "not BAM byte identity, all-locus alignment identity, performance or biological truth"
        ),
        "experiment_id": plan["experiment_id"],
        "inputs": inputs,
        "checks": checks,
        "script_sha256": sha256(Path(__file__)),
        "sources": {
            str(path): sha256(path)
            for path in (
                native / "experiment.json",
                native / "results.json",
                scores / "results.json",
                manifest_path,
            )
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--workflow-scores", type=Path, required=True)
    parser.add_argument("--genesis-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(
        args.native.resolve(),
        args.workflow_scores.resolve(),
        args.genesis_run.resolve(),
        args.output.resolve(),
    )
    print(result["status"], len(result["checks"]), "recorded observations")
