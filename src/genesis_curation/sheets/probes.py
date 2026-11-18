"""Cheap probes of read files: fetch the first bytes of a gzipped FASTQ and look at its reads.

`probe` fetches the first 200 KB of a file (an HTTP range request) and records the
lengths of its first 100 reads. Workflows that infer read length from those reads (DAP-seq's
METADATA step: "the first 100 reads must have identical lengths") reject files whose reads
were trimmed before upload; `LengthProbe.uniform` says whether a file passes. Results are
cached per URL in one JSON file; failed fetches are not cached, so the next build retries them.
"""

from __future__ import annotations

import json
import threading
import time
import zlib
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.request import Request, urlopen

PROBE_BYTES = 200_000
PROBE_READS = 100

Fetch = Callable[[str, int], bytes]


@dataclass(frozen=True)
class LengthProbe:
    url: str
    reads: int = 0  # reads read (at most PROBE_READS)
    distinct: int = 0  # distinct lengths among them
    shortest: int = 0
    longest: int = 0
    error: str = ""  # why the file could not be read, or ''

    @property
    def uniform(self) -> bool:
        return not self.error and self.reads > 0 and self.distinct == 1

    @property
    def problem(self) -> str:
        """'' when the reads have one length; otherwise a reason a sheet can record."""
        if self.error:
            return f"read-length probe failed ({self.error})"
        if self.reads == 0:
            return "read-length probe found no reads"
        if self.distinct > 1:
            return (
                f"reads trimmed before upload (first {self.reads} reads have "
                f"{self.distinct} lengths, {self.shortest}-{self.longest})"
            )
        return ""


def http_range(url: str, size: int) -> bytes:
    request = Request(
        url, headers={"User-Agent": "genesis-curation", "Range": f"bytes=0-{size - 1}"}
    )
    with urlopen(request, timeout=120) as response:
        return response.read(size)


def read_lengths(raw: bytes, reads: int = PROBE_READS) -> list[int]:
    """Lengths of the first complete reads of a (possibly truncated) gzipped FASTQ."""
    text = zlib.decompressobj(31).decompress(raw).decode(errors="replace")
    lines = text.split("\n")
    complete = (len(lines) - 1) // 4  # the last line may be cut short
    return [len(lines[4 * i + 1].rstrip("\r")) for i in range(min(reads, complete))]


def probe(url: str, fetch: Fetch = http_range) -> LengthProbe:
    try:
        found = read_lengths(fetch(url, PROBE_BYTES))
    except (OSError, ValueError, zlib.error) as error:
        return LengthProbe(url, error=type(error).__name__ + (f": {error}" if str(error) else ""))
    if not found:
        return LengthProbe(url)
    return LengthProbe(url, len(found), len(set(found)), min(found), max(found))


class LengthCache:
    """Read-length probes of many URLs, at most `workers` (8) at once, cached on disk."""

    def __init__(
        self,
        path: Path,
        fetch: Fetch = http_range,
        workers: int = 8,
        attempts: int = 4,
        offline: bool = False,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.path = path
        self.fetch = fetch
        self.workers = min(workers, 8)
        self.attempts = attempts
        self.offline = offline
        self.sleep = sleep
        self.lock = threading.Lock()
        self.known: dict[str, LengthProbe] = {}
        if path.exists():
            for url, value in json.loads(path.read_text()).items():
                self.known[url] = LengthProbe(**value)

    def one(self, url: str) -> LengthProbe:
        result = LengthProbe(url, error="not probed")
        for attempt in range(self.attempts):
            result = probe(url, self.fetch)
            if not result.error:
                break
            if attempt < self.attempts - 1:
                self.sleep(5.0 * 2**attempt)
        return result

    def lengths(self, urls: Iterable[str]) -> dict[str, LengthProbe]:
        wanted = sorted({u for u in urls if u and u != "-"})
        todo = [u for u in wanted if u not in self.known]
        if todo and not self.offline:
            with ThreadPoolExecutor(self.workers) as pool:
                for url, result in zip(todo, pool.map(self.one, todo), strict=True):
                    self.known[url] = result
            self.save()
        return {
            u: self.known.get(u, LengthProbe(u, error="not cached and offline")) for u in wanted
        }

    def save(self) -> None:
        kept = {u: asdict(p) for u, p in sorted(self.known.items()) if not p.error}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        part = self.path.with_suffix(".part")
        part.write_text(json.dumps(kept, indent=0, sort_keys=True))
        part.replace(self.path)
