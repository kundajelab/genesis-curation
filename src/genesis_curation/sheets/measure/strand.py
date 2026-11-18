"""Library strandedness per run, from the first reads of read 1.

The first 20,000 read-1 reads of each run are streamed from ENA (records are validated while
reading, so truncated or NUL-corrupted downloads are caught), tagged with the run, aligned in
batches with STAR (unique alignments only) and counted on genes with featureCounts on each
strand (`-s 1`, `-s 2`). The forward fraction gives the call: above 0.8 forward, below 0.2
reverse, 0.35-0.65 unstranded, anything else unclear; under 2,000 reads on genes is "too few
reads on genes". Calls are appended to `calls.tsv` in the work folder after every batch, so
an interrupted measurement resumes where it stopped.
"""

from __future__ import annotations

import csv
import os
import shutil
import subprocess
import zlib
from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from urllib.request import Request, urlopen

READS = 20_000
MIN_GENE_READS = 2_000
MAX_READ_LENGTH = 600  # STAR refuses reads over 650 bases
CALLS = ("forward", "reverse", "unstranded")


@dataclass(frozen=True)
class Tools:
    star: str
    featurecounts: str
    gffread: str
    threads: int = 16

    @classmethod
    def find(
        cls,
        star: str | None = None,
        featurecounts: str | None = None,
        gffread: str | None = None,
        threads: int = 16,
    ) -> Tools:
        found = {
            name: given or shutil.which(name) or ""
            for name, given in (
                ("STAR", star),
                ("featureCounts", featurecounts),
                ("gffread", gffread),
            )
        }
        missing = [name for name, path in found.items() if not os.access(path, os.X_OK)]
        if missing:
            raise ValueError(f"Strandedness needs {', '.join(missing)}; give executable paths")
        return cls(found["STAR"], found["featureCounts"], found["gffread"], threads)


@dataclass(frozen=True)
class StrandCall:
    run: str
    strandedness: str  # forward | reverse | unstranded | unclear | too few reads on genes | ...
    reads_sampled: int = 0
    forward_reads: int = 0
    reverse_reads: int = 0
    forward_fraction: str = "NA"
    gene_fraction: str = "NA"


def call(forward: int, reverse: int) -> str:
    total = forward + reverse
    if total < MIN_GENE_READS:
        return "too few reads on genes"
    fraction = forward / total
    if fraction > 0.8:
        return "forward"
    if fraction < 0.2:
        return "reverse"
    return "unstranded" if 0.35 <= fraction <= 0.65 else "strandedness unclear"


def result(run: str, reads: int, forward: int, reverse: int) -> StrandCall:
    total = forward + reverse
    return StrandCall(
        run=run,
        strandedness=call(forward, reverse) if reads else "download failed",
        reads_sampled=reads,
        forward_reads=forward,
        reverse_reads=reverse,
        forward_fraction=f"{forward / total:.3f}" if total else "NA",
        gene_fraction=f"{total / reads:.3f}" if reads else "NA",
    )


def fastq_records(chunks: Iterator[bytes]) -> Iterator[tuple[bytes, bytes, bytes]]:
    """(name, sequence, quality) of each record of a gzipped FASTQ stream (concatenated gzip
    members included). Raises ValueError at the first invalid record or NUL byte."""
    inflate = zlib.decompressobj(31)
    buffer = b""

    def lines() -> Iterator[bytes]:
        nonlocal buffer, inflate
        for chunk in chunks:
            data = inflate.decompress(chunk)
            while inflate.eof and inflate.unused_data:
                rest = inflate.unused_data
                inflate = zlib.decompressobj(31)
                data += inflate.decompress(rest)
            buffer += data
            *complete, buffer = buffer.split(b"\n")
            yield from complete
        if buffer:
            yield buffer

    stream = lines()
    while True:
        record = [line.rstrip(b"\r") for _, line in zip(range(4), stream, strict=False)]
        if not record:
            return
        if len(record) < 4:
            raise ValueError("truncated FASTQ record")
        name, sequence, plus, quality = record
        if b"\0" in name + sequence + plus + quality:
            raise ValueError("NUL bytes in FASTQ")
        if not name.startswith(b"@") or not plus.startswith(b"+") or len(sequence) != len(quality):
            raise ValueError("invalid FASTQ record")
        yield name, sequence, quality


def http_chunks(url: str, size: int = 1 << 16) -> Iterator[bytes]:
    request = Request(url, headers={"User-Agent": "genesis-curation"})
    with urlopen(request, timeout=300) as response:
        while chunk := response.read(size):
            yield chunk


def sample_reads(
    run: str,
    url: str,
    target: Path,
    reads: int = READS,
    chunks: Callable[[str], Iterator[bytes]] = http_chunks,
    attempts: int = 3,
) -> int:
    """Write the first `reads` valid reads of `url` to `target`, names prefixed `<run>:`.
    Returns the number written (0 when nothing could be read). Reads over 600 bases are
    skipped. An invalid record, NUL bytes or a broken connection retries the download; after
    the last attempt the valid reads before the fault are kept."""
    if target.exists():
        return sum(1 for _ in target.open("rb")) // 4
    part = target.with_suffix(".part")
    written = 0
    for attempt in range(attempts):
        written = 0
        try:
            with part.open("wb") as out:
                for name, sequence, quality in fastq_records(chunks(url)):
                    if len(sequence) > MAX_READ_LENGTH:
                        continue
                    out.write(b"@%s:%s\n%s\n+\n%s\n" % (run.encode(), name[1:], sequence, quality))
                    written += 1
                    if written >= reads:
                        break
        except OSError, ValueError, zlib.error:
            if attempt < attempts - 1:
                continue
        break
    if written:
        part.replace(target)
    else:
        part.unlink(missing_ok=True)
    return written


def assigned(core: Path) -> Counter[str]:
    """Reads assigned to a gene per run, from a featureCounts `-R CORE` file."""
    counts: Counter[str] = Counter()
    with core.open() as handle:
        for line in handle:
            name, status = line.split("\t", 2)[:2]
            if status == "Assigned":
                counts[name.split(":", 1)[0]] += 1
    return counts


def annotation_gtf(annotation: Path, tools: Tools, work: Path) -> Path:
    gtf = work / (annotation.name.removesuffix(".gz") + ".gtf")
    if not gtf.exists():
        plain = work / "annotation.gff3"
        with plain.open("wb") as out:
            data = annotation.read_bytes()
            out.write(zlib.decompress(data, 31) if annotation.suffix == ".gz" else data)
        subprocess.run(
            [tools.gffread, str(plain), "-T", "-o", str(gtf) + ".part"],
            check=True,
            capture_output=True,
        )
        Path(str(gtf) + ".part").replace(gtf)
        plain.unlink()
    return gtf


def count_batch(
    batch: dict[str, int], reads: Path, index: Path, gtf: Path, tools: Tools, work: Path
) -> list[StrandCall]:
    """Align the sampled reads of a batch of runs together and call each run."""
    with_reads = [run for run, n in batch.items() if n]
    calls = {run: result(run, 0, 0, 0) for run in batch if not batch[run]}
    if with_reads:
        folder = work / "batch"
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        combined = folder / "reads.fq"
        with combined.open("wb") as out:
            for run in with_reads:
                out.write((reads / f"{run}.fq").read_bytes())
        subprocess.run(
            [
                tools.star,
                "--runThreadN",
                str(tools.threads),
                "--genomeDir",
                str(index),
                "--readFilesIn",
                str(combined),
                "--outSAMtype",
                "BAM",
                "Unsorted",
                "--outFilterMultimapNmax",
                "1",
                "--outFileNamePrefix",
                f"{folder}/",
            ],
            check=True,
            capture_output=True,
        )
        strands = []
        for strand in ("1", "2"):
            subprocess.run(
                [
                    tools.featurecounts,
                    "-T",
                    str(min(tools.threads, 8)),
                    "-s",
                    strand,
                    "-a",
                    str(gtf),
                    "-o",
                    str(folder / f"s{strand}.txt"),
                    "-R",
                    "CORE",
                    "--Rpath",
                    str(folder),
                    str(folder / "Aligned.out.bam"),
                ],
                check=True,
                capture_output=True,
            )
            core = folder / "Aligned.out.bam.featureCounts"
            strands.append(assigned(core))
            core.unlink()
        for run in with_reads:
            calls[run] = result(run, batch[run], strands[0][run], strands[1][run])
        shutil.rmtree(folder)
    return [calls[run] for run in batch]


def load_calls(path: Path) -> dict[str, StrandCall]:
    if not path.exists():
        return {}
    calls = {}
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            calls[row["run"]] = StrandCall(
                run=row["run"],
                strandedness=row["strandedness"],
                reads_sampled=int(row.get("reads_sampled") or 0),
                forward_reads=int(row.get("forward_reads") or 0),
                reverse_reads=int(row.get("reverse_reads") or 0),
                forward_fraction=row.get("forward_fraction") or "NA",
                gene_fraction=row.get("gene_fraction") or "NA",
            )
    return calls


def measure(
    runs: dict[str, str],
    work: Path,
    index: Path,
    annotation: Path,
    tools: Tools,
    downloads: int = 8,
    batch_size: int = 400,
    reads: int = READS,
    log: Callable[[str], None] = print,
) -> dict[str, StrandCall]:
    """Strandedness for each run (run -> read-1 URL). Runs already in `work/calls.tsv` are
    not measured again; read samples are deleted once their batch is counted."""
    work.mkdir(parents=True, exist_ok=True)
    store = work / "calls.tsv"
    done = load_calls(store)
    todo = [run for run in sorted(runs) if run not in done]
    if not todo:
        return {run: done[run] for run in runs}
    sampled = work / "reads"
    sampled.mkdir(exist_ok=True)
    gtf = annotation_gtf(annotation, tools, work)
    header = [f.name for f in fields(StrandCall)]
    new = not store.exists()
    with store.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, header, delimiter="\t", lineterminator="\n")
        if new:
            writer.writeheader()
        with ThreadPoolExecutor(min(downloads, 8)) as pool:
            for start in range(0, len(todo), batch_size):
                batch = todo[start : start + batch_size]
                counts = pool.map(
                    lambda run: sample_reads(run, runs[run], sampled / f"{run}.fq", reads), batch
                )
                numbers = dict(zip(batch, counts, strict=True))
                for item in count_batch(numbers, sampled, index, gtf, tools, work):
                    writer.writerow(asdict(item))
                    done[item.run] = item
                handle.flush()
                for run in batch:
                    (sampled / f"{run}.fq").unlink(missing_ok=True)
                log(f"strandedness: {min(start + batch_size, len(todo))}/{len(todo)} runs")
    return {run: done[run] for run in runs}
