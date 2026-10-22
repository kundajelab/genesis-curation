"""Create a tiny synthetic inspection fixture; no scientific processing or loader run."""

from __future__ import annotations

import argparse
import shutil
from importlib.resources import files
from pathlib import Path
from typing import Any

from genesis_curation.contracts.records import canonical, dump, fingerprint
from genesis_curation.scatac.common import digest
from genesis_curation.scatac.model import MODELS


def fixture(root: Path, target: str = "chrombpnet") -> Path:
    root.mkdir(parents=True, exist_ok=False)
    sizes = {c: 10000 for c in ("chr1", "chr2", "chr3")}
    (root / "genome.fa").write_text("".join(f">{c}\n" + "ACGT" * 2500 + "\n" for c in sizes))
    (root / "chrom.sizes").write_text("".join(f"{c}\t{n}\n" for c, n in sizes.items()))
    with (
        files("genesis_curation.scatac").joinpath("assets/inspection-fixture.bw").open("rb") as source
    ):
        with (root / "signal.bw").open("wb") as dest:
            shutil.copyfileobj(source, dest)
    reference = {
        "reference_id": "synthetic-three-contig-v1",
        "assembly": "SYNTHETIC",
        "species": "synthetic",
        "contigs": sizes,
        "fasta": {"path": "genome.fa", "sha256": digest(root / "genome.fa")},
        "mitochondrial_contigs": [],
        "plastid_contigs": [],
    }
    group = {
        "identity": {
            "study_id": "synthetic-inspection-demo",
            "biological_sample_id": "sample-1",
            "biological_replicate_id": "replicate-1",
            "cell_type": "synthetic-cell-type",
            "library_scope": {"library": "synthetic-library-1"},
            "coordinate_offsets": [4, -5],
            "annotation_sha256": None,
        },
        "reference": reference,
        "library_composition": {"synthetic-library-1": 1},
        "cell_count": 1,
        "signal": {
            "kind": "raw_counts",
            "unit": "cut",
            "normalization": "none",
            "strand": "both",
            "derivation": "chrombpnet-atac-v1; two endpoints per row, not per support",
        },
    }
    config = {
        "schema_version": 1,
        "target": target,
        "model_sha": MODELS[target],
        "input_window": 2114,
        "output_window": 1000,
        "max_jitter": 0,
        "controls": None,
        "folds": {"train": ["chr1"], "validation": ["chr2"], "test": ["chr3"]},
        "background_method": "nonoverlapping-genome-tiles-v1",
    }
    if target == "chrombpnet":
        config["bias_model"] = {
            "status": "NOT_SUPPLIED",
            "requirement": "Review a suitable plant bias model",
        }
    for split, chroms in config["folds"].items():
        for kind, center in (("peaks", 5000), ("background", 2000)):
            (root / f"{split}.{kind}.bed").write_text(
                f"{chroms[0]}\t{center - 1}\t{center + 1}\tsynthetic\t0\t.\t0\t0\t0\t1\n"
            )
    dump(root / "metadata.json", group)
    dump(
        root / "qc.json",
        {"tss_enrichment": None, "scope": "synthetic inspection fixture; no biological QC"},
    )
    (root / "cells.jsonl").write_text(
        canonical(
            {
                "cell_id": "synthetic-cell-1",
                "source": {
                    "library_id": "synthetic-library-1",
                    "barcode": "SYNTHETIC",
                    "harmonized_label": "synthetic-cell-type",
                },
            }
        )
        + "\n"
    )
    copied = ("metadata.json", "qc.json", "cells.jsonl", "signal.bw", "chrom.sizes")
    parent: dict[str, Any] = {
        "schema_version": 1,
        "kind": "scatac-group",
        "data": {
            "group": group,
            "policy": {"signal_convention": "chrombpnet-atac-v1"},
            "review_status": "UNREVIEWED",
            "annotations_sha256": digest(root / "cells.jsonl"),
        },
        "outputs": {name: digest(root / name) for name in copied},
    }
    parent["version"] = fingerprint(parent)
    dump(root / "parent.json", parent)
    dump(root / "config.json", config)
    dump(root / "folds.json", config["folds"])
    dump(
        root / "loader.json",
        {
            **{
                k: config[k]
                for k in (
                    "target",
                    "model_sha",
                    "input_window",
                    "output_window",
                    "max_jitter",
                    "controls",
                )
            },
            "sequences": "genome.fa",
            "signals": ["signal.bw"],
            "signal_groups": [1],
            "summits": True,
            "further_tn5_shift": False,
            "bias_model": config.get("bias_model"),
        },
    )
    value: dict[str, Any] = {
        "schema_version": 1,
        "kind": "scatac-model",
        "data": {
            "parent": parent["version"],
            "config": config,
            "reference": reference,
            "model_ready": False,
            "provenance": {
                "input_scope": "synthetic",
                "description": (
                    "Hand-constructed inspection fixture, not a pipeline or model-loader execution"
                ),
            },
            "peaks_per_fold": {s: 1 for s in config["folds"]},
            "backgrounds_per_fold": {s: 1 for s in config["folds"]},
        },
        "outputs": {p.name: digest(p) for p in root.iterdir()},
    }
    value["version"] = fingerprint(value)
    dump(root / "complete.json", value)
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", choices=tuple(MODELS), default="chrombpnet")
    args = parser.parse_args()
    fixture(args.output, args.target)
    print("Created SYNTHETIC inspection fixture; model_ready=false; no loader or training executed")


if __name__ == "__main__":
    main()
