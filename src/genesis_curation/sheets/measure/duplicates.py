"""Duplicate submissions: the same reads uploaded as two runs.

File checksums always differ (ENA writes the run accession into read names), so candidates
are runs sharing a read count that is not a round chunk size, confirmed by identical first
read sequences (the first 64 KB of read 1). The earliest accession of a group (by number) is kept.
"""

from __future__ import annotations

import json
import time
import zlib
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

FIRST_BYTES = 65_536
FIRST_READS = 50


def http_head(url: str) -> bytes:
    request = Request(
        url,
        headers={"User-Agent": "genesis-curation", "Range": f"bytes=0-{FIRST_BYTES - 1}"},
    )
    with urlopen(request, timeout=60) as response:
        return response.read(FIRST_BYTES)


def first_sequences(raw: bytes) -> list[str]:
    text = zlib.decompressobj(31).decompress(raw).decode(errors="replace")
    lines = text.split("\n")
    return [lines[i] for i in range(1, min(len(lines) - 1, 4 * FIRST_READS), 4)]


def accession_order(run: str) -> tuple[str, int]:
    """Archive prefix, then number: the earliest submission of a prefix sorts first."""
    digits = run.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    return run[: len(run) - len(digits)], int(digits or 0)


def candidate_groups(read_counts: dict[str, str]) -> list[list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for run, count in read_counts.items():
        if count.isdigit() and int(count) > 100_000 and int(count) % 100_000:
            groups[count].append(run)
    return [sorted(runs, key=accession_order) for runs in groups.values() if len(runs) > 1]


def find(
    read_counts: dict[str, str],
    read1: dict[str, str],
    cache: Path,
    fetch: Callable[[str], bytes] = http_head,
    workers: int = 8,
    attempts: int = 3,
) -> dict[str, str]:
    """run -> the run it duplicates, for runs whose first reads equal a lower accession's
    in a group of equal read counts. First reads are cached in `cache/first-reads.json`."""
    groups = candidate_groups(read_counts)
    store = cache / "first-reads.json"
    known: dict[str, list[str]] = json.loads(store.read_text()) if store.exists() else {}
    todo = [run for group in groups for run in group if run not in known and read1.get(run)]

    def head(run: str) -> list[str]:
        for attempt in range(attempts):
            try:
                return first_sequences(fetch(read1[run]))
            except OSError, ValueError, zlib.error:
                time.sleep(2.0 * (attempt + 1))
        return []

    if todo:
        with ThreadPoolExecutor(min(workers, 8)) as pool:
            for run, sequences in zip(todo, pool.map(head, todo), strict=True):
                if sequences:
                    known[run] = sequences
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps(known, sort_keys=True))
    duplicates: dict[str, str] = {}
    for group in groups:
        by_reads: dict[tuple[str, ...], list[str]] = defaultdict(list)
        for run in group:
            if known.get(run):
                by_reads[tuple(known[run])].append(run)
        for runs in by_reads.values():
            for run in runs[1:]:
                duplicates[run] = runs[0]
    return duplicates
