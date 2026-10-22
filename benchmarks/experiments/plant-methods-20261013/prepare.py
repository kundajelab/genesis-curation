"""Prepare checksummed public DAP/ATAC method comparisons from verified downloads."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from genesis_curation.benchmark.reads import sample
from genesis_curation.benchmark.spec import sha256, write_json

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
COHORTS = {
    "dap-se": ("DAP-seq", "arabidopsis", ["SRR2926068", "SRR2926076", "SRR2926841"]),
    "dap-pe": (
        "DAP-seq",
        "arabidopsis",
        ["SRR31238993", "SRR31238589", "SRR31239194", "SRR31238722", "SRR31238919"],
    ),
    "atac-arabidopsis": ("bulk-ATAC", "arabidopsis", ["SRR4000468", "SRR4000469"]),
    "atac-maize": ("bulk-ATAC", "maize", ["SRR27443454", "SRR27443453"]),
}


def prepare(source: Path, out: Path, cohort: str, depths: list[int]) -> None:
    started = time.monotonic()
    out.mkdir(parents=True, exist_ok=True)
    assay, organism, runs = COHORTS[cohort]
    manifest = json.loads((HERE / "acquisition.json").read_text())
    assets = {a["id"]: a for a in manifest["assets"]}
    # This snapshot permits one completed cohort while later files still download.
    state = json.loads((source / "acquisition-state.json").read_text())["assets"]
    resolved = {}

    def asset(name: str, path: Path, checksum: str | None = None) -> str:
        actual = sha256(path)
        if checksum is not None and actual != checksum:
            raise ValueError("Source hash changed: " + str(path))
        resolved[name] = {
            "path": str(path.resolve()),
            "sha256": actual,
            "bytes": path.stat().st_size,
        }
        return name

    def original(name: str) -> Path:
        if name not in state or not state[name]["verified"]:
            raise ValueError("Source not yet verified: " + name)
        path = source / name
        asset(name, path, state[name]["sha256"])
        return path

    fasta = original(organism + ".fa.gz")
    gtf = original(organism + ".gtf.gz")
    sizes: dict[str, int] = {}
    nuclear = 0
    digest = hashlib.sha256()
    contig = ""
    with gzip.open(fasta, "rb") as stream:
        for line in stream:
            digest.update(line)
            if line.startswith(b">"):
                contig = line[1:].split()[0].decode()
                sizes[contig] = 0
            else:
                seq = line.strip().upper()
                sizes[contig] += len(seq)
                if contig not in {"Mt", "Pt"}:
                    nuclear += sum(seq.count(base) for base in (b"A", b"C", b"G", b"T"))
    if not {"Mt", "Pt"} <= sizes.keys():
        raise ValueError("Declared organelles not found")
    expected_nuclear = 118960141 if organism == "arabidopsis" else 2103640168
    if nuclear != expected_nuclear:
        raise ValueError("Reference composition differs")
    positions = set()
    with gzip.open(gtf, "rt") as stream:
        for line in stream:
            if line.startswith("#"):
                continue
            f = line.rstrip().split("\t")
            if f[2] == "transcript" and f[0] in sizes and f[0] not in {"Mt", "Pt"}:
                positions.add((f[0], int(f[3]) - 1 if f[6] == "+" else int(f[4]) - 1, f[6]))
    tss = out / "tss.bed"
    tss.write_text("".join(f"{c}\t{p}\t{s}\n" for c, p, s in sorted(positions)))
    expected_tss = {
        "arabidopsis": "8262db9fa150f528c6dd0836fcfe329455f26600ac256dd788e4540aa9ab9ff3",
        "maize": "0f8104ad685733ddf54d6c517507cf0e7286bc59ca91a8d7941792b7e81ddd42",
    }
    asset("tss", tss, expected_tss[organism])
    chrom = out / "chrom.sizes"
    chrom.write_text("".join(f"{c}\t{n}\n" for c, n in sizes.items()))
    asset("sizes", chrom)
    reference = {
        "id": organism + "-explicit-reference",
        "species": "Arabidopsis_thaliana" if organism == "arabidopsis" else "Zea_mays",
        "assembly": "TAIR10" if organism == "arabidopsis" else "B73_RefGen_v4",
        "cultivar": "Col-0" if organism == "arabidopsis" else "B73",
        "fasta": fasta.name,
        "fasta_content_sha256": digest.hexdigest(),
        "chrom_sizes": "sizes",
        "genome_size": sum(sizes.values()),
        "mitochondrial": ["Mt"],
        "plastid": ["Pt"],
        "tss": "tss",
        "gtf": gtf.name,
        "annotation_release": "EnsemblPlants59" if organism == "arabidopsis" else "EnsemblPlants46",
    }
    if assay == "bulk-ATAC":
        reference["effective_genome_size"] = nuclear
    selections = {}
    for run in runs:
        names = [name for name in assets if name.startswith(run + "_") or name == run + ".fastq.gz"]
        names.sort()
        if cohort == "dap-se":
            names = names[:1]  # explicit repository R1 projection for control SRR2926068
        reads = [original(name) for name in names]
        sampling = out / run / "sampling.json"
        if sampling.exists():
            record = json.loads(sampling.read_text())
            if (
                record["sources"] != {str(p.resolve()): sha256(p) for p in reads}
                or set(record["samples"]) != set(map(str, depths))
                or record["seed"] != 60141
            ):
                raise ValueError("Sampling inputs changed; use a new preparation directory")
            for value in record["samples"].values():
                for f in value["files"]:
                    if sha256(Path(f["path"])) != f["sha256"]:
                        raise ValueError("Sampled reads changed")
        else:
            sampling = sample(reads, out / run, depths=depths, seed=60141)
            record = json.loads(sampling.read_text())
        selections[run] = record
        print(run, "validated templates", record["input_templates"], flush=True)
    baseline = {
        "id": "baseline",
        "aligner": "bwa-mem2",
        "trimmer": "none",
        "duplicate_tool": "samtools" if assay == "bulk-ATAC" else "none",
        "caller": "macs3",
        "adapter_r1": "-",
        "adapter_r2": "-",
    }
    methods = [
        baseline,
        {**baseline, "id": "bwa", "aligner": "bwa"},
        {**baseline, "id": "bowtie2", "aligner": "bowtie2"},
        {**baseline, "id": "macs2", "caller": "macs2"},
    ]
    if assay == "bulk-ATAC":
        methods += [
            {**baseline, "id": "genrich", "caller": "genrich"},
            {**baseline, "id": "picard", "duplicate_tool": "picard"},
        ]
        methods += [
            {
                **baseline,
                "id": name,
                "trimmer": name,
                "adapter_r1": "CTGTCTCTTATACACATCT",
                "adapter_r2": "CTGTCTCTTATACACATCT",
            }
            for name in ("cutadapt", "fastp")
        ]
    environment = REPO / "benchmarks/environment"
    for depth in depths:
        dataset_assets = {k: v for k, v in resolved.items() if ".fastq" not in k}
        libraries = []
        for i, run in enumerate(runs):
            chosen = selections[run]["samples"][str(depth)]
            lib: dict[str, Any] = {
                "id": run,
                "layout": "SE" if cohort == "dap-se" else "PE",
                "control": runs[0] if assay == "DAP-seq" and i else None,
                "raw_templates": chosen["templates"],
                "biological_replicate": "not_established" if assay == "DAP-seq" else str(i + 1),
                "assay_subtype": assay,
            }
            for mate, f in enumerate(chosen["files"], 1):
                name = run + f"-R{mate}"
                dataset_assets[name] = f
                lib[f"read{mate}"] = name
            libraries.append(lib)
        name = cohort + "-depth-" + str(depth)
        dataset: dict[str, Any] = {
            "schema_version": 1,
            "id": name,
            "assay": assay,
            "synthetic": False,
            "status": "READY",
            "reference": reference,
            "assets": dataset_assets,
            "libraries": libraries,
            "thresholds": "UNSPECIFIED",
            "description": (
                "Seeded nested raw-template sampling, explicit new reference/annotation; "
                "not original-paper reproduction."
            ),
            "source": {
                "acquisition_manifest": str(HERE / "acquisition.json"),
                "sha256": sha256(HERE / "acquisition.json"),
                "sample_metadata": "sources/",
                "control_projection": "SRR2926068 R1 only, matching repository SE sheet"
                if cohort == "dap-se"
                else None,
                "effective_genome_size_method": "nuclear non-N ACGT bases, not mappability"
                if assay == "bulk-ATAC"
                else "sum of all FASTA contig lengths",
            },
        }
        if assay == "bulk-ATAC":
            source_metadata: dict[str, Any] = dict(dataset["source"])
            source_metadata["replicate_pairs"] = [runs]
            source_metadata["replicate_evidence"] = {
                "article": (
                    "https://pmc.ncbi.nlm.nih.gov/articles/PMC5389718/"
                    if organism == "arabidopsis"
                    else "https://doi.org/10.3389/fpls.2024.1370618"
                ),
                "sample_snapshots": {
                    p.name: sha256(p) for p in sorted((HERE / "sources").glob("*.xml"))
                },
                "context": (
                    "Col-0, 7-day whole seedlings, 50000 nuclei, FANS-ATAC, rep1/rep2"
                    if organism == "arabidopsis"
                    else "B73, 10-day seedlings, 2500 nuclei, ATAC-seq, rep1/rep2; "
                    "this comparison omits the publication's gDNA control"
                ),
            }
            dataset["source"] = source_metadata
        write_json(out / (name + ".dataset.json"), dataset)
        spec = {
            "schema_version": 2,
            "id": name,
            "assay": assay,
            "adapter": "methods",
            "dataset": name + ".dataset.json",
            "baseline": "baseline",
            "comparison": "matched-stage",
            "seed": 60141,
            "methods": methods,
            "environment": {
                "prefix": str(environment / ".pixi/envs/default"),
                "legacy_prefix": str(environment / ".pixi/envs/macs2"),
                "lock": str(environment / "pixi.lock"),
                "lock_sha256": sha256(environment / "pixi.lock"),
            },
            "parameters": {
                "mapq": [30 if assay == "bulk-ATAC" else 0],
                "duplicates": ["excluded" if assay == "bulk-ATAC" else "retained"],
                "keep_dup": "all" if assay == "bulk-ATAC" else "1",
                "qvalue": 0.01 if assay == "bulk-ATAC" else 0.05,
                "spp": False,
                "quantify": False,
            },
            "performance": {"repetitions": 1, "random_order": True},
            "limits": {
                "cpus": 16,
                "memory_gb": 96,
                "seconds": 21600,
                "disk_bytes": 80000000000,
                "download_bytes": 0,
                "max_arms": len(methods),
            },
        }
        write_json(out / (name + ".json"), spec)
    write_json(
        out / "preparation.json",
        {
            "cohort": cohort,
            "depths": depths,
            "selections": selections,
            "script_sha256": sha256(Path(__file__)),
            "seconds": time.monotonic() - started,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cohort", choices=COHORTS, required=True)
    parser.add_argument("--depth", type=int, action="append", required=True)
    args = parser.parse_args()
    prepare(args.sources.resolve(), args.out.resolve(), args.cohort, args.depth)
