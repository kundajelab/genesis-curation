"""Prepare explicit reference comparisons for the existing Genesis method runner."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from genesis_curation.benchmark.reads import sample
from genesis_curation.benchmark.references import prepare_reference
from genesis_curation.benchmark.spec import sha256, write_json


def sample_cohort(catalog: Path, cohort: str, sources: Path, out: Path) -> dict[str, Any]:
    document = json.loads(catalog.read_text())
    if document.get("schema_version") != 1:
        raise ValueError("Unsupported cohort catalog")
    info = document["cohorts"][cohort]
    state = json.loads((sources / "acquisition-state.json").read_text())["assets"]
    selected = {}
    for lib in info["libraries"]:
        records = []
        for name in lib["reads"]:
            record = state.get(name)
            if not record or not record["verified"]:
                raise ValueError("Complete source not verified: " + name)
            path = sources / name
            if sha256(path) != record["sha256"]:
                raise ValueError("Source bytes changed: " + name)
            records.append(path)
        receipt = out / lib["id"] / "sampling.json"
        if receipt.exists():
            value = json.loads(receipt.read_text())
            expected = {str(p.resolve()): state[p.name]["sha256"] for p in records}
            if (
                value["sources"] != expected
                or value["seed"] != 60141
                or set(value["samples"]) != {"1000000", "5000000"}
            ):
                raise ValueError("Sampling identity changed")
            for subset in value["samples"].values():
                for asset in subset["files"]:
                    if sha256(Path(asset["path"])) != asset["sha256"]:
                        raise ValueError("Sampled reads changed")
        else:
            receipt = sample(records, receipt.parent, depths=[1_000_000, 5_000_000], seed=60141)
            value = json.loads(receipt.read_text())
        selected[lib["id"]] = value
        print(lib["id"], value["input_templates"], "validated templates", flush=True)
    return {"catalog": info, "selections": selected, "catalog_sha256": sha256(catalog)}


def new_dataset(record: dict[str, Any], cohort: str, depth: int) -> dict[str, Any]:
    info = record["catalog"]
    assets, libraries = {}, []
    for lib in info["libraries"]:
        subset = record["selections"][lib["id"]]["samples"][str(depth)]
        value = {k: lib[k] for k in ("id", "control", "layout", "biological_replicate")}
        value["raw_templates"] = subset["templates"]
        value["assay_subtype"] = info["assay"]
        metadata = lib.get("metadata") or {}
        value["tf"] = metadata.get("characteristics", {}).get("tf id", "unknown")
        for mate, asset in enumerate(subset["files"], 1):
            name = f"{lib['id']}-R{mate}"
            assets[name] = asset
            value[f"read{mate}"] = name
        libraries.append(value)
    data = {
        "schema_version": 1,
        "id": f"{cohort}-depth-{depth}",
        "assay": info["assay"],
        "synthetic": False,
        "status": "READY",
        "assets": assets,
        "libraries": libraries,
        "thresholds": "UNSPECIFIED",
        "description": "Seeded public-library subsets; explicit reference comparison",
        "source": {
            "selection": info["selection"],
            "catalog_sha256": record["catalog_sha256"],
            "libraries": info["libraries"],
            "sampling_receipts": record.get("sampling_receipts", {}),
            "source_derivations": record.get("source_derivations", {}),
            "reference_role": "explicit comparison; not original-paper reproduction",
        },
    }
    if info["assay"] == "bulk-ATAC":
        data["source"]["replicate_pairs"] = info["replicate_pairs"]
        data["source"]["replicate_evidence"] = info["replicate_evidence"]
    return data


def selected_cohort(catalog: Path, cohort: str, sampling: Path) -> dict[str, Any]:
    """Reuse complete, immutable sampling receipts, including explicit SRA derivations."""
    document = json.loads(catalog.read_text())
    if document.get("schema_version") != 1:
        raise ValueError("Unsupported cohort catalog")
    info = document["cohorts"][cohort]
    selected, receipts, derivations = {}, {}, {}
    for library in info["libraries"]:
        run = library["id"]
        receipt = sampling / run / "sampling.json"
        value = json.loads(receipt.read_text())
        if (
            value["schema_version"] != 1
            or value["seed"] != 60141
            or set(value["samples"]) != {"1000000", "5000000"}
        ):
            raise ValueError("Unsupported sampling selection")
        for subset in value["samples"].values():
            if len(subset["files"]) != (2 if library["layout"] == "PE" else 1):
                raise ValueError("Sample layout does not match catalog")
            for asset in subset["files"]:
                if sha256(Path(asset["path"])) != asset["sha256"]:
                    raise ValueError("Sampled FASTQ changed")
        sra = any(Path(path).suffix == ".sra" for path in value["sources"])
        if sra:
            derivation = receipt.parent / "derivation.json"
            record = json.loads(derivation.read_text())
            if record["status"] != "COMPLETE" or record["sampling_sha256"] != sha256(receipt):
                raise ValueError("SRA derivation incomplete or stale")
            derivations[run] = {"sha256": sha256(derivation), **record}
        selected[run], receipts[run] = value, sha256(receipt)
    return {
        "catalog": info,
        "catalog_sha256": sha256(catalog),
        "selections": selected,
        "sampling_receipts": receipts,
        "source_derivations": derivations,
    }


def prepare(
    dataset: dict[str, Any],
    panel: Path,
    reference: str,
    out: Path,
    environment: Path,
    methods: list[str],
    repetitions: int,
    cpus: int,
) -> Path:
    if out.exists():
        raise FileExistsError("Choose a new experiment directory")
    if not 1 <= cpus <= 14 or not 1 <= repetitions <= 3:
        raise ValueError("Use 1–14 CPUs and 1–3 repetitions for this panel")
    bundle = prepare_reference(panel, reference, out / "reference")
    data = copy.deepcopy(dataset)
    read_ids = {lib[k] for lib in data["libraries"] for k in ("read1", "read2") if k in lib}
    data["assets"] = {k: a for k, a in data["assets"].items() if k in read_ids} | bundle["assets"]
    data["reference"] = bundle["reference"]
    atac = data["assay"] == "bulk-ATAC"
    if atac:
        if not data["reference"]["mitochondrial"] or not data["reference"]["plastid"]:
            raise ValueError("This ATAC reference comparison requires declared Mt and Pt sequences")
        if "tss" not in data["reference"]:
            raise ValueError("This ATAC reference comparison requires a compatible annotation")
        data["reference"]["effective_genome_size"] = bundle["nuclear_acgt"]
    data["id"] += "-" + reference
    data["source"] = dict(
        data["source"],
        reference_bundle={
            "entry_identity": bundle["entry_identity"],
            "assembly_accession": bundle["assembly_accession"],
            "source": bundle["source"],
            "sequence_set_sha256": bundle["scan"]["sequence_set_sha256"],
        },
    )
    baseline = {
        "id": "baseline",
        "aligner": "bwa-mem2",
        "trimmer": "none",
        "duplicate_tool": "samtools" if atac else "none",
        "caller": "macs3",
        "adapter_r1": "-",
        "adapter_r2": "-",
    }
    candidates = {"baseline": baseline}
    for key, names in (
        ("aligner", ["bwa", "bowtie2"]),
        ("caller", ["macs2"] + (["genrich"] if atac else [])),
        ("duplicate_tool", ["picard"] if atac else []),
    ):
        candidates.update({name: {**baseline, "id": name, key: name} for name in names})
    if atac:
        candidates.update(
            {
                name: {
                    **baseline,
                    "id": name,
                    "trimmer": name,
                    "adapter_r1": "CTGTCTCTTATACACATCT",
                    "adapter_r2": "CTGTCTCTTATACACATCT",
                }
                for name in ("cutadapt", "fastp")
            }
        )
    if (
        "baseline" not in methods
        or len(set(methods)) != len(methods)
        or set(methods) - candidates.keys()
    ):
        raise ValueError("Choose unique supported methods including baseline")
    identity_path = (
        Path(__file__).parent.parent
        / "plant-methods-20261013/sources/production-aligner-identity.json"
    )
    expected = json.loads(identity_path.read_text())["production"]["binaries"]
    for name, digest in expected.items():
        if sha256(environment / ".pixi/envs/default/bin" / name) != digest:
            raise ValueError("Baseline binary differs from production: " + name)
    write_json(out / "dataset.json", data)
    experiment = {
        "schema_version": 2,
        "id": data["id"],
        "assay": data["assay"],
        "adapter": "methods",
        "dataset": "dataset.json",
        "baseline": "baseline",
        "comparison": "matched-stage",
        "seed": 60141,
        "methods": [candidates[name] for name in methods],
        "environment": {
            "prefix": str(environment / ".pixi/envs/default"),
            "legacy_prefix": str(environment / ".pixi/envs/macs2"),
            "lock": str(environment / "pixi.lock"),
            "lock_sha256": sha256(environment / "pixi.lock"),
        },
        "parameters": {
            "mapq": [30 if atac else 0],
            "duplicates": ["excluded" if atac else "retained"],
            "keep_dup": "all" if atac else "1",
            "qvalue": 0.01 if atac else 0.05,
            "spp": False,
            "quantify": False,
        },
        "performance": {"repetitions": repetitions, "random_order": True},
        "limits": {
            "cpus": cpus,
            "memory_gb": 80,
            "seconds": 21600,
            "disk_bytes": 100_000_000_000,
            "download_bytes": 0,
            "max_arms": len(methods) * repetitions,
        },
    }
    target = out / "experiment.json"
    write_json(target, experiment)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--base-plan",
        type=Path,
        help="A completed run's experiment.json; reuse the same sampled reads",
    )
    source.add_argument("--cohorts", type=Path)
    source.add_argument(
        "--preselected",
        type=Path,
        help="Cohort catalog with completed --sampling receipts",
    )
    parser.add_argument("--cohort")
    parser.add_argument("--sources", type=Path)
    parser.add_argument("--sampling", type=Path)
    parser.add_argument("--depth", type=int, choices=(1_000_000, 5_000_000), default=1_000_000)
    parser.add_argument("--reference-panel", type=Path, required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--method", action="append", default=None)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--cpus", type=int, default=14)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.base_plan:
        plan = json.loads(args.base_plan.read_text())
        dataset = plan["dataset"]
        dataset["assets"] = plan["assets"]
    elif args.preselected:
        if not all((args.cohort, args.sampling)):
            parser.error("Preselected preparation requires --cohort and --sampling")
        record = selected_cohort(args.preselected, args.cohort, args.sampling.resolve())
        dataset = new_dataset(record, args.cohort, args.depth)
    else:
        if not all((args.cohort, args.sources, args.sampling)):
            parser.error("Cohort preparation requires --cohort, --sources and --sampling")
        record = sample_cohort(
            args.cohorts, args.cohort, args.sources.resolve(), args.sampling.resolve()
        )
        dataset = new_dataset(record, args.cohort, args.depth)
    print(
        prepare(
            dataset,
            args.reference_panel.resolve(),
            args.reference,
            args.out.resolve(),
            args.environment.resolve(),
            args.method or ["baseline"],
            args.repetitions,
            args.cpus,
        )
    )
