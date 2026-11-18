"""Read-based ATAC checks per run, from the first read pairs mapped with chromap.

The first 200,000 read pairs of each run are streamed (records validated while reading, as in
`strand.sample_reads`; a mate file cut short by a dropped connection keeps the pairs both
mates have) and mapped with `chromap --preset atac`, which writes deduplicated fragments.
From the fragments:

- mapped_fraction: fragments over sampled pairs;
- organelle_fraction: fragments on the organelle contigs;
- tss_enrichment: the pipeline's definition. Tn5 cut sites (fragment start + 4, end - 5)
  within 2 kb of each gene's TSS (start of + genes, end of - genes); the mean count in the
  central 100 bp over the mean of the outer 100 bp at each end;
- tss_peak: the highest 100 bp mean within 1 kb over the same flanks (plant promoters'
  accessible peak often sits upstream of the annotated TSS);
- nfr_fraction (fragments under 147 bp), median_fragment and genic_fraction (fragment
  midpoints inside genes), on nuclear fragments;
- promoter_fraction: nuclear cut sites within 1 kb upstream to 200 bp downstream of a TSS,
  a FRiP-like share that needs no peak calling (see `atac.py` for how it is used).

Metrics are appended to `metrics.tsv` in the work folder after each run, so an interrupted
measurement resumes; failed downloads are not recorded and are retried on the next build.
"""

from __future__ import annotations

import bisect
import csv
import gzip
import os
import shutil
import statistics
import subprocess
import threading
from collections import defaultdict
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

from .strand import http_chunks, sample_reads

PAIRS = 200_000
MIN_PAIRS = 20_000
WINDOW = 2_000
NFR_LENGTH = 147
PROMOTER = (1_000, 200)  # upstream, downstream of the TSS


@dataclass(frozen=True)
class Tools:
    chromap: str
    threads: int = 2  # per run; runs are mapped in parallel

    @classmethod
    def find(cls, chromap: str | None = None, threads: int = 2) -> Tools:
        path = chromap or shutil.which("chromap") or ""
        if not os.access(path, os.X_OK):
            raise ValueError("ATAC checks need chromap; give its path with --chromap")
        return cls(path, threads)


@dataclass(frozen=True)
class Metrics:
    run: str
    pairs: int = 0
    mapped_fragments: int = 0
    mapped_fraction: str = "NA"
    organelle_fraction: str = "NA"
    tss_enrichment: str = "NA"
    tss_peak: str = "NA"
    nfr_fraction: str = "NA"
    median_fragment: str = "NA"
    genic_fraction: str = "NA"
    promoter_fraction: str = "NA"
    failed: str = ""  # why no fragments were measured ('' when measured)


def number(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


class Genes:
    """TSSs and merged gene spans per contig, from a BED6 gene file."""

    def __init__(self, bed: Path) -> None:
        tss: dict[str, list[tuple[int, str]]] = defaultdict(list)
        spans: dict[str, list[tuple[int, int]]] = defaultdict(list)
        opener = gzip.open if bed.suffix == ".gz" else open
        with opener(bed, "rt") as handle:
            for line in handle:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 6 or line.startswith(("#", "track")):
                    continue
                contig, start, end, strand = parts[0], int(parts[1]), int(parts[2]), parts[5]
                tss[contig].append((start, "+") if strand != "-" else (end - 1, "-"))
                spans[contig].append((start, end))
        self.tss = {c: sorted(v) for c, v in tss.items()}
        self.positions = {c: [p for p, _ in v] for c, v in self.tss.items()}
        self.merged: dict[str, tuple[list[int], list[int]]] = {}
        for contig, values in spans.items():
            merged: list[list[int]] = []
            for start, end in sorted(values):
                if merged and start <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], end)
                else:
                    merged.append([start, end])
            self.merged[contig] = ([s for s, _ in merged], [e for _, e in merged])

    def genic(self, contig: str, point: int) -> bool:
        if contig not in self.merged:
            return False
        starts, ends = self.merged[contig]
        i = bisect.bisect_right(starts, point) - 1
        return i >= 0 and point < ends[i]


def fragment_metrics(
    run: str, pairs: int, fragments: Iterator[str], genes: Genes, organelles: set[str]
) -> Metrics:
    """Metrics of one run from chromap fragment lines (contig, start, end, ...)."""
    profile = [0] * (2 * WINDOW + 1)
    total = organelle = nfr = genic = promoter = cuts = 0
    lengths: list[int] = []
    upstream, downstream = PROMOTER
    for line in fragments:
        contig, start_text, end_text = line.split("\t", 3)[:3]
        start, end = int(start_text), int(end_text)
        total += 1
        if contig in organelles:
            organelle += 1
            continue
        length = end - start
        lengths.append(length)
        nfr += length < NFR_LENGTH
        genic += genes.genic(contig, (start + end) // 2)
        positions = genes.positions.get(contig)
        if positions is None:
            cuts += 2
            continue
        sites = genes.tss[contig]
        for cut in (start + 4, end - 5):
            cuts += 1
            near = False
            for i in range(
                bisect.bisect_left(positions, cut - WINDOW),
                bisect.bisect_right(positions, cut + WINDOW),
            ):
                site, strand = sites[i]
                offset = cut - site if strand == "+" else site - cut
                profile[offset + WINDOW] += 1
                near = near or -upstream <= offset <= downstream
            promoter += near
    flank = (sum(profile[:100]) + sum(profile[-100:])) / 200
    center = sum(profile[WINDOW - 50 : WINDOW + 50]) / 100
    peak = max(
        (sum(profile[i - 50 : i + 50]) / 100 for i in range(WINDOW - 1000, WINDOW + 1001, 10)),
        default=0.0,
    )
    nuclear = len(lengths)
    return Metrics(
        run=run,
        pairs=pairs,
        mapped_fragments=total,
        mapped_fraction=f"{total / pairs:.3f}" if pairs else "NA",
        organelle_fraction=f"{organelle / total:.3f}" if total else "NA",
        tss_enrichment=f"{center / flank:.2f}" if flank else "NA",
        tss_peak=f"{peak / flank:.2f}" if flank else "NA",
        nfr_fraction=f"{nfr / nuclear:.3f}" if nuclear else "NA",
        median_fragment=f"{statistics.median(lengths):g}" if lengths else "NA",
        genic_fraction=f"{genic / nuclear:.3f}" if nuclear else "NA",
        promoter_fraction=f"{promoter / cuts:.4f}" if cuts else "NA",
    )


def pair_files(read1: Path, read2: Path) -> int:
    """Cut both mate files to the pairs both have; returns that number of pairs."""
    counts = [sum(1 for _ in path.open("rb")) // 4 for path in (read1, read2)]
    pairs = min(counts)
    for path, count in zip((read1, read2), counts, strict=True):
        if count > pairs:
            part = path.with_suffix(".cut")
            with path.open("rb") as source, part.open("wb") as out:
                for _, line in zip(range(4 * pairs), source, strict=False):
                    out.write(line)
            part.replace(path)
    return pairs


def chromap_index(fasta: Path, target: Path, tools: Tools) -> Path:
    """Build the chromap index of `fasta` at `target` unless it exists."""
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_suffix(".part")
        subprocess.run(
            [tools.chromap, "-i", "-r", str(fasta), "-o", str(part)],
            check=True,
            capture_output=True,
        )
        part.replace(target)
    return target


Sampler = Callable[[str, str, Path, int], int]


def default_sampler(run: str, url: str, target: Path, pairs: int) -> int:
    return sample_reads(run, url, target, pairs, chunks=http_chunks)


def map_run(
    run: str,
    urls: tuple[str, str],
    work: Path,
    fasta: Path,
    index: Path,
    genes: Genes,
    organelles: set[str],
    tools: Tools,
    pairs: int = PAIRS,
    sampler: Sampler = default_sampler,
) -> Metrics:
    folder = work / "runs" / run
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    try:
        mates = [folder / "r1.fq", folder / "r2.fq"]
        with ThreadPoolExecutor(2) as pool:
            got = list(pool.map(lambda i: sampler(run, urls[i], mates[i], pairs), (0, 1)))
        if min(got) < MIN_PAIRS:
            return Metrics(run=run, failed=f"download failed ({got[0]} and {got[1]} reads)")
        sampled = pair_files(*mates)
        fragments = folder / "fragments.bed"
        done = subprocess.run(
            [
                tools.chromap,
                "--preset",
                "atac",
                "-x",
                str(index),
                "-r",
                str(fasta),
                "-1",
                str(mates[0]),
                "-2",
                str(mates[1]),
                "-t",
                str(tools.threads),
                "-o",
                str(fragments),
            ],
            capture_output=True,
            text=True,
        )
        if done.returncode != 0:
            return Metrics(run=run, pairs=sampled, failed="chromap failed")
        with fragments.open() as handle:
            return fragment_metrics(run, sampled, handle, genes, organelles)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def load_metrics(path: Path) -> dict[str, Metrics]:
    if not path.exists():
        return {}
    found = {}
    text = {f.name for f in fields(Metrics)} - {"run", "pairs", "mapped_fragments"}
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            item = Metrics(
                run=row["run"],
                pairs=int(row.get("pairs") or 0),
                mapped_fragments=int(row.get("mapped_fragments") or 0),
            )
            found[row["run"]] = replace(item, **{k: row[k] for k in text if row.get(k)})
    return found


def measure(
    runs: dict[str, tuple[str, str]],
    work: Path,
    fasta: Path,
    index: Path,
    genes_bed: Path,
    organelles: set[str],
    tools: Tools,
    downloads: int = 8,
    pairs: int = PAIRS,
    sampler: Sampler = default_sampler,
    log: Callable[[str], None] = print,
) -> dict[str, Metrics]:
    """Metrics for each run (run -> read-1 and read-2 URLs), at most `downloads` (8) runs at
    once. Runs already in `work/metrics.tsv` are not measured again."""
    work.mkdir(parents=True, exist_ok=True)
    store = work / "metrics.tsv"
    done = load_metrics(store)
    todo = [run for run in sorted(runs) if run not in done]
    if todo:
        genes = Genes(genes_bed)
        header = [f.name for f in fields(Metrics)]
        lock = threading.Lock()
        new = not store.exists()
        with store.open("a", newline="") as handle:
            writer = csv.DictWriter(handle, header, delimiter="\t", lineterminator="\n")
            if new:
                writer.writeheader()
                handle.flush()

            def one(run: str) -> Metrics:
                item = map_run(
                    run, runs[run], work, fasta, index, genes, organelles, tools, pairs, sampler
                )
                with lock:
                    if not item.failed.startswith("download failed"):
                        writer.writerow(asdict(item))
                        handle.flush()
                    done[run] = item
                    count = sum(1 for r in todo if r in done)
                    if count % 20 == 0 or count == len(todo):
                        log(f"atac checks: {count}/{len(todo)} runs")
                return item

            with ThreadPoolExecutor(min(downloads, 8)) as pool:
                list(pool.map(one, todo))
    return {run: done[run] for run in runs if run in done}
