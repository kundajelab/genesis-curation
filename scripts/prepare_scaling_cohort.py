#!/usr/bin/env python3
"""Freeze a staged experiment-level cohort from accession-only reference seeds.

This is accession reconstruction, not independent discovery. External annotations
choose strata only; model inputs contain newly retrieved archive evidence alone.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlencode

from genesis_curation.contracts.records import canonical, dump, fingerprint
from genesis_curation.registry import store
from genesis_curation.web import catalog, intake

STRATA = {
    "TSS": ("TSS-Fable-Curated.json", 30),
    "Bulk ATAC": ("Bulk-ATAC-Fable-Curated.json", 40),
    "scATAC": ("scATAC-Fable-Curated.json", 30),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--size", type=int, choices=(100, 1000), default=100)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=args.resume)
    if (args.output / "sealed.json").exists():
        raise ValueError("Cohort is sealed; do not change a frozen evaluation")
    store.initialize(args.output / "catalog")
    selected, seen, coverage = [], set(), {}
    for modality, (filename, count) in STRATA.items():
        count *= args.size // 100
        rows = json.loads((args.reference / filename).read_text())
        candidates = []
        gsa = 0
        for row in rows:
            key = row.get("experiment_accession") or row.get("sra_experiment", "")
            if not re.fullmatch(r"[SED]RX\d+", key):
                gsa += 1
                continue
            candidates.append((row.get("species", ""), key))
        # Rotate across source-reported species for coverage; never copy their labels into evidence.
        candidates.sort(
            key=lambda r: hashlib.sha256(("genesis-scale-v1:" + r[1]).encode()).hexdigest()
        )
        chosen, taxa = [], set()
        for unique_species in (True, False):
            for species, key in candidates:
                if key in seen or (unique_species and species in taxa) or len(chosen) == count:
                    continue
                chosen.append({"accession": key, "stratum": modality})
                seen.add(key)
                taxa.add(species)
        selected.extend(chosen)
        coverage[modality] = {
            "selected": len(chosen),
            "requested": count,
            "reference_species": len(taxa),
            "non_INSDC_rows_not_verified": gsa,
        }
        if len(chosen) != count:
            raise ValueError(
                f"Insufficient distinct archive experiments for {modality}: {len(chosen)}/{count}"
            )
    selection_path = args.output / "selection.json"
    if selection_path.exists():
        previous_selection = json.loads(selection_path.read_text())
        if previous_selection["seeds"] != selected or previous_selection["coverage"] != coverage:
            raise ValueError(
                "Resume must preserve the original reference selection and cohort size"
            )
    dump(
        selection_path,
        {
            "mode": "accession-reconstruction",
            "seeds": selected,
            "coverage": coverage,
            "reference_labels_in_model_input": False,
        },
    )
    previous = args.output / "frozen.json"
    samples = (
        json.loads(previous.read_text())["samples"] if args.resume and previous.exists() else []
    )
    completed = {sample["state"]["name"] for sample in samples}
    failures = []
    frozen = json.loads(previous.read_text()) if previous.exists() else {}
    for seed in selected:
        key = seed["accession"]
        if key in completed:
            continue
        try:
            url = "https://www.ebi.ac.uk/ena/portal/api/filereport?" + urlencode(
                {
                    "accession": key,
                    "result": "read_run",
                    "format": "json",
                    "fields": (
                        "run_accession,sample_accession,study_accession,experiment_accession,"
                        "scientific_name,tax_id,library_strategy,library_layout,sample_title"
                    ),
                }
            )
            source = intake.fetch(args.output, url)
            runs = json.loads(source["text"])
            if not runs or any(r["experiment_accession"] != key for r in runs):
                raise ValueError("Archive did not return the exact experiment identity")
            for field in (
                "sample_accession",
                "study_accession",
                "scientific_name",
                "library_strategy",
                "library_layout",
            ):
                if len({r[field] for r in runs}) != 1:
                    raise ValueError("Conflicting run-level " + field)
            row = {**runs[0], "run_accessions": sorted({r["run_accession"] for r in runs})}
            row.pop("run_accession", None)
            sid = catalog.import_rows(
                args.output / "catalog",
                canonical([row]),
                key + ".json",
                "scale-reconstruction-v1",
                "experiment_accession",
                "public",
                "archive-import",
            )[0]
            for raw in [source] + [
                intake.archive_xml(args.output, acc) for acc in (key, row["sample_accession"])
            ]:
                intake.attach(
                    args.output / "catalog",
                    sid,
                    raw["text"],
                    raw["url"],
                    "sample",
                    "archive-import",
                )
            state = catalog.get(args.output / "catalog", sid)
            samples.append(
                {
                    "id": sid,
                    "state": state,
                    "study": row["study_accession"],
                    "split": "evaluation",
                    "stratum": seed["stratum"],
                    "task_fields": [],
                }
            )
            print(key, "FROZEN", flush=True)
        except (ValueError, OSError) as error:
            failures.append({**seed, "error": str(error)})
            print(key, "FAILED", str(error), flush=True)
        frozen = {
            "version": 1,
            "mode": "accession-reconstruction",
            "samples": samples,
            "coverage": coverage,
            "failures": failures,
            "labels": "AWAITING_HUMAN_LABELS",
            "reference_labels_in_model_input": False,
        }
        frozen["manifest_hash"] = fingerprint(frozen)
        dump(args.output / "frozen.json", frozen)
    if len(samples) == args.size and not failures:
        dump(args.output / "sealed.json", frozen, immutable=True)
    print(json.dumps({"frozen": len(samples), "failed": len(failures), "coverage": coverage}))


if __name__ == "__main__":
    main()
