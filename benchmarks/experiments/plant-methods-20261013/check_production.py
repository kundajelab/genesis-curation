"""Exercise equivalence, differing peak populations and stale comparison inputs."""

from __future__ import annotations

import json
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from compare_production import compare
from genesis_curation.benchmark.spec import sha256


def write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        native, scores, genesis = (root / name for name in ("native", "scores", "genesis"))
        recipe = {"benchmark/metrics.py": "a" * 64, "atac/fragments.py": "b" * 64}
        value: dict[str, Any] = {
            "layout": "PE",
            "fragment_definition": "test outer span",
            "mapq_distribution": {"60": 2},
            "fragment_length_distribution": {"100": 1},
            "peak_regions": [["chr1", 10, 110]],
            "peak_widths": [100],
            "peak_scores": [12.5],
            "tss": {"profile": [1, 5, 1], "usable_TSS": 1},
            "counts": {"fragments": 1, "fragments_in_fixed_peaks": 1},
            "fixed_regions": [["chr1", 10, 110]],
            "fixed_counts": [1],
        }
        manifest: dict[str, Any] = {
            "libraries": [
                {
                    "library_id": "sample-A",
                    "lanes": [{"read1_sha256": "1" * 64, "read2_sha256": "2" * 64}],
                    "reference": {"fasta_sha256": "3" * 64, "tss_sha256": "4" * 64},
                }
            ]
        }
        write(genesis / "manifest.json", manifest)
        common: dict[str, Any] = {
            "source_records": {str(genesis / "manifest.json"): sha256(genesis / "manifest.json")},
            "recipe": {"recipe_sha256": recipe},
            "observations": {"genesis": {"sample-A": value}},
        }
        write(scores / "results.json", common)
        plan: dict[str, Any] = {
            "experiment_id": "explicit-software-fixture",
            "spec": {
                "assay": "bulk-ATAC",
                "adapter": "methods",
                "baseline": "baseline",
                "performance": {"repetitions": 1},
            },
            "source": {"recipe_sha256": recipe},
            "dataset": {
                "libraries": [{"id": "sample-A", "layout": "PE", "read1": "R1", "read2": "R2"}],
                "reference": {"fasta": "fasta", "tss": "tss"},
            },
            "assets": {
                name: {"sha256": str(i) * 64}
                for i, name in enumerate(("R1", "R2", "fasta", "tss"), 1)
            },
        }
        write(native / "experiment.json", plan)
        result: dict[str, Any] = {
            "status": "COMPLETE",
            "results": [
                {
                    "arm": "baseline",
                    "repetition": 1,
                    "status": "COMPLETE",
                    "libraries": {"sample-A": deepcopy(value)},
                }
            ],
        }
        result["results"][0]["libraries"]["sample-A"]["counts"]["organellar_read_ends"] = 0
        write(native / "results.json", result)
        matched = compare(native, scores, genesis, root / "equal.json")
        assert matched["status"] == "EQUAL"
        changed = deepcopy(result)
        changed["results"][0]["libraries"]["sample-A"]["fixed_regions"][0][1] = 20
        write(native / "results.json", changed)
        different = compare(native, scores, genesis, root / "different.json")
        assert different["status"] == "DIFFERENT"
        assert any(
            row["observation"] == "counts/fragments_in_fixed_peaks"
            and row["status"] == "NOT_COMPARABLE"
            for row in different["checks"]
        )
        assert not any(row["observation"] == "fixed_counts" for row in different["checks"])
        write(native / "results.json", result)
        changed_plan = deepcopy(plan)
        changed_plan["assets"]["R1"]["sha256"] = "9" * 64
        write(native / "experiment.json", changed_plan)
        for filename, change_manifest in (
            ("wrong-input.json", False),
            ("stale-manifest.json", True),
        ):
            if change_manifest:
                write(native / "experiment.json", plan)
                manifest["libraries"][0]["lanes"][0]["read1_sha256"] = "8" * 64
                write(genesis / "manifest.json", manifest)
            try:
                compare(native, scores, genesis, root / filename)
            except ValueError:
                pass
            else:
                raise AssertionError("Different/stale inputs were accepted")
            assert not (root / filename).exists()
    print("PASS equal observations, differing regions, sparse counters and stale input rejection")


if __name__ == "__main__":
    main()
