"""Audit caller behavior and biological-replicate IDR on a completed ATAC baseline.

These are additional, explicitly labelled configurations. They do not replace
the original comparison's calls or change the production analysis policy.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from genesis_curation.benchmark.metrics import assess, intervals
from genesis_curation.benchmark.spec import replicate_pairs, sha256, write_json


def diagnose(results: Path, environment: Path, auxiliary: Path, out: Path) -> None:
    plan = json.loads((results / "experiment.json").read_text())
    measured = json.loads((results / "results.json").read_text())
    if measured["status"] != "COMPLETE" or plan["dataset"]["assay"] != "bulk-ATAC":
        raise ValueError("A completed ATAC baseline is required")
    cases = [r for r in measured["results"] if r["arm"] == "baseline" and r["repetition"] == 1]
    if len(cases) != 1:
        raise ValueError("Expected one baseline repetition")
    case, reference = cases[0], plan["dataset"]["reference"]
    out.mkdir(parents=True, exist_ok=False)
    events, diagnostic, relaxed = [], {}, {}
    executables = {name: environment / "bin" / name for name in ("samtools", "Genrich", "macs3")}
    executables["idr"] = auxiliary / "bin/idr"

    def command(argv: list[str], folder: Path, label: str) -> None:
        start = time.monotonic()
        with (
            (folder / (label + ".stdout")).open("w") as stdout,
            (folder / (label + ".stderr")).open("w") as stderr,
        ):
            completed = subprocess.run(
                argv,
                stdout=stdout,
                stderr=stderr,
                timeout=1800,
                env=dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1"),
            )
        events.append(
            {"argv": argv, "seconds": time.monotonic() - start, "exit_status": completed.returncode}
        )
        write_json(out / "commands.json", events)
        if completed.returncode:
            raise RuntimeError("Caller diagnostic command failed: " + label)

    for library in plan["dataset"]["libraries"]:
        name = library["id"]
        bam, peaks = Path(case["bams"][name]), Path(case["peaks"][name])
        artifact = case["libraries"][name]["artifacts"]
        if sha256(bam) != artifact[str(bam)] or sha256(peaks) != artifact[str(peaks)]:
            raise ValueError("Baseline artifacts changed")
        folder = out / name
        folder.mkdir()
        names = folder / "names.bam"
        command(
            [str(executables["samtools"]), "sort", "-n", "-m", "128M", "-o", str(names), str(bam)],
            folder,
            "name-sort",
        )
        entries = {}
        for label, threshold in (
            ("genrich-q0.01", ["-q", "0.01"]),
            ("genrich-native-p0.01", ["-p", "0.01"]),
        ):
            target = folder / (label + ".narrowPeak")
            argv = [
                str(executables["Genrich"]),
                "-t",
                str(names),
                "-o",
                str(target),
                "-j",
                *threshold,
                "-v",
            ]
            if label == "genrich-q0.01":
                argv += ["-f", str(folder / "genrich-pq.tsv")]
            command(argv, folder, label)
            with tempfile.TemporaryDirectory(dir=folder, prefix="assessment-") as scratch:
                entries[label] = assess(
                    bam,
                    target,
                    peaks,
                    layout="PE",
                    raw_templates=library["raw_templates"],
                    organelles=set(reference["mitochondrial"] + reference["plastid"]),
                    temporary=Path(scratch),
                )
            entries[label]["output_sha256"] = sha256(target)
            entries[label]["scientific_policy"] = (
                "Additional caller diagnostic, not a production policy or QC decision"
            )
        names.unlink()
        target = folder / "relaxed_peaks.narrowPeak"
        command(
            [
                str(executables["macs3"]),
                "callpeak",
                "-t",
                str(bam),
                "-f",
                "BAMPE",
                "-g",
                str(reference["effective_genome_size"]),
                "--keep-dup",
                "all",
                "-p",
                "0.01",
                "-n",
                "relaxed",
                "--outdir",
                str(folder),
            ],
            folder,
            "macs3-relaxed-for-idr",
        )
        relaxed[name] = target
        diagnostic[name] = entries
    idr = []
    for first, second in replicate_pairs(plan["dataset"]):
        counts = [len(intervals(relaxed[n])[0]) for n in (first, second)]
        record: dict[str, Any] = {
            "libraries": [first, second],
            "input_peak_counts": counts,
            "threshold": 0.05,
            "input_policy": (
                "Separate MACS3 BAMPE calls at p=0.01, same usable BAMs "
                "and genome size; signal.value ranking"
            ),
        }
        if min(counts) < 20:
            record.update(status="NOT_EVALUATED", reason="Fewer than 20 input peaks in a replicate")
        else:
            target = out / (first + "-" + second + ".idr.tsv")
            command(
                [
                    str(executables["idr"]),
                    "--samples",
                    str(relaxed[first]),
                    str(relaxed[second]),
                    "--input-file-type",
                    "narrowPeak",
                    "--rank",
                    "signal.value",
                    "--output-file",
                    str(target),
                    "--log-output-file",
                    str(target.with_suffix(".log")),
                    "--soft-idr-threshold",
                    "0.05",
                    "--random-seed",
                    "60141",
                ],
                out,
                first + "-" + second + "-idr",
            )
            rows = [line.split("\t") for line in target.read_text().splitlines() if line]
            scores = [float(row[11]) for row in rows]
            if any(not math.isfinite(v) or v < 0 for v in scores):
                raise ValueError("IDR produced invalid scores")
            record.update(
                status="COMPLETE",
                merged_output_regions=len(rows),
                global_idr_at_most_0_05=sum(v >= -math.log10(0.05) for v in scores),
                output_sha256=sha256(target),
            )
        idr.append(record)
    write_json(
        out / "diagnostics.json",
        {
            "schema_version": 1,
            "status": "COMPLETE",
            "experiment_id": plan["experiment_id"],
            "result_sha256": sha256(results / "results.json"),
            "script_sha256": sha256(Path(__file__)),
            "tools": {name: sha256(path) for name, path in executables.items()},
            "environment_locks": {
                "methods": sha256(environment.parents[2] / "pixi.lock"),
                "auxiliary": sha256(auxiliary.parents[2] / "pixi.lock"),
            },
            "caller_diagnostics": diagnostic,
            "biological_replicate_idr": idr,
            "interpretation": (
                "Exploratory pairwise IDR; no pooled/pseudoreplicate rescue ratios, "
                "no plant exclusion threshold. Genrich native p-values and q-values "
                "are different operating points, not matched FDR."
            ),
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--auxiliary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    diagnose(
        args.results.resolve(),
        args.environment.resolve(),
        args.auxiliary.resolve(),
        args.out.resolve(),
    )
