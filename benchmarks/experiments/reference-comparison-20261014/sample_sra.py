"""Validate full-quality local SRA archives and stream nested paired FASTQ samples."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from genesis_curation.benchmark import reads
from genesis_curation.benchmark.reads import fastq_stream, sample_records
from genesis_curation.benchmark.spec import sha256, write_json


def stream_pairs(
    command: list[str], log: Path, seconds: int, expected_bases: int
) -> Iterator[tuple[bytes, list[bytes]]]:
    """Reject singleton/multi-read spots, truncated streams and incomplete decodes."""
    bases = 0
    with (
        log.open("xb") as stderr,
        subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=stderr, start_new_session=True
        ) as process,
    ):
        if process.stdout is None:
            raise RuntimeError("Decoder has no output pipe")

        def stop() -> None:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        timer = threading.Timer(seconds, stop)
        timer.start()
        try:
            records = iter(fastq_stream(process.stdout))
            for name, first in records:
                second = next(records, None)
                if second is None or second[0] != name:
                    raise ValueError(
                        "SRA spots do not contain exactly two matched biological reads"
                    )
                if not first.splitlines()[0].endswith(b"/1") or not second[1].splitlines()[
                    0
                ].endswith(b"/2"):
                    raise ValueError("Unexpected biological mate order; review the SRA layout")
                mates = [first, second[1]]
                bases += sum(len(record.splitlines()[1]) for record in mates)
                yield name, mates
            if process.wait() != 0:
                raise ValueError("SRA decoding failed or exceeded its time budget; see decoder log")
            if bases != expected_bases:
                raise ValueError("Decoded biological base count disagrees with source metadata")
        finally:
            timer.cancel()
            stop()
            process.wait()


def sample_sra(
    source: Path, state_path: Path, tools: Path, out: Path, templates: int, bases: int
) -> Path:
    source = source.resolve(strict=True)
    state = json.loads(state_path.read_text())["assets"].get(source.name)
    if source.suffix != ".sra" or not state or not state["verified"]:
        raise ValueError("A verified full-quality .sra archive is required")
    expected = state["sha256"]
    if sha256(source) != expected:
        raise ValueError("SRA source changed since acquisition")
    receipt = out / "sampling.json"
    sources = {str(source): expected}
    if receipt.exists():
        value = json.loads(receipt.read_text())
        if (
            value["sources"] != sources
            or value["input_templates"] != templates
            or value["seed"] != 60141
            or set(value["samples"]) != {"1000000", "5000000"}
        ):
            raise ValueError("Existing sample belongs to a different source or selection")
        derivation = out / "derivation.json"
        if not derivation.is_file():
            raise ValueError("Sampling receipt lacks its completed decoding derivation")
        derived = json.loads(derivation.read_text())
        if (
            derived.get("status") != "COMPLETE"
            or derived.get("source_sha256") != expected
            or derived.get("expected_templates") != templates
            or derived.get("expected_bases") != bases
            or derived.get("full_decode_passes") != 2
            or derived.get("sampling_sha256") != sha256(receipt)
            or derived.get("validation_log_sha256") != sha256(out / "vdb-validate.log")
        ):
            raise ValueError("Existing decoding evidence differs from the requested source/counts")
        for depth, subset in value["samples"].items():
            if subset["templates"] != min(int(depth), templates) or len(subset["files"]) != 2:
                raise ValueError("Existing sample depth or mate count differs")
            for asset in subset["files"]:
                if sha256(Path(asset["path"])) != asset["sha256"]:
                    raise ValueError("Sampled FASTQ changed")
        return receipt
    out.mkdir(parents=True, exist_ok=False)
    fastq_dump = tools / "bin/fastq-dump"
    validate = tools / "bin/vdb-validate"
    identities = {
        str(path): sha256(path)
        for path in (fastq_dump, validate, Path(__file__), Path(reads.__file__))
    }
    command = [
        str(fastq_dump),
        "--stdout",
        "--split-spot",
        "--skip-technical",
        "--minReadLen",
        "1",
        "--defline-seq",
        "@$ac.$si/$ri",
        "--defline-qual",
        "+",
        str(source),
    ]
    started = time.monotonic()
    try:
        with (out / "vdb-validate.log").open("xb") as log:
            command_validate = [str(validate), str(source)]
            with subprocess.Popen(
                command_validate, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
            ) as process:
                try:
                    code = process.wait(timeout=21600)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise
                if code:
                    raise subprocess.CalledProcessError(code, command_validate)
        passes = 0

        def records() -> Iterator[tuple[bytes, list[bytes]]]:
            nonlocal passes
            passes += 1
            print(source.name, "full decode pass", passes, flush=True)
            yield from stream_pairs(command, out / f"decode-{passes}.log", 21600, bases)

        def verify() -> None:
            if sha256(source) != expected or any(
                sha256(Path(path)) != digest for path, digest in identities.items()
            ):
                raise ValueError("SRA source or decoding implementation changed")

        receipt = sample_records(
            records,
            out,
            mate_count=2,
            depths=[1_000_000, 5_000_000],
            seed=60141,
            sources=sources,
            verify=verify,
            expected_templates=templates,
            seconds=43200,
        )
        write_json(
            out / "derivation.json",
            {
                "schema_version": 1,
                "status": "COMPLETE",
                "source_sha256": expected,
                "source_format": "SRA Normalized, full base qualities; not SRA Lite",
                "source_accession": source.stem,
                "expected_templates": templates,
                "expected_bases": bases,
                "decoder_command": command,
                "identities": identities,
                "python_version": sys.version,
                "full_decode_passes": passes,
                "validation_log_sha256": sha256(out / "vdb-validate.log"),
                "sampling_sha256": sha256(receipt),
                "seconds": time.monotonic() - started,
                "scope": (
                    "All biological reads; no clipping or quality filtering. "
                    "FASTQ headers normalized to accession.spot/mate; derived FASTQs "
                    "are not the original ENA compressed bytes."
                ),
            },
        )
        return receipt
    except Exception as error:
        write_json(
            out / "failure.json",
            {
                "status": "FAILED",
                "error": str(error),
                "seconds": time.monotonic() - started,
            },
        )
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--tools", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--templates", type=int, required=True)
    parser.add_argument("--bases", type=int, required=True)
    args = parser.parse_args()
    print(
        sample_sra(
            args.source,
            args.state,
            args.tools.resolve(),
            args.out.resolve(),
            args.templates,
            args.bases,
        )
    )
