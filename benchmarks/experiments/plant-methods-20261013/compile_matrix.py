"""Compile a panel comparison with the verified production BWA-MEM2 binaries.

Preparation remains separate from execution. The mapping/processing groups use
identical input subsets and the same baseline, limiting temporary storage for maize.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from genesis_curation.benchmark.spec import load, sha256

HERE = Path(__file__).resolve().parent


def compile_matrix(
    prepared: Path,
    out: Path,
    *,
    group: str,
    repetitions: int,
    cpus: int,
    seed: int,
    include_legacy: bool = False,
) -> None:
    if out.exists():
        raise FileExistsError("Choose a new experiment manifest")
    if not 1 <= cpus <= 16 or not 1 <= repetitions <= 3:
        raise ValueError("This panel permits 1–16 CPUs and 1–3 timing repetitions")
    spec = json.loads(prepared.read_text())
    if spec["schema_version"] != 2 or spec["adapter"] != "methods":
        raise ValueError("A prepared version-2 method comparison is required")
    spec["dataset"] = str((prepared.parent / spec["dataset"]).resolve())
    baseline = next(m for m in spec["methods"] if m["id"] == "baseline")
    if baseline["aligner"] != "bwa-mem2":
        raise ValueError("Use a fresh prepared experiment with the BWA-MEM2 2.3 baseline")
    evidence = json.loads((HERE / "sources/production-aligner-identity.json").read_text())
    prefix = (prepared.parent / spec["environment"]["prefix"]).resolve()
    for name, expected in evidence["production"]["binaries"].items():
        if sha256(prefix / "bin" / name) != expected:
            raise ValueError(
                "Baseline executable differs from the tested production image: " + name
            )
    if include_legacy:
        spec["methods"].insert(1, {**baseline, "id": "bwa-mem2-2.2.1", "aligner": "bwa-mem2-2.2.1"})
        environment = HERE.parents[1] / "environment/bwa-mem2-2.2.1"
        spec["environment"]["bwa_mem2_221"] = {
            "prefix": str(environment / ".pixi/envs/default"),
            "lock": str(environment / "pixi.lock"),
            "lock_sha256": sha256(environment / "pixi.lock"),
        }
    mapping = {"baseline", "bwa-mem2-2.2.1", "bwa", "bowtie2"}
    if group != "all":
        spec["methods"] = [
            m
            for m in spec["methods"]
            if m["id"] == "baseline" or (m["id"] in mapping) == (group == "mapping")
        ]
    spec["id"] += "-production-baseline-" + group
    spec["seed"] = seed
    spec["performance"] = {"repetitions": repetitions, "random_order": True}
    spec["limits"].update(
        cpus=cpus, disk_bytes=70000000000, max_arms=len(spec["methods"]) * repetitions
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, indent=2, allow_nan=False) + "\n")
    plan = load(out)
    if plan["status"] != "READY":
        raise ValueError("Prepared assets are incomplete: " + str(plan["blockers"]))
    print(plan["experiment_id"], len(plan["arms"]), "methods,", repetitions, "repetitions")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prepared", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--group", choices=("all", "mapping", "processing"), default="all")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--cpus", type=int, default=14)
    parser.add_argument("--order-seed", type=int, default=60141)
    parser.add_argument("--include-legacy", action="store_true", help="Also compare BWA-MEM2 2.2.1")
    args = parser.parse_args()
    compile_matrix(
        args.prepared.resolve(),
        args.out.resolve(),
        group=args.group,
        repetitions=args.repetitions,
        cpus=args.cpus,
        seed=args.order_seed,
        include_legacy=args.include_legacy,
    )
