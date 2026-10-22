"""Prepare the declared four-species extension as verified samples become available."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from genesis_curation.benchmark.spec import sha256, write_json
from prepare import new_dataset, prepare, selected_cohort


def compile_panel(catalog: Path, sampling: Path, panel: Path, environment: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=False)
    document = json.loads(catalog.read_text())
    snapshots = Path(__file__).parent.parent / "plant-methods-20261013/sources"
    runs = ["SRR27443454", "SRR27443453"]
    document["cohorts"]["atac-maize"] = {
        "assay": "bulk-ATAC",
        "species": "Zea_mays",
        "cultivar": "B73",
        "selection": "Two biological replicates: B73, 10-day seedlings, 2500 nuclei",
        "libraries": [
            {
                "id": run,
                "layout": "PE",
                "control": None,
                "biological_replicate": str(index + 1),
                "ena": json.loads((snapshots / (run + ".json")).read_text())[0],
            }
            for index, run in enumerate(runs)
        ],
        "replicate_pairs": [runs],
        "replicate_evidence": {
            "article": "https://doi.org/10.3389/fpls.2024.1370618",
            "sample_snapshots": {
                name: sha256(snapshots / name) for name in ("SAMN39276717.xml", "SAMN39276718.xml")
            },
            "context": (
                "B73, 10-day seedlings, 2500 nuclei, ATAC-seq, rep1/rep2; "
                "publication gDNA control omitted"
            ),
        },
    }
    expanded = out / "cohorts.json"
    write_json(expanded, document)
    jobs = []
    for depth in (1_000_000, 5_000_000):
        for cohort, references in (
            ("dap-lyrata", ["lyrata-phytozome2.1", "lyrata-ensembl19"]),
            ("dap-sorghum", ["sorghum-rtx430-v2", "sorghum-sorbi1", "sorghum-btx623-v5"]),
            ("atac-maize", ["maize-agpv4", "maize-nam5-common-organelles"]),
        ):
            for reference in references:
                name = f"{cohort}-{reference}-{depth}"
                jobs.append(
                    {
                        "id": name,
                        "cohort": cohort,
                        "reference": reference,
                        "depth": depth,
                        "methods": ["baseline"],
                        "repetitions": 1,
                    }
                )
    # First cover every species/reference. Method timing repetitions are a separate block.
    for depth in (1_000_000, 5_000_000):
        for cohort, reference in (
            ("dap-lyrata", "lyrata-phytozome2.1"),
            ("dap-sorghum", "sorghum-rtx430-v2"),
            ("atac-maize", "maize-agpv4"),
        ):
            groups = {"methods": ["baseline", "bwa", "bowtie2", "macs2"]}
            if cohort == "atac-maize":
                groups = {
                    "mapping": ["baseline", "bwa", "bowtie2"],
                    "processing": ["baseline", "macs2", "genrich", "picard", "cutadapt", "fastp"],
                }
            for group, methods in groups.items():
                jobs.append(
                    {
                        "id": f"{cohort}-{group}-{depth}",
                        "cohort": cohort,
                        "reference": reference,
                        "depth": depth,
                        "methods": methods,
                        "repetitions": 3 if depth == 1_000_000 else 1,
                    }
                )
    for job in jobs:
        job["experiment"] = str(out / job["id"] / "experiment.json")
        job["results"] = str(out / (job["id"] + "-results"))
        job["archive_indexes"] = len(job["methods"]) == 1
        job["archive_work"] = len(job["methods"]) > 1
    write_json(out / "queue.json", jobs)
    deadline = time.monotonic() + 7 * 86400
    pending = list(jobs)
    status = {}
    while pending:
        if time.monotonic() >= deadline:
            raise TimeoutError("Seven-day preparation limit reached")
        for job in list(pending):
            info = document["cohorts"][job["cohort"]]
            if not all(
                (sampling / lib["id"] / "sampling.json").exists()
                and (
                    (sampling / lib["id"] / "derivation.json").exists()
                    or lib["id"] == "SRR27443454"
                )
                for lib in info["libraries"]
            ):
                status[job["id"]] = "WAITING_FOR_VALIDATED_READS"
                continue
            record = selected_cohort(expanded, job["cohort"], sampling)
            dataset = new_dataset(record, job["cohort"], job["depth"])
            path = prepare(
                dataset,
                panel,
                job["reference"],
                out / job["id"],
                environment,
                job["methods"],
                job["repetitions"],
                14,
            )
            status[job["id"]] = "PREPARED"
            pending.remove(job)
            print(path, flush=True)
            write_json(out / "preparation.json", status)
        write_json(out / "preparation.json", status)
        if pending:
            time.sleep(30)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--sampling", type=Path, required=True)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    compile_panel(
        args.catalog.resolve(),
        args.sampling.resolve(),
        args.panel.resolve(),
        args.environment.resolve(),
        args.out.resolve(),
    )
