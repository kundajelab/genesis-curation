"""FASTQ integrity and deterministic nested, paired template subsampling."""

from __future__ import annotations

import gzip
import hashlib
import heapq
import itertools
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Protocol


class LineReader(Protocol):
    def readline(self, size: int = -1, /) -> bytes: ...


def fastq(path: Path) -> Iterator[tuple[bytes, bytes]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as stream:
        yield from fastq_stream(stream)


def fastq_stream(stream: LineReader) -> Iterator[tuple[bytes, bytes]]:
    """Validate complete records from a binary FASTQ stream."""
    while header := stream.readline():
        sequence, plus, quality = stream.readline(), stream.readline(), stream.readline()
        if (
            not header.startswith(b"@")
            or not header[1:].split()
            or not plus.startswith(b"+")
            or not quality.endswith(b"\n")
            or not sequence.strip()
            or len(sequence.rstrip(b"\r\n")) != len(quality.rstrip(b"\r\n"))
        ):
            raise ValueError("Corrupt or truncated FASTQ record")
        name = header[1:].split()[0]
        if name.endswith((b"/1", b"/2")):
            name = name[:-2]
        if not name:
            raise ValueError("FASTQ template name is empty")
        yield name, header + sequence + plus + quality


def paired(read1: Path, read2: Path) -> Iterator[tuple[bytes, bytes, bytes]]:
    found = False
    for first, second in itertools.zip_longest(fastq(read1), fastq(read2)):
        if first is None or second is None or first[0] != second[0]:
            raise ValueError("FASTQ mates differ in record count or template names")
        found = True
        yield first[0], first[1], second[1]
    if not found:
        raise ValueError("Empty FASTQ library")


def subset(
    read1: Path, read2: Path, out1: Path, out2: Path, *, templates: int, seed: int, expected: int
) -> dict[str, int | str]:
    """Select the lowest seeded hashes of (record index, template name), preserving order.

    Index disambiguates repeated source names. Smaller caps are nested subsets for
    fixed input bytes/seed; iteration order is part of the checksummed input contract.
    """
    if out1.resolve() == out2.resolve() or out1.exists() or out2.exists():
        raise ValueError("Subset outputs must be distinct new files")
    if templates <= 0:
        raise ValueError("Template cap must be positive")
    if read1.resolve() in {out1.resolve(), out2.resolve()} or read2.resolve() in {
        out1.resolve(),
        out2.resolve(),
    }:
        raise ValueError("Subsampling may not overwrite original FASTQs")
    heap: list[tuple[int, int]] = []
    count = 0
    for index, (name, _, _) in enumerate(paired(read1, read2)):
        score = int.from_bytes(hashlib.sha256(f"{seed}:{index}:".encode() + name).digest(), "big")
        item = (-score, -index)
        if len(heap) < templates:
            heapq.heappush(heap, item)
        elif item > heap[0]:
            heapq.heapreplace(heap, item)
        count += 1
    if count != expected:
        raise ValueError("Declared raw template count disagrees with FASTQs")
    selected = {-index for _, index in heap}
    with out1.open("xb") as first_raw, out2.open("xb") as second_raw:
        with (
            gzip.GzipFile(fileobj=first_raw, mode="wb", filename="", mtime=0) as first,
            gzip.GzipFile(fileobj=second_raw, mode="wb", filename="", mtime=0) as second,
        ):
            for index, (_, r1, r2) in enumerate(paired(read1, read2)):
                if index in selected:
                    first.write(r1)
                    second.write(r2)
    return {
        "input_templates": count,
        "selected_templates": len(selected),
        "seed": seed,
        "method": (
            "lowest SHA256(seed, record index, canonical template name); "
            "original record order retained"
        ),
    }


def sample(
    reads: list[Path], out: Path, *, depths: list[int], seed: int, seconds: int = 172800
) -> Path:
    """Count/validate a library and create nested seeded SE or PE depth samples."""
    from .spec import sha256

    if len(reads) not in {1, 2} or not depths or any(type(n) is not int or n <= 0 for n in depths):
        raise ValueError("Supply one or two FASTQs and positive depths")
    if seconds <= 0 or type(seed) is not int:
        raise ValueError("Finite positive time budget and integer seed required")
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    sources = {str(p.resolve()): sha256(p) for p in reads}

    def records() -> Iterator[tuple[bytes, list[bytes]]]:
        if len(reads) == 2:
            for name, r1, r2 in paired(*reads):
                yield name, [r1, r2]
        else:
            for name, record in fastq(reads[0]):
                yield name, [record]

    def verify() -> None:
        if sources != {str(p.resolve()): sha256(p) for p in reads}:
            raise ValueError("Input changed during sampling")

    return sample_records(
        records,
        out,
        mate_count=len(reads),
        depths=depths,
        seed=seed,
        sources=sources,
        verify=verify,
        seconds=seconds,
        start=start,
    )


def sample_records(
    records: Callable[[], Iterator[tuple[bytes, list[bytes]]]],
    out: Path,
    *,
    mate_count: int,
    depths: list[int],
    seed: int,
    sources: dict[str, str],
    verify: Callable[[], None],
    seconds: int = 172800,
    start: float | None = None,
    expected_templates: int | None = None,
) -> Path:
    """Sample two reproducible full streams into an existing empty directory.

    Callers supply validated records and an immutable-source check. A complete
    second pass must reproduce the first pass's decoded bytes and template count.
    This permits SRA decoding without materializing a full uncompressed library.
    """
    from .spec import sha256, write_json

    if mate_count not in {1, 2} or not depths or any(type(n) is not int or n <= 0 for n in depths):
        raise ValueError("Supply one or two mates and positive depths")
    if (
        seconds <= 0
        or type(seed) is not int
        or (
            expected_templates is not None
            and (type(expected_templates) is not int or expected_templates <= 0)
        )
    ):
        raise ValueError("Invalid sampling budget, seed or expected template count")
    if not out.is_dir() or (out / "sampling.json").exists():
        raise ValueError("Choose a fresh sampling directory")
    start = time.monotonic() if start is None else start
    depths = sorted(set(depths))
    heap: list[tuple[int, int]] = []

    def check(index: int) -> None:
        if index % 10000 == 0 and time.monotonic() - start > seconds:
            raise ValueError("Sampling time budget exceeded; partial outputs retained")

    count = 0
    decoded = hashlib.sha256()
    for index, (name, mates) in enumerate(records()):
        check(index)
        if len(mates) != mate_count:
            raise ValueError("Unexpected number of biological reads in a template")
        for record in mates:
            decoded.update(record)
        score = int.from_bytes(hashlib.sha256(f"{seed}:{index}:".encode() + name).digest(), "big")
        item = (-score, -index)
        if len(heap) < depths[-1]:
            heapq.heappush(heap, item)
        elif item > heap[0]:
            heapq.heapreplace(heap, item)
        count += 1
    if count == 0:
        raise ValueError("Empty FASTQ library")
    if expected_templates is not None and count != expected_templates:
        raise ValueError("Declared template count disagrees with decoded records")
    ranked = sorted(heap, reverse=True)
    del heap
    # Rank lookup is shared by all nested samples; do not store complete FASTQs.
    rank = {-index: position for position, (_, index) in enumerate(ranked)}
    del ranked
    outputs: dict[int, list[Path]] = {
        n: [out / f"depth-{n}.R{mate + 1}.fastq.gz" for mate in range(mate_count)] for n in depths
    }
    with ExitStack() as stack:
        writers = {
            n: [
                stack.enter_context(
                    gzip.GzipFile(
                        fileobj=stack.enter_context(p.open("xb")),
                        mode="wb",
                        filename="",
                        mtime=0,
                        compresslevel=1,
                    )
                )
                for p in paths
            ]
            for n, paths in outputs.items()
        }
        replayed, replay_count = hashlib.sha256(), 0
        for index, (_, mates) in enumerate(records()):
            check(index)
            if len(mates) != mate_count:
                raise ValueError("Unexpected number of biological reads in a template")
            for record in mates:
                replayed.update(record)
            replay_count += 1
            position = rank.get(index)
            if position is not None:
                for n in depths:
                    if position < n:
                        for writer, record in zip(writers[n], mates, strict=True):
                            writer.write(record)
    if replay_count != count or replayed.digest() != decoded.digest():
        raise ValueError("Decoded stream changed between sampling passes")
    verify()
    result = out / "sampling.json"
    write_json(
        result,
        {
            "schema_version": 1,
            "sources": sources,
            "input_templates": count,
            "decoded_fastq_sha256": decoded.hexdigest(),
            "seed": seed,
            "gzip_level": 1,
            "method": (
                "lowest SHA256(seed, record index, canonical template name); "
                "original order; nested depths"
            ),
            "seconds": time.monotonic() - start,
            "samples": {
                str(n): {
                    "templates": min(n, count),
                    "files": [
                        {"path": str(p), "sha256": sha256(p), "bytes": p.stat().st_size}
                        for p in paths
                    ],
                }
                for n, paths in outputs.items()
            },
        },
    )
    return result
