"""Stage the public ATAC panel for current Genesis and pinned nf-core workflows.

Choose a directory visible to Docker. This prepares files; it does not start a
workflow or download an image. Native component comparisons remain separate.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
from pathlib import Path

from genesis_curation.atac.inputs import libraries, references
from genesis_curation.benchmark.spec import load, sha256

NFCORE_SHA = "1a1dbe52ffbd82256c941a032b0e22abbd925b8a"
CONFIG_SHA = "19bb40da0b4bd9e69f8c0043f8c55ce10f5dac20"


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def prepare(prepared: Path, out: Path, checkout: Path, containers: Path) -> None:
    plan = load(prepared)
    if plan["status"] != "READY" or plan["spec"]["assay"] != "bulk-ATAC":
        raise ValueError("A fully verified public ATAC experiment is required")
    if plan["spec"]["parameters"]["mapq"] != [30] or plan["spec"]["parameters"]["duplicates"] != [
        "excluded"
    ]:
        raise ValueError("This panel uses MAPQ30 and explicit duplicate exclusion")
    revision = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = subprocess.check_output(
        ["git", "-C", str(checkout), "status", "--porcelain"], text=True
    ).strip()
    if revision != NFCORE_SHA or dirty:
        raise ValueError("Use a clean nf-core/atacseq 2.1.2 checkout")
    out.mkdir(parents=True, exist_ok=False)
    dataset = plan["dataset"]
    for key, asset in dataset["assets"].items():
        source = Path(plan["assets"][key]["path"])
        target = out / (key + ".asset" + "".join(source.suffixes))
        shutil.copyfile(source, target)
        if sha256(target) != asset["sha256"]:
            raise ValueError("Staged asset differs: " + key)
        asset["path"] = str(target)
    write(out / "dataset.json", dataset)
    ref = dataset["reference"]
    fasta, tss = (dataset["assets"][ref[k]] for k in ("fasta", "tss"))
    reference = {
        "reference_id": ref["id"],
        "species": ref["species"],
        "assembly": ref["assembly"],
        "annotation_release": ref["annotation_release"],
        "fasta": fasta["path"],
        "fasta_sha256": fasta["sha256"],
        "tss": tss["path"],
        "tss_sha256": tss["sha256"],
        "genome_size": ref["effective_genome_size"],
        "genome_size_method": "Nuclear ACGT bases; not a mappability estimate",
        "mitochondrial_contigs": ref["mitochondrial"],
        "plastid_contigs": ref["plastid"],
    }
    registry = out / "references.json"
    write(registry, {"schema_version": 1, "references": [reference]})
    sheet = out / "samples.tsv"
    rows = []
    for library in dataset["libraries"]:
        row = {
            "library_id": library["id"],
            "sample_id": dataset["id"],
            "biological_replicate": library["biological_replicate"],
            "lane_id": "sampled-input",
            "reference_id": ref["id"],
            "mapq": 30,
            "duplicates": "exclude",
            "adapter_r1": "-",
            "adapter_r2": "-",
        }
        for mate in ("read1", "read2"):
            asset = dataset["assets"][library[mate]]
            row[mate], row[mate + "_sha256"] = asset["path"], asset["sha256"]
        rows.append(row)
    with sheet.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    libraries(sheet, references(registry))
    (out / "genesis.config").write_text(
        "process { withName: '.*' { cpus = 14; memory = '80 GB'; time = '2h' } }\n"
        "executor.cpus = 14\nexecutor.memory = '80 GB'\n"
        "docker.runOptions = '-u $(id -u):$(id -g) --pull=never --cpus 14 --memory 80g'\n"
    )
    lock = out / "nfcore-containers.json"
    shutil.copyfile(containers, lock)
    experiment = {
        "schema_version": 1,
        "id": dataset["id"] + "-nfcore",
        "assay": "bulk-ATAC",
        "adapter": "nfcore-atac",
        "dataset": str(out / "dataset.json"),
        "baseline": "mapq1-excluded",
        "comparison": "workflow-configured",
        "seed": 60141,
        "parameters": {
            "mapq": [1],
            "duplicates": ["excluded"],
            "keep_dup": "all",
            "qvalue": 0.01,
            "spp": False,
            "quantify": False,
        },
        "limits": {
            "cpus": 14,
            "memory_gb": 80,
            "seconds": 7200,
            "disk_bytes": 20000000000,
            "download_bytes": 0,
            "max_arms": 1,
        },
        "performance": {"repetitions": 1, "random_order": False},
        "workflow": {
            "checkout": str(checkout),
            "git_sha": NFCORE_SHA,
            "container_lock": str(lock),
            "container_lock_sha256": sha256(lock),
            "parameters": {
                "aligner": "bwa",
                "narrow_peak": True,
                "macs_fdr": 0.01,
                "skip_trimming": True,
                "keep_dups": False,
                "keep_multi_map": False,
                # An unused genome-size lookup option when macs_gsize is explicit;
                # nf-core restricts it to 50/75/100/150/200. It is not observed length.
                "read_length": 50,
                "custom_config_version": CONFIG_SHA,
                "mito_name": ref["mitochondrial"][0],
            },
        },
    }
    write(out / "nfcore.json", experiment)
    validated = load(out / "nfcore.json")
    if validated["status"] != "READY":
        raise ValueError(str(validated["blockers"]))
    write(
        out / "preparation.json",
        {
            "script_sha256": sha256(Path(__file__)),
            "prepared_sha256": sha256(prepared),
            "nfcore_git_sha": NFCORE_SHA,
            "configuration_sha": CONFIG_SHA,
            "scope": "Workflow-configured; not matched filtering or untouched defaults",
            "differences": {
                "genesis": "BWA-MEM2; both mates MAPQ30; excludes Mt and Pt; nuclear ACGT gsize",
                "nfcore": "BWA; MAPQ1 plus native filters; removes Mt; total reference gsize",
                "read_length": "50 is an unused genome-size lookup option, not observed length",
            },
            "files": {p.name: sha256(p) for p in sorted(out.iterdir()) if p.is_file()},
        },
    )
    print(out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prepared", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--nfcore-checkout", type=Path, required=True)
    parser.add_argument("--container-lock", type=Path, required=True)
    args = parser.parse_args()
    prepare(
        args.prepared.resolve(),
        args.out.resolve(),
        args.nfcore_checkout.resolve(),
        args.container_lock.resolve(),
    )
