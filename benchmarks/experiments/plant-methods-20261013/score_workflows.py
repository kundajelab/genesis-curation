"""Compare completed Genesis and nf-core outputs with the same metric definitions.

The workflows retain their actual filtering/calling policies. Genesis peaks are
the fixed evaluation regions, not ground truth. No pipeline is rerun or changed.
"""

from __future__ import annotations

import argparse
import csv
import json
import tempfile
from pathlib import Path
from typing import Any

from genesis_curation.benchmark.metrics import assess, compare, tss_enrichment
from genesis_curation.benchmark.spec import sha256, source_identity


def score(genesis: Path, nfcore: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=False)
    production = json.loads((genesis / "provenance.json").read_text())
    if production["exit_code"] != 0:
        raise ValueError("Genesis did not finish successfully")
    original = json.loads((nfcore / "results.json").read_text())
    if original["status"] != "COMPLETE" or len(original["results"]) != 1:
        raise ValueError("Expected one complete nf-core repetition")
    plan = json.loads((nfcore / "experiment.json").read_text())
    manifest = json.loads((genesis / "manifest.json").read_text())
    metadata = {lib["library_id"]: lib for lib in manifest["libraries"]}
    libraries = {lib["id"]: lib for lib in plan["dataset"]["libraries"]}
    if metadata.keys() != libraries.keys():
        raise ValueError("Workflow library identities differ")
    with (genesis / "output/trace.tsv").open() as stream:
        trace = list(csv.DictReader(stream, delimiter="\t"))
    if any(row["status"] != "COMPLETED" for row in trace):
        raise ValueError("This comparison requires a successful fresh Genesis run")
    ref = plan["dataset"]["reference"]
    sizes = {
        c: int(n)
        for c, n in (
            line.split("\t")
            for line in Path(plan["assets"][ref["chrom_sizes"]]["path"]).read_text().splitlines()
        )
    }
    tss = Path(plan["assets"][ref["tss"]]["path"])
    observations: dict[str, dict[str, Any]] = {"genesis": {}, "nfcore": {}}
    inputs = {}
    for name, library in libraries.items():
        meta = metadata[name]
        if len(meta["lanes"]) != 1:
            raise ValueError("This panel compares one prepared input per library")
        for mate in ("read1", "read2"):
            if meta["lanes"][0][mate + "_sha256"] != plan["assets"][library[mate]]["sha256"]:
                raise ValueError("Workflow read bytes differ")
        if meta["reference"]["fasta_sha256"] != plan["assets"][ref["fasta"]]["sha256"] or meta[
            "reference"
        ]["tss_sha256"] != sha256(tss):
            raise ValueError("Workflow reference or annotation differs")
        tasks = [row for row in trace if row["name"] == f"ATAC_FRAGMENTS ({name})"]
        if len(tasks) != 1:
            raise ValueError("Expected one Genesis fragment task per library")
        selected = Path(tasks[0]["workdir"]) / "usable.bam"
        peaks = genesis / "output" / name / "peaks/atac_peaks.narrowPeak"
        nf = original["results"][0]["libraries"][name]
        native = json.loads((genesis / "output" / name / "qc/enrichment.json").read_text())
        for workflow, bam, called in (
            ("genesis", selected, peaks),
            ("nfcore", Path(nf["artifact_roles"]["bam"]), Path(nf["artifact_roles"]["peaks"])),
        ):
            if workflow == "nfcore" and any(
                sha256(p) != nf["artifacts"][str(p)] for p in (bam, called)
            ):
                raise ValueError("nf-core output changed after assessment")
            inputs[f"{workflow}/{name}"] = {str(p): sha256(p) for p in (bam, called)}
            with tempfile.TemporaryDirectory(dir=out) as temporary:
                value = assess(
                    bam,
                    called,
                    peaks,
                    layout="PE",
                    raw_templates=library["raw_templates"],
                    organelles=set(ref["mitochondrial"] + ref["plastid"]),
                    temporary=Path(temporary),
                )
                value["tss"] = tss_enrichment(bam, tss, sizes, Path(temporary))
            if workflow == "genesis" and (
                value["counts"]["fragments"] != native["usable_fragments"]
                or value["metrics"]["fragments_FRiP_own"]["value"] != native["FRiP"]
                or value["tss"] != native["tss"]
            ):
                raise ValueError("Common assessment disagrees with production outputs")
            value["comparison"] = compare(value, observations["genesis"].get(name, value))
            observations[workflow][name] = value
    rows = []
    for workflow, measured in observations.items():
        for name, value in measured.items():
            metrics = value["metrics"] | value["comparison"]
            metrics["common_TSS_enrichment"] = value["tss"]["metric"]
            for key, measurement in metrics.items():
                rows.append({"workflow": workflow, "library": name, "metric": key} | measurement)
    with (out / "metrics.tsv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    evidence = {
        "schema_version": 1,
        "comparison": "Different configured workflows; fixed Genesis regions are not truth",
        "replication": "One workflow execution per library; no performance ranking",
        "observations": observations,
        "inputs": inputs,
        "recipe": source_identity(),
        "script_sha256": sha256(Path(__file__)),
        "source_records": {
            str(p): sha256(p)
            for p in (
                genesis / "provenance.json",
                genesis / "manifest.json",
                nfcore / "experiment.json",
                nfcore / "results.json",
                tss,
            )
        },
    }
    (out / "results.json").write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    print(out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--genesis-run", type=Path, required=True)
    parser.add_argument("--nfcore-run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    score(args.genesis_run.resolve(), args.nfcore_run.resolve(), args.out.resolve())
