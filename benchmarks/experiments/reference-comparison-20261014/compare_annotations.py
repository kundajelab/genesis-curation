"""Compare two annotation releases on identical BAMs and the same FASTA."""

from __future__ import annotations

import argparse
import csv
import json
import tempfile
from collections import defaultdict
from pathlib import Path

from genesis_curation.benchmark.metrics import tss_enrichment
from genesis_curation.benchmark.spec import sha256, write_json


def gene_points(path: Path) -> dict[str, set[tuple[str, int, str]]]:
    result: dict[str, set[tuple[str, int, str]]] = defaultdict(set)
    with path.open() as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            # Prefixes are distribution syntax; stable gene IDs remain unchanged.
            gene = row["gene"].removeprefix("gene:")
            result[gene].add((row["contig"], int(row["position_bed0"]), row["strand"]))
    return result


def compare(run: Path, bundles: list[Path], out: Path) -> None:
    if len(bundles) != 2:
        raise ValueError("Supply exactly two reference bundles")
    refs = [json.loads(p.read_text()) for p in bundles]
    if len({r["scan"]["content_sha256"] for r in refs}) != 1:
        raise ValueError("Annotation-only comparison requires the same FASTA content")
    plan = json.loads((run / "experiment.json").read_text())
    result = json.loads((run / "results.json").read_text())
    if (
        result["status"] != "COMPLETE"
        or plan["dataset"]["reference"]["fasta_content_sha256"] != refs[0]["scan"]["content_sha256"]
    ):
        raise ValueError("Completed experiment uses a different reference")
    for ref in refs:
        for asset in ref["assets"].values():
            if sha256(Path(asset["path"])) != asset["sha256"]:
                raise ValueError("Reference artifact changed")
    out.mkdir(parents=True, exist_ok=False)
    points = [
        gene_points(Path(r["assets"][r["reference"]["id"] + "-tss-genes"]["path"])) for r in refs
    ]
    common = sorted(
        g
        for g in points[0].keys() & points[1].keys()
        if len(points[0][g]) == len(points[1][g]) == 1
    )
    common = [g for g in common if next(iter(points[0][g]))[::2] == next(iter(points[1][g]))[::2]]
    sets = []
    for index, ref in enumerate(refs):
        path = out / f"annotation-{index + 1}-common-genes.tss.bed"
        positions = sorted({next(iter(points[index][gene])) for gene in common})
        path.write_text("".join(f"{c}\t{p}\t{s}\n" for c, p, s in positions))
        sets.append(
            {
                "all_transcript_starts": Path(ref["assets"][ref["reference"]["tss"]]["path"]),
                "common_single_start_genes": path,
            }
        )
    observed = {}
    baseline = next(c for c in result["results"] if c["arm"] == "baseline" and c["repetition"] == 1)
    sizes = {c: x["length"] for c, x in refs[0]["scan"]["contigs"].items()}
    for library, value in baseline["libraries"].items():
        bam = Path(value["artifact_roles"]["bam"])
        if sha256(bam) != value["artifacts"][str(bam)]:
            raise ValueError("Evaluated BAM changed")
        observed[library] = {"bam_sha256": sha256(bam), "annotations": {}}
        for ref, collections in zip(refs, sets, strict=True):
            profiles = {}
            for name, tss in collections.items():
                with tempfile.TemporaryDirectory(dir=out) as temporary:
                    profiles[name] = tss_enrichment(bam, tss, sizes, Path(temporary))
            observed[library]["annotations"][ref["reference"]["annotation_release"]] = profiles
    write_json(
        out / "comparison.json",
        {
            "schema_version": 1,
            "status": "COMPLETE",
            "experiment_id": plan["experiment_id"],
            "bundle_sha256": [sha256(p) for p in bundles],
            "script_sha256": sha256(Path(__file__)),
            "common_single_start_gene_count": len(common),
            "common_genes": common,
            "all_gene_counts": [len(p) for p in points],
            "definition": (
                "Same FASTA and selected fragments. Common genes have exactly one unique "
                "TSS per annotation, on the same contig and strand; positions may differ. "
                "This tests annotation sensitivity, not annotation correctness."
            ),
            "libraries": observed,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--bundle", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    compare(args.run.resolve(), args.bundle, args.out.resolve())
