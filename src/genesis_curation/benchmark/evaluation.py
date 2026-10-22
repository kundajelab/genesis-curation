"""Independent interval checks and labeled synthetic alignment evaluation."""

from __future__ import annotations

import itertools
import tempfile
from pathlib import Path
from typing import Any

import pysam

from .metrics import Regions, assess, groups, intersection, intervals, merged, primary, ratio
from .spec import replicate_pairs


def alignment_truth(actual: Path, truth: Path, temporary: Path) -> dict[str, Any]:
    """Compare primary end coordinates/strand with known simulated origins.

    This includes ambiguously mappable origins; it does not treat injected MAPQs
    or duplicate flags as truth. The unfiltered aligner output is required.
    """
    correct = mapped = total = 0
    with (
        tempfile.TemporaryDirectory(dir=temporary) as a,
        tempfile.TemporaryDirectory(dir=temporary) as b,
    ):
        for observed, expected in itertools.zip_longest(
            groups(actual, Path(a)), groups(truth, Path(b))
        ):
            if (
                observed is None
                or expected is None
                or observed[0].query_name != expected[0].query_name
            ):
                raise ValueError(
                    "Alignment truth requires the identical complete read-name population"
                )
            records = {r.is_read2: r for r in primary(observed)}
            for read in expected:
                total += 1
                value = records.get(read.is_read2)
                if value is None:
                    continue
                mapped += 1
                correct += (
                    value.reference_name == read.reference_name
                    and value.reference_start == read.reference_start
                    and value.reference_end == read.reference_end
                    and value.is_reverse == read.is_reverse
                )
    return {
        "truth_alignment_precision": ratio(
            correct, mapped, "exact source contig/start/end/strand / mapped primary read ends"
        ),
        "truth_alignment_recall": ratio(
            correct, total, "exact source contig/start/end/strand / simulated raw read ends"
        ),
    }


def peak_truth(actual: Path, truth: Path) -> dict[str, Any]:
    calls, _ = intervals(actual)
    expected, _ = intervals(truth)
    index, reverse = Regions(expected), Regions(calls)
    overlap = intersection(calls, expected)
    return {
        "truth_peak_precision": ratio(
            sum(bool(index.hits(*r)) for r in calls),
            len(calls),
            "called peaks overlapping a simulated enriched region / called peaks",
        ),
        "truth_region_recall": ratio(
            sum(bool(reverse.hits(*r)) for r in expected),
            len(expected),
            "simulated enriched regions overlapped by a called peak / simulated enriched regions",
        ),
        "truth_peak_base_precision": ratio(
            overlap,
            sum(e - s for _, s, e in merged(calls)),
            "called bases within simulated enriched regions / called union bases",
        ),
        "truth_region_base_recall": ratio(
            overlap,
            sum(e - s for _, s, e in merged(expected)),
            "simulated enriched bases covered by calls / enriched union bases",
        ),
    }


def read_end_counts(bam: Path, regions: Path) -> list[int]:
    """Unsplit aligned spans, matching bedtools coverage without -split."""
    rows, _ = intervals(regions)
    index = Regions(rows)
    counts = [0] * len(rows)
    with pysam.AlignmentFile(str(bam)) as stream:
        for read in stream:
            if read.is_unmapped or read.is_secondary or read.is_supplementary:
                continue
            end, chrom = read.reference_end, read.reference_name
            if end is not None and chrom is not None:
                for hit in index.hits(chrom, read.reference_start, end):
                    counts[hit] += 1
    return counts


def replicate_evaluation(dataset: dict[str, Any], results: list[dict[str, Any]], out: Path) -> None:
    """Evaluate explicitly declared biological pairs; never infer from accessions."""
    source = dataset.get("source", {})
    pairs = replicate_pairs(dataset)
    libraries = {lib["id"]: lib for lib in dataset["libraries"]}
    for record in results:
        if record["status"] != "COMPLETE":
            continue
        for pair in pairs:
            for name, other in (pair, pair[::-1]):
                value = record["libraries"][name]
                observed, comparison = Path(record["peaks"][name]), Path(record["peaks"][other])
                current, _ = intervals(observed)
                target, _ = intervals(comparison)
                overlap = intersection(current, target)
                union = (
                    sum(e - s for _, s, e in merged(current))
                    + sum(e - s for _, s, e in merged(target))
                    - overlap
                )
                value["metrics"]["replicate_peak_jaccard"] = ratio(
                    overlap,
                    union,
                    "peak base-pair intersection / union with independently declared "
                    f"biological replicate {other}",
                )
                with tempfile.TemporaryDirectory(dir=out) as tmp:
                    heldout = assess(
                        Path(record["bams"][name]),
                        comparison,
                        comparison,
                        layout=libraries[name]["layout"],
                        raw_templates=libraries[name]["raw_templates"],
                        organelles=set(
                            dataset["reference"]["mitochondrial"] + dataset["reference"]["plastid"]
                        ),
                        temporary=Path(tmp),
                    )
                score = heldout["metrics"]["fragments_FRiP_fixed"]
                score["definition"] = (
                    "retained fragments overlapping peaks called independently in biological "
                    f"replicate {other} / retained fragments; width-dependent, not accuracy"
                )
                value["metrics"]["heldout_fragments_FRiP"] = score
                value["replicate_evidence"] = {
                    "other_library": other,
                    "peaks": str(comparison),
                    "declaration": source,
                }
