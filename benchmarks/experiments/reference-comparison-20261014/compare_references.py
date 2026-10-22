"""Compare peaks only after unique reciprocal mapping between declared references."""

from __future__ import annotations

import argparse
import collections
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from genesis_curation.benchmark.metrics import (
    assess,
    correlation,
    intersection,
    intervals,
    merged,
    metric,
    ratio,
)
from genesis_curation.benchmark.reference_mapping import paf_to_chain, validate_chain
from genesis_curation.benchmark.spec import sha256, write_json


def matched_inputs(plans: list[dict[str, Any]]) -> dict[str, Any]:
    """Require a paired comparison, including controls and scientific settings."""
    if len(plans) != 2:
        raise ValueError("Exactly two experiments are required")
    left, right = plans
    if left["dataset"]["assay"] != right["dataset"]["assay"]:
        raise ValueError("Reference comparison requires the same assay")
    if any(left["spec"][k] != right["spec"][k] for k in ("parameters", "seed")):
        raise ValueError("Reference comparison requires identical scientific parameters and seed")
    methods = [[m for m in p["spec"]["methods"] if m["id"] == "baseline"] for p in plans]
    if any(len(m) != 1 for m in methods) or methods[0] != methods[1]:
        raise ValueError("Reference comparison requires an identical baseline method")
    libraries = [{lib["id"]: lib for lib in p["dataset"]["libraries"]} for p in plans]
    if any(
        len(libs) != len(p["dataset"]["libraries"])
        for libs, p in zip(libraries, plans, strict=True)
    ):
        raise ValueError("Duplicate library IDs")
    if libraries[0].keys() != libraries[1].keys():
        raise ValueError("Reference comparison requires identical library sets")
    reads = {}
    for name in sorted(libraries[0]):
        fields = (
            "layout",
            "raw_templates",
            "control",
            "biological_replicate",
            "assay_subtype",
        )
        if any(libraries[0][name].get(k) != libraries[1][name].get(k) for k in fields):
            raise ValueError("Reference comparison requires identical library and control metadata")
        selected = [
            [
                p["assets"][libs[name][k]]["sha256"] if k in libs[name] else None
                for k in ("read1", "read2")
            ]
            for p, libs in zip(plans, libraries, strict=True)
        ]
        if selected[0] != selected[1]:
            raise ValueError(
                "Reference comparison requires identical sampled reads, including controls"
            )
        reads[name] = selected[0]
    return {
        "read_sha256": reads,
        "scientific_parameters": left["spec"]["parameters"],
        "baseline_method": methods[0][0],
        "seed": left["spec"]["seed"],
    }


def lift(
    rows: list[tuple[str, int, int]],
    forward: Path,
    reverse: Path,
    tool: Path,
    out: Path,
    source_sizes: dict[str, int],
    target_sizes: dict[str, int],
) -> tuple[dict[int, tuple[str, int, int]], dict[str, Any]]:
    out.mkdir()
    bed = out / "input.bed"
    bed.write_text("".join(f"{c}\t{s}\t{e}\tp{i}\t0\t+\n" for i, (c, s, e) in enumerate(rows)))

    def run(
        source: Path, chain: Path, name: str, sizes: dict[str, int]
    ) -> dict[int, list[tuple[str, int, int, str]]]:
        target = out / (name + ".bed")
        command = [
            str(tool),
            "-minMatch=0.95",
            "-multiple",
            "-noSerial",
            str(source),
            str(chain),
            str(target),
            str(out / (name + ".unmapped")),
        ]
        completed = subprocess.run(command, text=True, capture_output=True, timeout=300)
        write_json(
            out / (name + ".command.json"),
            {
                "argv": command,
                "exit_status": completed.returncode,
                "stderr": completed.stderr,
            },
        )
        if completed.returncode:
            raise ValueError("liftOver failed")
        result: dict[int, list[tuple[str, int, int, str]]] = collections.defaultdict(list)
        for line in target.read_text().splitlines():
            fields = line.split("\t")
            c, s, e, identifier, strand = (
                fields[0],
                int(fields[1]),
                int(fields[2]),
                fields[3],
                fields[5],
            )
            index = int(identifier.removeprefix("p"))
            if (
                not 0 <= index < len(rows)
                or c not in sizes
                or not 0 <= s < e <= sizes[c]
                or strand not in {"+", "-"}
            ):
                raise ValueError("Invalid lifted interval")
            result[index].append((c, s, e, strand))
        return result

    first = run(bed, forward, "forward", target_sizes)
    unique = out / "unique.bed"
    unique.write_text(
        "".join(
            f"{r[0]}\t{r[1]}\t{r[2]}\tp{i}\t0\t{r[3]}\n"
            for i, values in first.items()
            if len(values) == 1
            for r in values
        )
    )
    back = run(unique, reverse, "reverse", source_sizes)
    accepted, states = {}, {}
    for i, original in enumerate(rows):
        if not first.get(i):
            state = "UNMAPPED_OR_LESS_THAN_95_PERCENT_ALIGNED"
        elif len(first[i]) != 1:
            state = "AMBIGUOUS_FORWARD"
        elif not back.get(i):
            state = "ROUNDTRIP_UNMAPPED"
        elif len(back[i]) != 1:
            state = "AMBIGUOUS_ROUNDTRIP"
        else:
            c, s, e, strand = back[i][0]
            overlap = max(0, min(original[2], e) - max(original[1], s)) if c == original[0] else 0
            if strand != "+" or overlap / max(original[2] - original[1], e - s) < 0.95:
                state = "ROUNDTRIP_DIFFERENT_LOCUS_OR_SPAN"
            else:
                state = "UNIQUE_RECIPROCAL"
                accepted[i] = first[i][0][:3]
        states[str(i)] = state
    return accepted, {
        "counts": dict(collections.Counter(states.values())),
        "input_peaks": len(rows),
        "unique_reciprocal_fraction": ratio(
            len(accepted),
            len(rows),
            "unique forward and reverse placements; "
            "roundtrip overlap >=95% of both spans / input peaks",
        ),
        "per_peak": states,
    }


def compare(left: Path, right: Path, mapping: Path, tool: Path, out: Path) -> None:
    plans = [json.loads((p / "experiment.json").read_text()) for p in (left, right)]
    results = [json.loads((p / "results.json").read_text()) for p in (left, right)]
    if any(r["status"] != "COMPLETE" for r in results):
        raise ValueError("Both experiments must be complete")
    matched = matched_inputs(plans)
    refs = [p["dataset"]["reference"] for p in plans]
    if refs[0]["species"] != refs[1]["species"]:
        raise ValueError("This comparison requires the same species")
    m = json.loads(mapping.read_text())
    hashes = [p["assets"][r["fasta"]]["sha256"] for p, r in zip(plans, refs, strict=True)]
    if m["schema_version"] != 1 or [m["left_fasta_sha256"], m["right_fasta_sha256"]] != hashes:
        raise ValueError("Assembly mapping is not bound to these exact reference files")
    sizes = [
        {
            c: int(n)
            for c, n in (
                line.split()
                for line in Path(p["assets"][r["chrom_sizes"]]["path"]).read_text().splitlines()
            )
        }
        for p, r in zip(plans, refs, strict=True)
    ]
    for p, r in zip(plans, refs, strict=True):
        asset = p["assets"][r["chrom_sizes"]]
        if sha256(Path(asset["path"])) != asset["sha256"]:
            raise ValueError("Chromosome sizes changed after execution")
    out.mkdir(parents=True, exist_ok=False)
    chains = []
    for index, key in enumerate(("forward", "reverse")):
        source = (mapping.parent / m[key]["path"]).resolve()
        if sha256(source) != m[key]["sha256"]:
            raise ValueError("Assembly mapping changed")
        if m[key]["format"] == "paf":
            chain = out / (key + ".chain")
            receipt = paf_to_chain(source, chain, sizes[index], sizes[1 - index])
            write_json(out / (key + ".json"), receipt)
        elif m[key]["format"] == "chain":
            chain = source
        else:
            raise ValueError("Unsupported mapping format")
        write_json(
            out / (key + ".validation.json"),
            validate_chain(chain, sizes[index], sizes[1 - index]),
        )
        chains.append(chain)
    cases = [
        next(c for c in r["results"] if c["arm"] == "baseline" and c["repetition"] == 1)
        for r in results
    ]
    libraries = [{lib["id"]: lib for lib in p["dataset"]["libraries"]} for p in plans]
    expected = {
        name
        for name, lib in libraries[0].items()
        if plans[0]["dataset"]["assay"] == "bulk-ATAC" or lib.get("control")
    }
    if any(set(c["peaks"]) != expected for c in cases):
        raise ValueError("Baseline peak libraries are missing or inconsistent")
    observed = {}
    for name in sorted(expected):
        paths = [Path(c["peaks"][name]) for c in cases]
        for path, case in zip(paths, cases, strict=True):
            if sha256(path) != case["libraries"][name]["artifacts"][str(path)]:
                raise ValueError("Peak artifact changed")
        peaks = [intervals(p)[0] for p in paths]
        destination = out / name
        destination.mkdir()
        projected, evidence = [], []
        for index, label in enumerate(("left", "right")):
            values, receipt = lift(
                peaks[index],
                chains[index],
                chains[1 - index],
                tool,
                destination / label,
                sizes[index],
                sizes[1 - index],
            )
            projected.append(values)
            evidence.append(receipt)
        a = list(projected[0].values())
        b = [peaks[1][i] for i in projected[1]]
        overlap = intersection(a, b)
        union = sum(e - s for _, s, e in merged(a)) + sum(e - s for _, s, e in merged(b)) - overlap
        measurement = {
            "reciprocal_peak_base_jaccard": ratio(
                overlap,
                union,
                "peak intersection / union in right-reference coordinates, "
                "restricted to uniquely reciprocal intervals on both sides",
            )
        }
        fixed = [[peaks[0][i] for i in projected[0]], list(projected[0].values())]
        counts = []
        for index, label in enumerate(("left", "right")):
            bed = destination / (label + "-fixed.bed")
            bed.write_text("".join(f"{c}\t{s}\t{e}\n" for c, s, e in fixed[index]))
            bam = Path(cases[index]["bams"][name])
            if not bam.exists():
                counts.append(None)
                continue
            if sha256(bam) != cases[index]["libraries"][name]["artifacts"][str(bam)]:
                raise ValueError("BAM changed")
            with tempfile.TemporaryDirectory(dir=destination) as scratch:
                value = assess(
                    bam,
                    bed,
                    bed,
                    layout=libraries[index][name]["layout"],
                    raw_templates=libraries[index][name]["raw_templates"],
                    organelles=set(refs[index]["mitochondrial"] + refs[index]["plastid"]),
                    temporary=Path(scratch),
                )
            counts.append(value["fixed_counts"])
        measurement["common_region_fragment_spearman"] = metric(
            correlation(counts[0], counts[1], rank=True)
            if all(v is not None for v in counts)
            else None,
            "Spearman fragment counts in left baseline peaks and their "
            "unique reciprocal projections; widths may differ",
            "correlation",
            len(fixed[0]),
            "BAM unavailable, fewer than two regions, or zero variance",
        )
        observed[name] = {
            "left_mapping": evidence[0],
            "right_mapping": evidence[1],
            "metrics": measurement,
            "common_region_counts": counts,
        }
    write_json(
        out / "comparison.json",
        {
            "schema_version": 1,
            "status": "COMPLETE",
            "experiments": [p["experiment_id"] for p in plans],
            "matched_inputs": matched,
            "mapping_sha256": sha256(mapping),
            "liftover_sha256": sha256(tool),
            "script_sha256": sha256(Path(__file__)),
            "libraries": observed,
            "scope": (
                "Unique within the supplied assembly alignments; at least 95% aligned bases "
                "each way and 95% reciprocal span overlap. Excluded mappings remain explicit; "
                "this is concordance, not biological accuracy."
            ),
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left", type=Path, required=True)
    parser.add_argument("--right", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--liftover", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    compare(
        args.left.resolve(),
        args.right.resolve(),
        args.mapping.resolve(),
        args.liftover.resolve(),
        args.out.resolve(),
    )
