"""Content verification and atomic output publication for fragment-first processing."""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import os
import re
import resource
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO

import pysam

from ..contracts.records import dump, fingerprint, load


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_asset(value: dict[str, Any], base: Path) -> Path:
    if set(value) != {"path", "sha256"} or not re.fullmatch("[a-f0-9]{64}", value["sha256"]):
        raise ValueError("Assets require exactly path and SHA256")
    path = (base / value["path"]).resolve()
    if not path.is_file() or digest(path) != value["sha256"]:
        raise ValueError(f"Missing or changed asset: {path.name}")
    return path


def text_file(path: Path) -> TextIO:
    with path.open("rb") as stream:
        compressed = stream.read(2) == b"\x1f\x8b"
    return gzip.open(path, "rt", encoding="utf-8") if compressed else path.open(encoding="utf-8")


def bounded_lines(stream: TextIO, limit: int = 1024 * 1024) -> Iterator[str]:
    while line := stream.readline(limit + 1):
        if len(line) > limit:
            raise ValueError("Input line exceeds the 1 MiB record limit")
        yield line


def reference(value: dict[str, Any], base: Path) -> tuple[Path, dict[str, int]]:
    if not all(
        isinstance(value.get(k), str) and value[k].strip()
        for k in ("reference_id", "assembly", "species")
    ):
        raise ValueError("Explicit reference ID, assembly and species are required")
    fasta = checked_asset(value["fasta"], base)
    observed: dict[str, int] = {}
    current = None
    with text_file(fasta) as stream:
        line_start = True
        while chunk := stream.readline(1024 * 1024):
            if line_start and chunk.startswith(">"):
                if not chunk.endswith("\n") and len(chunk) == 1024 * 1024:
                    raise ValueError("Oversized FASTA header")
                header = chunk[1:].split()
                if not header:
                    raise ValueError("Empty FASTA contig name")
                current = header[0]
                if current in observed:
                    raise ValueError("Duplicate FASTA contig")
                observed[current] = 0
            else:
                sequence = "".join(chunk.split())
                if sequence:
                    if current is None:
                        raise ValueError("Sequence before FASTA header")
                    if set(sequence.upper()) - set("ACGTRYSWKMBDHVN"):
                        raise ValueError("Non-IUPAC genomic FASTA sequence")
                    observed[current] += len(sequence)
            line_start = chunk.endswith("\n")
    if not observed or any(n < 1 for n in observed.values()) or value["contigs"] != observed:
        raise ValueError("FASTA and declared chromosome dictionary differ")
    mito, plastid = value["mitochondrial_contigs"], value["plastid_contigs"]
    if len(mito) != len(set(mito)) or len(plastid) != len(set(plastid)):
        raise ValueError("Repeated organellar contig")
    if set(mito) & set(plastid) or (set(mito) | set(plastid)) - observed.keys():
        raise ValueError("Invalid or overlapping organellar contig declarations")
    return fasta, observed


def software() -> dict[str, Any]:
    code = Path(__file__).parent
    return {
        "python": sys.version.split()[0],
        "pysam": pysam.__version__,
        "implementation": {
            str(p.relative_to(code)): digest(p)
            for p in sorted(code.rglob("*"))
            if p.is_file() and p.suffix in {".py", ".json"}
        },
    }


def verify_output(directory: Path, kind: str) -> dict[str, Any]:
    value = load(directory / "complete.json")
    if value.get("kind") != kind or value.get("schema_version") != 1:
        raise ValueError("Wrong output type or version")
    signed = {k: v for k, v in value.items() if k != "version"}
    if value.get("version") != fingerprint(signed):
        raise ValueError("Output manifest content digest changed")
    for name, expected in value["outputs"].items():
        path = directory / name
        if not path.resolve().is_relative_to(directory.resolve()) or digest(path) != expected:
            raise ValueError("Output checksum mismatch: " + name)
    return value


@contextlib.contextmanager
def publication(output: Path) -> Iterator[Path]:
    if output.exists():
        raise ValueError("Output exists; use resume or a new output directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="." + output.name + "-", dir=output.parent))
    try:
        yield staging
        if not (staging / "complete.json").is_file():
            raise ValueError("Missing completion manifest")
        # Linux rename must not replace another completed/nonempty run.
        if output.exists():
            raise ValueError("Another run published this output")
        staging.rename(output)
    except BaseException as error:
        dump(staging / "failure.json", {"error": str(error), "type": type(error).__name__})
        raise


def complete(directory: Path, kind: str, data: dict[str, Any], start: float) -> dict[str, Any]:
    paths = [p for p in sorted(directory.rglob("*")) if p.is_file()]

    def diagnostic(path: Path) -> bool:
        return path.name == "execution.json" or path.name.endswith(
            (".stdout", ".stderr", ".command.json", ".xls")
        )

    outputs = {str(p.relative_to(directory)): digest(p) for p in paths if not diagnostic(p)}
    diagnostics = {str(p.relative_to(directory)): digest(p) for p in paths if diagnostic(p)}
    value = {"schema_version": 1, "kind": kind, "data": data, "outputs": outputs}
    value["version"] = fingerprint(value)
    dump(directory / "complete.json", value, immutable=True)
    dump(
        directory / "execution.json",
        {
            "elapsed_seconds": time.monotonic() - start,
            "diagnostics_sha256": diagnostics,
            "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "pid": os.getpid(),
            "git_sha": subprocess.run(
                ["git", "-C", str(Path(__file__).parent), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            or None,
            "argv": sys.argv,
            "software": software(),
        },
    )
    return value
