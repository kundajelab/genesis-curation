"""Independent BED0 metrics with explicit populations and undefined-value reasons."""

from __future__ import annotations

import bisect
import collections
import gzip
import hashlib
import itertools
import json
import math
import shutil
import statistics
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pysam

Interval = tuple[str, int, int]


def metric(
    value: object, definition: str, unit: str, denominator: object = None, reason: str | None = None
) -> dict[str, Any]:
    return {
        "value": value,
        "definition": definition,
        "version": 1,
        "unit": unit,
        "denominator": denominator,
        "status": "OK" if value is not None else "UNDEFINED",
        "reason": reason if value is None else None,
    }


def ratio(numerator: int, denominator: int, definition: str) -> dict[str, Any]:
    return metric(
        numerator / denominator if denominator else None,
        definition,
        "fraction",
        denominator,
        "zero denominator",
    )


def intervals(path: Path) -> tuple[list[Interval], list[float]]:
    opener = gzip.open if path.suffix == ".gz" else open
    rows, scores = [], []
    with opener(path, "rt") as stream:
        for line in stream:
            if not line.strip() or line.startswith(("#", "track", "browser")):
                continue
            fields = line.rstrip().split("\t")
            if len(fields) < 3:
                raise ValueError("Interval input requires at least three tab-separated columns")
            chrom, start, end = fields[0], int(fields[1]), int(fields[2])
            if start < 0 or end <= start:
                raise ValueError("Invalid BED0 half-open interval")
            rows.append((chrom, start, end))
            scores.append(float(fields[8]) if len(fields) >= 9 else 0.0)
    if any(not math.isfinite(x) for x in scores):
        raise ValueError("Non-finite peak scores")
    return rows, scores


def merged(rows: list[Interval]) -> list[Interval]:
    result: list[Interval] = []
    for chrom, start, end in sorted(rows):
        if result and result[-1][0] == chrom and result[-1][2] >= start:
            result[-1] = (chrom, result[-1][1], max(end, result[-1][2]))
        else:
            result.append((chrom, start, end))
    return result


def intersection(left: list[Interval], right: list[Interval]) -> int:
    a, b = merged(left), merged(right)
    i = j = total = 0
    while i < len(a) and j < len(b):
        c, s, e = a[i]
        d, x, y = b[j]
        if c == d:
            total += max(0, min(e, y) - max(s, x))
        if c < d or (c == d and e <= y):
            i += 1
        else:
            j += 1
    return total


def ranks(values: list[float]) -> list[float]:
    result = [0.0] * len(values)
    order = sorted(range(len(values)), key=values.__getitem__)
    for _, group in itertools.groupby(enumerate(order), key=lambda p: values[p[1]]):
        entries = list(group)
        average = statistics.mean(p[0] for p in entries)
        for _, index in entries:
            result[index] = average
    return result


def correlation(a: list[float], b: list[float], *, rank: bool = False) -> float | None:
    if len(a) != len(b):
        raise ValueError("Correlations require identical evaluation regions")
    if len(a) < 2:
        return None
    if rank:
        a, b = ranks(a), ranks(b)
    x, y = statistics.mean(a), statistics.mean(b)
    denominator = math.sqrt(sum((v - x) ** 2 for v in a) * sum((v - y) ** 2 for v in b))
    return (
        sum((v - x) * (w - y) for v, w in zip(a, b, strict=True)) / denominator
        if denominator
        else None
    )


class Regions:
    """Index intervals; preserve individual region counts and merge only for union metrics."""

    def __init__(self, rows: list[Interval]) -> None:
        self.rows = rows
        self.by_chrom: dict[str, list[tuple[int, int, int]]] = collections.defaultdict(list)
        for i, (chrom, start, end) in enumerate(rows):
            self.by_chrom[chrom].append((start, end, i))
        self.starts = {}
        self.prefix_end = {}
        for chrom, entries in self.by_chrom.items():
            entries.sort()
            self.starts[chrom] = [e[0] for e in entries]
            self.prefix_end[chrom] = list(itertools.accumulate((e[1] for e in entries), max))

    def hits(self, chrom: str, start: int, end: int) -> list[int]:
        entries = self.by_chrom.get(chrom, [])
        hi = bisect.bisect_left(self.starts.get(chrom, []), end)
        lo = bisect.bisect_right(self.prefix_end.get(chrom, []), start, hi=hi)
        return [i for s, e, i in entries[lo:hi] if s < end and e > start]


def groups(bam: Path, temporary: Path) -> Iterator[list[pysam.AlignedSegment]]:
    """Name collate on disk; memory bounded by one template rather than the library."""
    collated = temporary / "names.bam"
    pysam.sort("-n", "-m", "128M", "-o", str(collated), str(bam))
    try:
        with pysam.AlignmentFile(str(collated)) as stream:
            for _, records in itertools.groupby(stream, key=lambda r: r.query_name):
                yield list(records)
    finally:
        collated.unlink(missing_ok=True)


def primary(records: list[pysam.AlignedSegment]) -> list[pysam.AlignedSegment]:
    return [r for r in records if not (r.is_unmapped or r.is_secondary or r.is_supplementary)]


def fragment(records: list[pysam.AlignedSegment], layout: str) -> Interval | None:
    if layout == "SE":
        if len(records) != 1:
            return None
    elif (
        len(records) != 2
        or not all(r.is_paired for r in records)
        or sum(r.is_read1 for r in records) != 1
        or sum(r.is_read2 for r in records) != 1
        or records[0].reference_id != records[1].reference_id
    ):
        return None
    starts = [r.reference_start for r in records]
    ends = [r.reference_end for r in records]
    if any(end is None for end in ends):
        return None
    chrom = records[0].reference_name
    if chrom is None:
        return None
    return chrom, min(starts), max(e for e in ends if e is not None)


def alignment_identity(bam: Path, temporary: Path) -> str:
    digest = hashlib.sha256()
    for records in groups(bam, temporary):
        values = sorted(
            (
                r.query_name,
                r.flag & ~1024,
                r.reference_id,
                r.reference_start,
                r.mapping_quality,
                r.cigarstring,
                r.next_reference_id,
                r.next_reference_start,
                r.template_length,
                r.query_sequence,
                list(r.query_qualities) if r.query_qualities is not None else None,
            )
            for r in records
        )
        digest.update(json.dumps(values, sort_keys=True).encode())
    return digest.hexdigest()


def filter_bam(
    source: Path,
    output: Path,
    *,
    layout: str,
    mapq: int,
    exclude_duplicates: bool,
    organelles: set[str],
) -> dict[str, Any]:
    """Both mates pass positive MAPQ; no-cutoff retained baseline preserves orphan reads."""
    counts: collections.Counter[str] = collections.Counter()
    before: collections.Counter[int] = collections.Counter()
    after: collections.Counter[int] = collections.Counter()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        temporary = Path(tmp)
        unsorted = temporary / "selected.bam"
        with pysam.AlignmentFile(str(source)) as original:
            header = original.header
        with pysam.AlignmentFile(str(unsorted), "wb", header=header) as writer:
            for records in groups(source, temporary):
                reads = primary(records)
                counts["input_read_ends"] += len(records)
                counts["mapped_primary_read_ends"] += len(reads)
                before.update(r.mapping_quality for r in reads)
                if not reads:
                    continue
                if len(reads) > (2 if layout == "PE" else 1):
                    raise ValueError("Repeated query names or mixed libraries in one BAM")
                org = any(r.reference_name in organelles for r in reads)
                low = any(
                    r.mapping_quality < mapq or (mapq > 0 and r.mapping_quality == 255)
                    for r in reads
                )
                dup = any(r.is_duplicate for r in reads)
                incomplete = layout == "PE" and fragment(reads, layout) is None
                # Independent reasons overlap; exclusion waterfall below is mutually exclusive.
                for name, flag in (
                    ("organellar_templates", org),
                    ("low_mapq_templates", low),
                    ("duplicate_templates", dup),
                    ("incomplete_templates", incomplete),
                ):
                    counts[name] += int(flag)
                if org:
                    counts["excluded_organellar_read_ends"] += len(reads)
                elif low:
                    counts["excluded_mapq_read_ends"] += len(reads)
                elif mapq > 0 and incomplete:
                    counts["excluded_pair_completeness_read_ends"] += len(reads)
                elif exclude_duplicates and dup:
                    counts["excluded_duplicate_read_ends"] += len(reads)
                else:
                    for read in reads:
                        writer.write(read)
                    counts["retained_read_ends"] += len(reads)
                    after.update(r.mapping_quality for r in reads)
        if (
            mapq == 0
            and not exclude_duplicates
            and not organelles
            and counts["input_read_ends"] == counts["retained_read_ends"]
        ):
            shutil.copyfile(source, output)
        else:
            pysam.sort("-m", "128M", "-o", str(output), str(unsorted))
        pysam.index(str(output))
    return dict(counts) | {
        "mapq_before": dict(sorted(before.items())),
        "mapq_after": dict(sorted(after.items())),
    }


def assess(
    bam: Path,
    peak_file: Path,
    fixed_file: Path,
    *,
    layout: str,
    raw_templates: int,
    organelles: set[str],
    temporary: Path,
) -> dict[str, Any]:
    peaks, scores = intervals(peak_file)
    fixed, _ = intervals(fixed_file)
    own_index, fixed_index = Regions(peaks), Regions(fixed)
    fixed_counts = [0] * len(fixed)
    counts: collections.Counter[str] = collections.Counter()
    mapqs: collections.Counter[int] = collections.Counter()
    lengths: collections.Counter[int] = collections.Counter()
    for records in groups(bam, temporary):
        reads = primary(records)
        counts["primary_mapped_read_ends"] += len(reads)
        counts["duplicate_flagged_read_ends"] += sum(r.is_duplicate for r in reads)
        counts["organellar_read_ends"] += sum(r.reference_name in organelles for r in reads)
        mapqs.update(r.mapping_quality for r in reads)
        for read in reads:
            end = read.reference_end
            chrom = read.reference_name
            if end is not None and chrom is not None:
                counts["read_ends_in_own_peaks"] += bool(
                    own_index.hits(chrom, read.reference_start, end)
                )
                counts["read_ends_in_fixed_peaks"] += bool(
                    fixed_index.hits(chrom, read.reference_start, end)
                )
        span = fragment(reads, layout)
        if span is None:
            counts["unresolved_templates"] += bool(reads)
            continue
        counts["fragments"] += 1
        counts["duplicate_flagged_fragments"] += any(r.is_duplicate for r in reads)
        lengths[span[2] - span[1]] += 1
        own_hits, fixed_hits = own_index.hits(*span), fixed_index.hits(*span)
        counts["fragments_in_own_peaks"] += bool(own_hits)
        counts["fragments_in_fixed_peaks"] += bool(fixed_hits)
        for index in fixed_hits:
            fixed_counts[index] += 1
    ends, fragments = counts["primary_mapped_read_ends"], counts["fragments"]
    raw_ends = raw_templates * (2 if layout == "PE" else 1)
    widths = [end - start for _, start, end in peaks]
    overlap = intersection(peaks, fixed)
    union = (
        sum(e - s for _, s, e in merged(peaks)) + sum(e - s for _, s, e in merged(fixed)) - overlap
    )
    metrics = {
        "read_retention": ratio(ends, raw_ends, "primary mapped read ends / raw read ends"),
        "fragment_retention": ratio(
            fragments, raw_templates, "complete same-contig template spans / raw templates"
        ),
        "duplicate_flag_fraction": ratio(
            counts["duplicate_flagged_read_ends"],
            ends,
            "flag 0x400 / retained mapped primary read ends; not a PCR estimate",
        ),
        "organellar_fraction": ratio(
            counts["organellar_read_ends"],
            ends,
            "declared organellar mapped read ends / retained mapped primary read ends",
        ),
        "peak_count": metric(len(peaks), "number of reported intervals", "peaks"),
        "peak_bases": metric(
            sum(e - s for _, s, e in merged(peaks)), "union of half-open peak intervals", "bp"
        ),
        "peak_width_median": metric(
            statistics.median(widths) if widths else None,
            "median end-start",
            "bp",
            len(widths),
            "no peaks",
        ),
        "peak_jaccard": ratio(
            overlap, union, "base-pair intersection / union versus frozen baseline intervals"
        ),
    }
    for unit, denominator in (("read_ends", ends), ("fragments", fragments)):
        for region in ("own", "fixed"):
            metrics[f"{unit}_FRiP_{region}"] = ratio(
                counts[f"{unit}_in_{region}_peaks"],
                denominator,
                f"any BED0 span overlap with {region} peaks / retained {unit}; "
                "union prevents double counting",
            )
    return {
        "metrics": metrics,
        "counts": dict(counts),
        "mapq_distribution": dict(sorted(mapqs.items())),
        "fragment_length_distribution": dict(sorted(lengths.items())),
        "peak_widths": widths,
        "fixed_regions": fixed,
        "fixed_counts": fixed_counts,
        "peak_regions": peaks,
        "peak_scores": scores,
        "layout": layout,
        "fragment_definition": (
            "SE aligned span; PE outer aligned span of two primary mates on same contig"
        ),
    }


def compare(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    if current["fixed_regions"] != baseline["fixed_regions"]:
        raise ValueError("Fixed evaluation regions differ")
    a, b = baseline["fixed_counts"], current["fixed_counts"]
    result = {}
    for rank, name in ((False, "pearson"), (True, "spearman")):
        result[f"fixed_count_{name}"] = metric(
            correlation(a, b, rank=rank),
            f"{name} of fragment counts over identical frozen baseline regions",
            "correlation",
            len(a),
            "fewer than two regions or zero variance",
        )
    deltas = [abs(x - y) for x, y in zip(a, b, strict=True)]
    result["fixed_count_absolute_change_median"] = metric(
        statistics.median(deltas) if deltas else None,
        "median absolute raw-fragment count difference on fixed regions",
        "fragments",
        len(a),
        "no regions",
    )
    # Mutual maximum-overlap matching, deterministic coordinate/index ties; unmatched
    # peaks are separately counted and never imputed a zero rank.
    left, right = baseline["peak_regions"], current["peak_regions"]

    def best(source: list[Interval], target: list[Interval]) -> dict[int, int]:
        index = Regions(target)
        matches = {}
        for i, (chrom, start, end) in enumerate(source):
            hits = index.hits(chrom, start, end)
            if hits:
                matches[i] = min(
                    hits,
                    key=lambda j: (
                        -(min(end, target[j][2]) - max(start, target[j][1])),
                        target[j],
                        j,
                    ),
                )
        return matches

    forward, backward = best(left, right), best(right, left)
    matches = [(i, j) for i, j in forward.items() if backward.get(j) == i]
    result["matched_peak_rank_spearman"] = metric(
        correlation(
            [baseline["peak_scores"][i] for i, _ in matches],
            [current["peak_scores"][j] for _, j in matches],
            rank=True,
        ),
        "narrowPeak -log10(q) ranks on reciprocal maximum-overlap matches; ties averaged",
        "correlation",
        len(matches),
        "fewer than two matches or zero score variance",
    )
    result["unmatched_baseline_peaks"] = metric(
        len(left) - len(matches), "baseline peaks without reciprocal match", "peaks"
    )
    result["unmatched_current_peaks"] = metric(
        len(right) - len(matches), "current peaks without reciprocal match", "peaks"
    )
    return result


def tn5_cuts(start: int, end: int) -> tuple[int, int]:
    """BED0 cut-base coordinates corresponding to shifted interval [start+4,end-5)."""
    if end - start < 10:
        raise ValueError("Fragment too short for +4/-5 interval")
    return start + 4, end - 6


def tss_enrichment(
    bam: Path, tss_file: Path, chrom_sizes: dict[str, int], temporary: Path
) -> dict[str, Any]:
    """Common ATAC profile: BED0 cut bases, +/-2000, 100-bp flank mean, center bin."""
    window = 2000
    tsses = []
    excluded = 0
    for line in tss_file.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        chrom, position, strand = line.split("\t")
        pos = int(position)
        if strand not in {"+", "-"} or chrom not in chrom_sizes or pos < 0:
            raise ValueError("Invalid three-column BED0 TSS point/strand")
        if pos < window or pos + window >= chrom_sizes[chrom]:
            excluded += 1
        else:
            tsses.append((chrom, pos, strand))
    windows = Regions([(chrom, pos - window, pos + window + 1) for chrom, pos, _ in tsses])
    profile = [0] * (2 * window + 1)
    short = 0
    for records in groups(bam, temporary):
        span = fragment(primary(records), "PE")
        if span is None:
            continue
        chrom, start, end = span
        if end - start < 10:
            short += 1
            continue
        for cut in tn5_cuts(start, end):
            for i in windows.hits(chrom, cut, cut + 1):
                _, pos, strand = tsses[i]
                offset = cut - pos if strand == "+" else pos - cut
                profile[offset + window] += 1
    background = (sum(profile[:100]) + sum(profile[-100:])) / 200
    value = profile[window] / background if tsses and background else None
    return {
        "metric": metric(
            value,
            "Tn5 BED0 cuts start+4/end-6; +/-2000; center bin divided by mean "
            "100bp flanks; full windows only",
            "fold_enrichment",
            background,
            "no full TSS windows or zero flank background",
        ),
        "profile": profile,
        "usable_TSS": len(tsses),
        "edge_excluded_TSS": excluded,
        "too_short_fragments": short,
        "annotation_sha256": hashlib.sha256(tss_file.read_bytes()).hexdigest(),
    }


def compare_quantification(current: Path, baseline: Path) -> dict[str, Any]:
    def values(path: Path) -> tuple[list[list[str]], list[float]]:
        rows = [line.split("\t") for line in path.read_text().splitlines() if line.strip()]
        return [r[:3] for r in rows], [float(r[-1]) for r in rows]

    ar, a = values(baseline)
    br, b = values(current)
    if ar != br:
        raise ValueError("Quantification region coordinates differ")
    absolute = [abs(x - y) for x, y in zip(a, b, strict=True)]
    relative = [abs(y - x) / abs(x) for x, y in zip(a, b, strict=True) if x != 0]
    return {
        "pearson": metric(
            correlation(a, b),
            "fixed-region normalized signal Pearson",
            "correlation",
            len(a),
            "too few regions or zero variance",
        ),
        "spearman": metric(
            correlation(a, b, rank=True),
            "fixed-region normalized signal Spearman",
            "correlation",
            len(a),
            "too few regions or zero variance",
        ),
        "median_absolute_change": metric(
            statistics.median(absolute) if absolute else None,
            "median absolute normalized-signal change",
            "signal",
            len(a),
            "no regions",
        ),
        "median_relative_change": metric(
            statistics.median(relative) if relative else None,
            "median absolute relative change; zero baseline regions excluded",
            "fraction",
            len(relative),
            "no nonzero baseline regions",
        ),
        "zero_baseline_regions": metric(
            sum(x == 0 for x in a), "regions excluded from relative-change denominator", "regions"
        ),
    }
