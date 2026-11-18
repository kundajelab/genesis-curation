"""Instruments that sequenced a run, from the original read names at a few spots.

An SRA run can interleave reads from two instruments whose index reads come out in opposite
orientations (MiSeq and NovaSeq in SRP699388). scatac-reads sets one barcode orientation
per library from its first 200,000 reads, so the reads of the other instrument lose their
barcodes. The probe reads only the NAME column of about a dozen spots with `vdb-dump`
(a few seconds per run, no download): the first spots, which set the orientation, and
spots spread over the rest of the run.

Instruments are compared by family, the letters of the instrument ID (M02100 -> M,
A00178 -> A, LH00123 -> LH): machines of one model read barcodes the same way. Runs whose
names were not kept (SRA Lite, `1`, `2`, ...) have no instrument and are not judged.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

ORIENTATION_READS = 200_000
SPREAD = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
INSTRUMENT = re.compile(r"^@?([A-Za-z]+)(\d+)[A-Za-z]?:\d+:[^:]+:\d+:")

Dump = Callable[[str, Sequence[int]], list[str]]


def rows(spots: int) -> list[int]:
    """Spots to read (1-based): the first ones, which set the orientation, then a spread."""
    head = [1, ORIENTATION_READS // 2, ORIENTATION_READS]
    spread = [round(spots * fraction) for fraction in SPREAD]
    return sorted({row for row in head + spread if 1 <= row <= spots})


def instrument(name: str) -> str:
    """The instrument ID of an Illumina read name ('M02100'), or '' when there is none."""
    match = INSTRUMENT.match(name.strip())
    return match.group(1) + match.group(2) if match else ""


def family(instrument_id: str) -> str:
    return re.sub(r"\d.*", "", instrument_id)


@dataclass(frozen=True)
class RunInstruments:
    run: str
    first: tuple[str, ...]  # instrument IDs of the spots that set the orientation
    rest: tuple[str, ...]  # instrument IDs of the spots after them
    error: str = ""

    @property
    def known(self) -> bool:
        return bool(self.first) and all(self.first + self.rest)

    def to_json(self) -> dict[str, object]:
        return {"run": self.run, "first": self.first, "rest": self.rest, "error": self.error}

    @classmethod
    def from_json(cls, value: dict[str, object]) -> RunInstruments:
        first, rest = value.get("first"), value.get("rest")
        return cls(
            run=str(value["run"]),
            first=tuple(str(x) for x in first) if isinstance(first, list) else (),
            rest=tuple(str(x) for x in rest) if isinstance(rest, list) else (),
            error=str(value.get("error") or ""),
        )


def summary(probe: RunInstruments) -> str:
    counts = Counter(probe.first + probe.rest)
    return ",".join(f"{name or '?'}x{n}" for name, n in sorted(counts.items()))


def library_verdict(probes: Sequence[RunInstruments]) -> str:
    """Why a library's runs (in sheet order) mix instruments, or ''. The first run's first
    spots set the orientation; any later spot from another instrument family breaks it."""
    known = [p for p in probes if p.known]
    if not known or len(known) != len(probes):
        return ""
    lead = {family(name) for name in known[0].first}
    later = [name for p in known for name in (p.rest if p is known[0] else p.first + p.rest)]
    others = sorted({name for name in later if family(name) not in lead})
    if len(lead) == 1 and not others:
        return ""
    first = ",".join(sorted(set(known[0].first)))
    return (
        f"mixed instruments: first reads from {first}, later reads from "
        f"{','.join(others) or first}; the workflow sets one barcode orientation per library "
        f"from its first {ORIENTATION_READS:,} reads"
    )


def vdb_dump(executable: str) -> Dump:
    def dump(run: str, wanted: Sequence[int]) -> list[str]:
        result = subprocess.run(
            [executable, run, "-R", ",".join(map(str, wanted)), "-C", "NAME", "-f", "tab"],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if result.returncode:
            lines = result.stderr.strip().splitlines() or [f"exit {result.returncode}"]
            raise OSError(lines[-1][-300:])
        return result.stdout.splitlines()

    return dump


def find_vdb_dump(path: str | None = None) -> str:
    found = path or shutil.which("vdb-dump")
    if not found:
        raise ValueError("The instrument probe needs vdb-dump (sra-tools); give --vdb-dump")
    return found


def probe(run: str, spots: int, dump: Dump) -> RunInstruments:
    wanted = rows(spots)
    try:
        names = dump(run, wanted)
    except (OSError, subprocess.TimeoutExpired) as error:
        return RunInstruments(run, (), (), error=str(error) or type(error).__name__)
    ids = [instrument(name) for name in names]
    if len(ids) != len(wanted):
        return RunInstruments(run, (), (), error=f"{len(ids)} names for {len(wanted)} spots")
    first = tuple(i for row, i in zip(wanted, ids, strict=True) if row <= ORIENTATION_READS)
    rest = tuple(i for row, i in zip(wanted, ids, strict=True) if row > ORIENTATION_READS)
    return RunInstruments(run, first, rest)


def measure(
    runs: dict[str, int],
    cache: Path,
    dump: Dump,
    workers: int = 8,
    log: Callable[[str], None] = print,
) -> dict[str, RunInstruments]:
    """Probe each run (accession -> spots), cached per run; failed probes are not cached,
    so the next build asks again."""
    cache.mkdir(parents=True, exist_ok=True)
    found: dict[str, RunInstruments] = {}
    todo = []
    for run in sorted(runs):
        path = cache / f"{run}.json"
        if path.exists():
            found[run] = RunInstruments.from_json(json.loads(path.read_text()))
        else:
            todo.append(run)
    if todo:
        log(f"instrument probe: {len(todo)} runs ({len(found)} cached)")
    with ThreadPoolExecutor(min(workers, 8)) as pool:
        for result in pool.map(lambda run: probe(run, runs[run], dump), todo):
            found[result.run] = result
            if not result.error:
                (cache / f"{result.run}.json").write_text(json.dumps(result.to_json()) + "\n")
    return found
