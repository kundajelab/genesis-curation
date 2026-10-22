"""Evaluate DAP summits with independent PBM motifs and GC-matched genomic controls.

Run with the separate benchmark environment's Python (NumPy and pysam).
This is a biological plausibility measurement, not a binding-site truth set.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import random
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pysam

HERE = Path(__file__).resolve().parent
MOTIFS = {"SRR2926076": "MA0565.1", "SRR2926841": "MA0931.1"}
ScoreArray = npt.NDArray[np.int64]


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def matrix(path: Path) -> tuple[ScoreArray, int, dict[str, Any]]:
    record = json.loads(path.read_text())
    if record["type"] != "PBM" or record["matrix_id"] not in MOTIFS.values():
        raise ValueError("The declared independent PBM matrix is required")
    counts = np.array([record["pfm"][base] for base in "ACGT"], dtype=float)
    if (
        counts.ndim != 2
        or not 1 <= counts.shape[1] <= 10
        or np.any(counts < 0)
        or not np.all(np.isfinite(counts))
    ):
        raise ValueError("Unsupported PFM dimensions or negative counts")
    probabilities = (counts + 0.5) / (counts.sum(axis=0) + 2.0)
    # Integer millionths of a bit make the null and scanner agree exactly at ties.
    scores = np.rint(np.log2(probabilities / 0.25) * 1e6).astype(np.int64)
    # Enumerate all words: these small PBM motifs permit an exact uniform null.
    distribution = np.zeros(1, dtype=np.int64)
    for column in scores.T:
        distribution = (distribution[:, None] + column[None, :]).ravel()
    distribution.sort()
    # Choose the least score whose inclusive upper tail is <= 1e-4.
    allowed = max(1, int(len(distribution) * 1e-4))
    threshold = int(distribution[-allowed])
    if np.count_nonzero(distribution >= threshold) > allowed:
        threshold += 1
    return (
        scores,
        threshold,
        {
            "matrix_id": record["matrix_id"],
            "name": record["name"],
            "sha256": digest(path),
            "source_type": "independent PBM",
            "pseudocount_per_base": 0.5,
            "null": "independent uniform bases; single strand, single position",
            "threshold_log2_odds": threshold / 1e6,
            "score_rounding": "each log2-odds weight rounded to integer millionths of a bit",
            "exact_null_tail_probability": float(np.mean(distribution >= threshold)),
        },
    )


def score(sequence: str, pwm: ScoreArray) -> int:
    """Maximum log2 odds over both strands; ambiguous bases are not accepted."""
    if set(sequence) - set("ACGT") or len(sequence) < pwm.shape[1]:
        raise ValueError("Motif windows must contain only A/C/G/T and fit the motif")
    encoded = np.array(["ACGT".index(base) for base in sequence], dtype=np.int8)
    windows = np.lib.stride_tricks.sliding_window_view(encoded, pwm.shape[1])
    columns = np.arange(pwm.shape[1])
    forward = pwm[windows, columns].sum(axis=1)
    reverse = pwm[3 - windows[:, ::-1], columns].sum(axis=1)
    return int(max(forward.max(), reverse.max()))


def peak_windows(path: Path, genome: dict[str, str]) -> tuple[list[dict[str, Any]], int]:
    rows, excluded = [], 0
    for line in path.read_text().splitlines():
        f = line.split("\t")
        start, end = int(f[1]), int(f[2])
        summit = int(f[9]) if len(f) >= 10 else -1
        center = start + summit if 0 <= summit < end - start else (start + end) // 2
        left, right = center - 100, center + 100
        sequence = genome.get(f[0], "")[max(0, left) : right]
        if left < 0 or len(sequence) != 200 or set(sequence) - set("ACGT"):
            excluded += 1
            continue
        rows.append(
            {
                "chrom": f[0],
                "start": left,
                "end": right,
                "signal": float(f[6]),
                "center_kind": "summit" if summit >= 0 else "interval midpoint",
                "sequence": sequence,
            }
        )
    rows.sort(key=lambda r: (-r["signal"], r["chrom"], r["start"], r["end"]))
    return rows, excluded


def exclusion_index(rows: list[dict[str, Any]]) -> dict[str, tuple[list[int], list[int]]]:
    """Index the union of summit windows from every compared method."""
    merged: dict[str, list[list[int]]] = {}
    for row in sorted(rows, key=lambda r: (r["chrom"], r["start"], r["end"])):
        intervals = merged.setdefault(row["chrom"], [])
        if intervals and row["start"] <= intervals[-1][1]:
            intervals[-1][1] = max(intervals[-1][1], row["end"])
        else:
            intervals.append([row["start"], row["end"]])
    return {chrom: ([r[0] for r in rs], [r[1] for r in rs]) for chrom, rs in merged.items()}


def background(
    row: dict[str, Any], genome: dict[str, str], index: dict[str, tuple[list[int], list[int]]]
) -> tuple[str, int] | None:
    """One same-chromosome 200-bp window, within 2 GC bases, outside all calls."""
    seed = hashlib.sha256(f"60141:{row['chrom']}:{row['start']}:{row['end']}".encode()).digest()
    rng = random.Random(seed)
    sequence = genome[row["chrom"]]
    starts, ends = index.get(row["chrom"], ([], []))
    target_gc = row["sequence"].count("G") + row["sequence"].count("C")
    for _ in range(10000):
        start = rng.randrange(len(sequence) - 199)
        right = start + 200
        match = bisect.bisect_left(starts, right) - 1
        if match >= 0 and ends[match] > start:
            continue
        candidate = sequence[start:right]
        if set(candidate) - set("ACGT"):
            continue
        if abs(candidate.count("G") + candidate.count("C") - target_gc) <= 2:
            return candidate, start
    return None


def evaluate(results: Path, out: Path, peak_root: Path | None = None) -> None:
    plan = json.loads((results / "experiment.json").read_text())
    measured = json.loads((results / "results.json").read_text())
    if measured["status"] != "COMPLETE" or plan["dataset"]["assay"] != "DAP-seq":
        raise ValueError("A complete DAP comparison is required")
    out.mkdir(parents=True, exist_ok=False)
    reference = plan["assets"][plan["dataset"]["reference"]["fasta"]]
    fasta = Path(reference["path"])
    if digest(fasta) != reference["sha256"]:
        raise ValueError("Reference changed after the comparison")
    organelles = set(
        plan["dataset"]["reference"]["mitochondrial"] + plan["dataset"]["reference"]["plastid"]
    )
    genome: dict[str, str] = {}
    with pysam.FastxFile(str(fasta)) as stream:
        for record in stream:
            name, sequence = record.name, record.sequence
            if name is None or sequence is None or name in genome:
                raise ValueError("Reference needs unique named sequences")
            if name not in organelles:
                genome[name] = sequence.upper()
    all_rows, peak_hashes = {}, {}
    for record in measured["results"]:
        for library in MOTIFS.keys() & record["peaks"].keys():
            key = (record["arm"], record["repetition"], library)
            original_peaks = Path(record["peaks"][library])
            peaks = (
                peak_root / original_peaks.relative_to(results)
                if peak_root is not None
                else original_peaks
            )
            actual = digest(peaks)
            expected = record["libraries"][library]["artifacts"][str(original_peaks)]
            if actual != expected:
                raise ValueError("Peak file changed after evaluation")
            peak_hashes[str(original_peaks.relative_to(results))] = actual
            all_rows[key] = peak_windows(peaks, genome)
    if not all_rows:
        raise ValueError("No library has an independently declared PBM motif")
    summaries, detail, motif_records = [], [], {}
    for library, name in MOTIFS.items():
        pwm, threshold, provenance = matrix(HERE / "sources" / (name + ".json"))
        motif_records[library] = provenance
        exclusion = exclusion_index(
            [r for k, (rows, _) in all_rows.items() if k[2] == library for r in rows]
        )
        cached = {}
        for (arm, repetition, sample), (rows, excluded) in all_rows.items():
            if sample != library:
                continue
            paired: list[dict[str, Any]] = []
            for rank, row in enumerate(rows, 1):
                key = (row["chrom"], row["start"], row["end"])
                if key not in cached:
                    matched = background(row, genome, exclusion)
                    cached[key] = (
                        None
                        if matched is None
                        else (score(row["sequence"], pwm), score(matched[0], pwm), matched[1])
                    )
                value = cached[key]
                if value is None:
                    continue
                target, control, position = value
                entry = {
                    "arm": arm,
                    "repetition": repetition,
                    "library": library,
                    "rank": rank,
                    "chrom": row["chrom"],
                    "start": row["start"],
                    "end": row["end"],
                    "center_kind": row["center_kind"],
                    "peak_score": target / 1e6,
                    "control_score": control / 1e6,
                    "control_start": position,
                    "peak_hit": target >= threshold,
                    "control_hit": control >= threshold,
                }
                detail.append(entry)
                paired.append(entry)
            for top in (1000, 5000, None):
                selection = [r for r in paired if top is None or r["rank"] <= top]
                n = len(selection)
                ph, bh = (
                    sum(r["peak_hit"] for r in selection),
                    sum(r["control_hit"] for r in selection),
                )
                summaries.append(
                    {
                        "arm": arm,
                        "repetition": repetition,
                        "library": library,
                        "matrix": name,
                        "selection": "all" if top is None else f"top{top}",
                        "eligible_peaks": len(rows),
                        "excluded_windows": excluded,
                        "unmatched_controls": len(rows) - len(paired),
                        "paired_windows": n,
                        "status": "COMPLETE" if n and (top is None or n == top) else "INCOMPLETE",
                        "peak_hits": ph,
                        "control_hits": bh,
                        "peak_hit_fraction": ph / n if n else None,
                        "control_hit_fraction": bh / n if n else None,
                        "fold_enrichment": ph / bh if bh else None,
                        "reason": "zero control hits" if n and not bh else "",
                    }
                )
    for name, rows in (("motif-metrics.tsv", summaries), ("motif-windows.tsv", detail)):
        if rows:
            with (out / name).open("w") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter="\t")
                writer.writeheader()
                writer.writerows(rows)
    (out / "provenance.json").write_text(
        json.dumps(
            {
                "script_sha256": digest(Path(__file__)),
                "numpy": np.__version__,
                "experiment_id": plan["experiment_id"],
                "result_sha256": digest(results / "results.json"),
                "reference_sha256": reference["sha256"],
                "peaks": peak_hashes,
                "peak_location": "verified archive projection" if peak_root else "original result",
                "motifs": motif_records,
                "window": "200 bp centered on summit; interval midpoint if summit is absent",
                "background": "one seeded same-chromosome window per peak, GC within 2 bases, "
                "outside pooled method summit windows; at most 10000 attempts",
                "scope": "PBM motif support, not independent in-vivo binding truth; "
                "top counts ranked by caller signal, not a shared calibrated significance",
            },
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--peak-root",
        type=Path,
        help="Optional extracted archive root; original result hashes still verify every peak file",
    )
    args = parser.parse_args()
    evaluate(
        args.results.resolve(),
        args.out.resolve(),
        args.peak_root.resolve() if args.peak_root else None,
    )
