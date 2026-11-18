"""TSS signal, 5' trim and 3' adapter of a run, from its first reads.

The first 20,000 reads of the TSS read file are streamed from ENA (validated record by record,
as for strandedness). From them:
- adapter rates: the share of reads holding each known 3' adapter (first 10 bases);
- the share of reads starting with G (a cap-trapping library adds a non-templated G);
- the Smart-seq2 primer rate (Smar2C2 TSS reads carry it, in either mate);
- a trim sweep: the reads are cut by 0-15 bases at the 5' end, clipped at any known adapter and
  mapped together with STAR the way the tss-seq workflow maps (5' end never soft-clipped, or
  local alignment for template-switching methods; unique alignments only). For each trim the
  5' ends of uniquely mapped reads are compared with annotated transcript starts on the same
  strand: the share within 10 and 100 bases and the most common offset within 10 bases
  (positive = downstream), which a wrong trim moves by one per base.
Results are appended to `profiles.tsv` and `sweep.tsv` in the work folder after every batch, so
an interrupted measurement resumes; the trim is chosen later from the sweep (`choose_trim`), so
a changed method rule needs no new measurement.
"""

from __future__ import annotations

import bisect
import csv
import gzip
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from .strand import sample_reads

READS = 20_000
TRIMS = tuple(range(16))
MIN_LENGTH = 18
NEAR, CLOSE = 100, 10
TRUSEQ = "AGATCGGAAGAGC"
# 3' adapters seen in TSS libraries, as written to sheets. The small-RNA index adapter is what
# CAGE libraries built with small-RNA kits read into (GSE136356).
ADAPTERS = {
    "TruSeq": TRUSEQ,
    "small RNA index": "TCGTATGCCGTCTTCTGCTTG",
    "small RNA": "TGGAATTCTCGGGTGCCAAGG",
    "Nextera": "CTGTCTCTTATACACATCT",
}
ADAPTER_PROBE = 10  # leading bases searched for
MIN_ADAPTER_RATE = 0.01
SMART_PRIMER = "AAGCAGTGGTATCAACGCAGAGTAC"
UMI = 12
# The primer with up to one mismatch, then the UMI and AT (as TSS_EXTRACT matches it).
SMART = re.compile(
    "(?:"
    + "|".join(SMART_PRIMER[:i] + "." + SMART_PRIMER[i + 1 :] for i in range(len(SMART_PRIMER)))
    + f")[ACGTN]{{{UMI}}}AT"
)


@dataclass(frozen=True)
class Tools:
    star: str
    threads: int = 16

    @classmethod
    def find(cls, star: str | None = None, threads: int = 16) -> Tools:
        path = star or shutil.which("STAR") or ""
        if not Path(path).is_file():
            raise ValueError("TSS measurement needs STAR; give its path with --star")
        return cls(path, threads)


@dataclass(frozen=True)
class Profile:
    """Read-structure measurements of one run's sample."""

    run: str
    reads: int
    adapter_rates: dict[str, float] = field(default_factory=dict)
    first_g: float = 0.0
    smart_rate: float = 0.0

    @property
    def adapter(self) -> str:
        """The most frequent known adapter, TruSeq when none reaches 1%."""
        name, rate = max(self.adapter_rates.items(), key=lambda kv: kv[1], default=("", 0.0))
        return ADAPTERS[name] if rate >= MIN_ADAPTER_RATE else TRUSEQ


@dataclass(frozen=True)
class TrimStats:
    trim: int
    reads: int  # reads given to STAR after trimming and the length floor
    unique: int
    near_close: int
    near: int
    offset_mode: int | None

    def unique_fraction(self, sampled: int) -> float:
        return self.unique / sampled if sampled else 0.0

    @property
    def near_fraction(self) -> float:
        return self.near / self.unique if self.unique else 0.0


@dataclass(frozen=True)
class Measurement:
    profile: Profile
    sweep: dict[int, TrimStats]


def choose_trim(sweep: dict[int, TrimStats], target: int) -> int:
    """The trim whose offset mode is `target` (0, or -1 for a cap G the workflow removes) with
    the most 5' ends within 10 bases of a start; without one, the trim with the most such ends.
    Ties go to the smaller trim."""
    if not sweep:
        return 0
    hits = [s for s in sweep.values() if s.offset_mode == target]
    best = max(sweep.values(), key=lambda s: (s.near_close, -s.trim))
    if hits:
        chosen = max(hits, key=lambda s: (s.near_close, -s.trim))
        # A mode reached only on a trim where few reads map is noise.
        if chosen.near_close >= 0.5 * best.near_close:
            return chosen.trim
    return best.trim


def offset_mode(offsets: Counter[int]) -> int | None:
    """Most common offset; ties go to the nearest, then upstream (as TSS_QC breaks them)."""
    if not offsets:
        return None
    return min(offsets, key=lambda d: (-offsets[d], abs(d), d))


def transcript_starts(annotation: Path) -> dict[tuple[str, str], list[int]]:
    """0-based start of every transcript (a feature that is the parent of exons), by contig and
    strand: the starts gffread's GTF gives TSS_QC."""
    opener = gzip.open if annotation.suffix == ".gz" else open
    features: dict[str, tuple[str, str, int]] = {}
    parents: set[str] = set()
    with opener(annotation, "rt") as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 9:
                continue
            attributes = dict(item.split("=", 1) for item in cols[8].split(";") if "=" in item)
            if cols[2] == "exon":
                parents.update(attributes.get("Parent", "").split(","))
            elif "ID" in attributes:
                start = int(cols[3]) - 1 if cols[6] == "+" else int(cols[4]) - 1
                features[attributes["ID"]] = (cols[0], cols[6], start)
    starts: dict[tuple[str, str], set[int]] = defaultdict(set)
    for name in parents:
        if name in features:
            contig, strand, start = features[name]
            starts[(contig, strand)].add(start)
    return {key: sorted(values) for key, values in starts.items()}


def nearest(starts: list[int], five: int, strand: str) -> int | None:
    """Offset of a 5' end to the nearest start within 100 bases (positive = downstream)."""
    i = bisect.bisect_left(starts, five - NEAR)
    best: int | None = None
    while i < len(starts) and starts[i] <= five + NEAR:
        d = five - starts[i] if strand == "+" else starts[i] - five
        if best is None or abs(d) < abs(best):
            best = d
        i += 1
    return best


CIGAR = re.compile(r"(\d+)([MIDNSHP=X])")


def five_prime(flag: int, position: int, cigar: str) -> tuple[str, int]:
    """Strand and 0-based genomic position of a read's first aligned base."""
    span = sum(int(n) for n, op in CIGAR.findall(cigar) if op in "MDN=X")
    if flag & 16:
        return "-", position - 1 + span - 1
    return "+", position - 1


def sam_stats(
    sam: Path, starts: dict[tuple[str, str], list[int]]
) -> dict[tuple[str, int], tuple[int, int, int, Counter[int]]]:
    """(run, trim) -> unique reads, ends within 10 and 100 bases, offsets within 10 bases.
    Read names are `<run>:<trim>:<name>`."""
    stats: dict[tuple[str, int], tuple[int, int, int, Counter[int]]] = {}
    with sam.open() as handle:
        for line in handle:
            if line.startswith("@"):
                continue
            cols = line.split("\t", 6)
            flag = int(cols[1])
            if flag & (4 | 256 | 2048):
                continue
            run, trim, _ = cols[0].split(":", 2)
            key = (run, int(trim))
            unique, close, near, offsets = stats.get(key) or (0, 0, 0, Counter())
            strand, five = five_prime(flag, int(cols[3]), cols[5])
            d = nearest(starts.get((cols[2], strand), []), five, strand)
            if d is not None:
                near += 1
                if abs(d) <= CLOSE:
                    close += 1
                    offsets[d] += 1
            stats[key] = (unique + 1, close, near, offsets)
    return stats


def clip(sequence: bytes) -> int:
    """Length of the read before the first known adapter."""
    cut = len(sequence)
    for adapter in ADAPTERS.values():
        at = sequence.find(adapter[:ADAPTER_PROBE].encode())
        if 0 <= at < cut:
            cut = at
    return cut


def read_fastq(path: Path) -> list[tuple[bytes, bytes, bytes]]:
    lines = path.read_bytes().split(b"\n")
    return [(lines[i][1:], lines[i + 1], lines[i + 3]) for i in range(0, len(lines) - 3, 4)]


def smart_reads(records: Iterable[tuple[bytes, bytes, bytes]]) -> list[tuple[bytes, bytes, bytes]]:
    """Smar2C2 TSS reads: the part after the primer, UMI and AT."""
    kept = []
    for name, sequence, quality in records:
        match = SMART.search(sequence.decode(errors="replace"))
        if match:
            kept.append((name, sequence[match.end() :], quality[match.end() :]))
    return kept


def profile(run: str, records: list[tuple[bytes, bytes, bytes]]) -> Profile:
    n = len(records)
    if not n:
        return Profile(run, 0)
    rates = {
        name: round(sum(adapter[:ADAPTER_PROBE].encode() in s for _, s, _ in records) / n, 4)
        for name, adapter in ADAPTERS.items()
    }
    return Profile(
        run=run,
        reads=n,
        adapter_rates=rates,
        first_g=round(sum(s[:1] == b"G" for _, s, _ in records) / n, 4),
        smart_rate=round(len(smart_reads(records)) / n, 4),
    )


def write_sweep_reads(
    run: str, records: list[tuple[bytes, bytes, bytes]], trims: Iterable[int], out: BinaryIO
) -> dict[int, int]:
    """Write each record once per trim, cut and adapter-clipped, as `<run>:<trim>:<name>`;
    returns the reads written per trim (those still at least 18 bases long)."""
    written: dict[int, int] = {}
    for trim in trims:
        count = 0
        for name, sequence, quality in records:
            end = clip(sequence)
            if end - trim < MIN_LENGTH:
                continue
            out.write(
                b"@%s:%d:%s\n%s\n+\n%s\n"
                % (
                    run.encode(),
                    trim,
                    name.split(b":", 1)[-1],
                    sequence[trim:end],
                    quality[trim:end],
                )
            )
            count += 1
        written[trim] = count
    return written


SWEEP_FIELDS = ("run", "trim", "reads", "unique", "near_close", "near", "offset_mode")
PROFILE_FIELDS = ("run", "reads", "first_g", "smart_rate", *(f"rate_{k}" for k in ADAPTERS))


def load(work: Path) -> dict[str, Measurement]:
    profiles: dict[str, Profile] = {}
    path = work / "profiles.tsv"
    if path.exists():
        with path.open(newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                profiles[row["run"]] = Profile(
                    run=row["run"],
                    reads=int(row["reads"]),
                    adapter_rates={k: float(row[f"rate_{k}"]) for k in ADAPTERS},
                    first_g=float(row["first_g"]),
                    smart_rate=float(row["smart_rate"]),
                )
    sweeps: dict[str, dict[int, TrimStats]] = defaultdict(dict)
    path = work / "sweep.tsv"
    if path.exists():
        with path.open(newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                mode = row["offset_mode"]
                sweeps[row["run"]][int(row["trim"])] = TrimStats(
                    trim=int(row["trim"]),
                    reads=int(row["reads"]),
                    unique=int(row["unique"]),
                    near_close=int(row["near_close"]),
                    near=int(row["near"]),
                    offset_mode=int(mode) if mode not in ("", "NA") else None,
                )
    return {run: Measurement(p, dict(sweeps.get(run, {}))) for run, p in profiles.items()}


def append_rows(path: Path, fields: tuple[str, ...], rows: list[dict[str, object]]) -> None:
    new = not path.exists()
    with path.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fields, delimiter="\t", lineterminator="\n")
        if new:
            writer.writeheader()
        writer.writerows(rows)


def map_batch(reads: Path, index: Path, tools: Tools, folder: Path, local: bool) -> Path:
    subprocess.run(
        [
            tools.star,
            "--runThreadN",
            str(tools.threads),
            "--genomeDir",
            str(index),
            "--readFilesIn",
            str(reads),
            "--alignEndsType",
            "Local" if local else "Extend5pOfRead1",
            "--outFilterMultimapNmax",
            "1",
            "--alignIntronMax",
            "10000",
            "--outSAMtype",
            "SAM",
            "--outSAMattributes",
            "NH",
            "--outFileNamePrefix",
            f"{folder}/",
        ],
        check=True,
        capture_output=True,
    )
    return folder / "Aligned.out.sam"


@dataclass(frozen=True)
class Job:
    """One run to measure: its TSS read file(s), and how its reads are aligned."""

    run: str
    urls: tuple[str, ...]  # one file; Smar2C2 gives both mates
    local: bool = False  # template switching: soft-clip the 5' Gs
    smart: bool = False  # Smar2C2: keep the reads after the primer, UMI and AT


def measure(
    jobs: list[Job],
    work: Path,
    index: Path,
    annotation: Path,
    tools: Tools,
    downloads: int = 8,
    batch_size: int = 100,
    reads: int = READS,
    sample: Callable[[str, str, Path, int], int] = sample_reads,
    log: Callable[[str], None] = print,
) -> dict[str, Measurement]:
    """Measurements of each job's run, from `work` when already measured. Runs whose download
    failed get a profile with 0 reads and are retried on the next build."""
    work.mkdir(parents=True, exist_ok=True)
    done = load(work)
    todo = [job for job in sorted(jobs, key=lambda j: j.run) if job.run not in done]
    failed: dict[str, Measurement] = {}
    if todo:
        starts = transcript_starts(annotation)
        sampled = work / "reads"
        sampled.mkdir(exist_ok=True)

        def fetch(job: Job) -> list[tuple[bytes, bytes, bytes]]:
            records = []
            for mate, url in enumerate(job.urls, 1):
                target = sampled / f"{job.run}_{mate}.fq"
                if sample(job.run, url, target, reads):
                    records += read_fastq(target)
                target.unlink(missing_ok=True)
            return records

        with ThreadPoolExecutor(min(downloads, 8)) as pool:
            for start in range(0, len(todo), batch_size):
                batch = todo[start : start + batch_size]
                folder = work / "batch"
                shutil.rmtree(folder, ignore_errors=True)
                folder.mkdir(parents=True)
                profiles, written = {}, {}
                for local in (False, True):
                    group = [job for job in batch if job.local == local]
                    if not group:
                        continue
                    combined = folder / ("local.fq" if local else "end-to-end.fq")
                    with combined.open("wb") as out:
                        for job, records in zip(group, pool.map(fetch, group), strict=True):
                            profiles[job.run] = profile(job.run, records)
                            if job.smart:
                                records = smart_reads(records)
                            trims = (0,) if local else TRIMS
                            written[job.run] = write_sweep_reads(job.run, records, trims, out)
                    stats = sam_stats(map_batch(combined, index, tools, folder, local), starts)
                    for path in folder.iterdir():
                        if path.is_file():
                            path.unlink()
                    rows = []
                    for job in group:
                        if not profiles[job.run].reads:
                            continue
                        for trim, n in written[job.run].items():
                            unique, close, near, offsets = stats.get(
                                (job.run, trim), (0, 0, 0, Counter())
                            )
                            mode = offset_mode(offsets)
                            rows.append(
                                {
                                    "run": job.run,
                                    "trim": trim,
                                    "reads": n,
                                    "unique": unique,
                                    "near_close": close,
                                    "near": near,
                                    "offset_mode": "NA" if mode is None else mode,
                                }
                            )
                    append_rows(work / "sweep.tsv", SWEEP_FIELDS, rows)
                kept = [p for p in profiles.values() if p.reads]
                append_rows(
                    work / "profiles.tsv",
                    PROFILE_FIELDS,
                    [
                        {
                            "run": p.run,
                            "reads": p.reads,
                            "first_g": p.first_g,
                            "smart_rate": p.smart_rate,
                            **{f"rate_{k}": v for k, v in p.adapter_rates.items()},
                        }
                        for p in kept
                    ],
                )
                failed |= {run: Measurement(p, {}) for run, p in profiles.items() if not p.reads}
                shutil.rmtree(folder, ignore_errors=True)
                log(f"tss: {min(start + batch_size, len(todo))}/{len(todo)} runs measured")
        done = load(work)
    return {job.run: done.get(job.run) or failed[job.run] for job in jobs}
