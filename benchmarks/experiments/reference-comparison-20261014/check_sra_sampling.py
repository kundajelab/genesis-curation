"""Offline checks for paired decoding, stream replay and unchanged sampling bytes."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

from genesis_curation.benchmark.reads import fastq_stream, paired, sample, sample_records
from genesis_curation.benchmark.spec import sha256
from sample_sra import sample_sra, stream_pairs


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        # sample_sra keys receipts by resolved source paths; resolve the fixture root to match
        # (macOS temporary folders are reached through the /var -> /private/var symlink).
        root = Path(directory).resolve()
        paths = [root / f"R{i}.fq" for i in (1, 2)]
        for i, path in enumerate(paths, 1):
            path.write_bytes(b"".join(f"@run.{j}/{i}\nACGT\n+\nIIII\n".encode() for j in range(20)))
        regular = sample(paths, root / "regular", depths=[3, 7, 50], seed=14)

        def records() -> Iterator[tuple[bytes, list[bytes]]]:
            for name, first, second in paired(*paths):
                yield name, [first, second]

        out = root / "stream"
        out.mkdir()
        streamed = sample_records(
            records,
            out,
            mate_count=2,
            depths=[3, 7, 50],
            seed=14,
            sources={},
            verify=lambda: None,
            expected_templates=20,
        )
        a, b = json.loads(regular.read_text()), json.loads(streamed.read_text())
        assert a["decoded_fastq_sha256"] == b["decoded_fastq_sha256"]
        for n in (3, 7, 50):
            for mate in (1, 2):
                filename = f"depth-{n}.R{mate}.fastq.gz"
                assert sha256(regular.parent / filename) == sha256(out / filename)
        for variant in ("truncated", "changed"):
            out = root / variant
            out.mkdir()
            passes = 0

            def changed(variant: str = variant) -> Iterator[tuple[bytes, list[bytes]]]:
                nonlocal passes
                passes += 1
                for index, (name, mates) in enumerate(records()):
                    if passes == 2 and index == 19:
                        if variant == "truncated":
                            return
                        mates = [mate.replace(b"ACGT", b"TCGT") for mate in mates]
                    yield name, mates

            try:
                sample_records(
                    changed,
                    out,
                    mate_count=2,
                    depths=[3],
                    seed=14,
                    sources={},
                    verify=lambda: None,
                )
            except ValueError as error:
                assert "changed between" in str(error)
            else:
                raise AssertionError("Changed replay accepted")
            assert not (out / "sampling.json").exists()
        payload = b"".join(first + second for _, first, second in paired(*paths))
        stream = root / "stream.fq"
        stream.write_bytes(payload)
        command = [
            sys.executable,
            "-c",
            "import pathlib,sys; sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())",
            str(stream),
        ]
        assert len(list(stream_pairs(command, root / "valid.log", 10, 160))) == 20
        stream.write_bytes(payload[:-1])
        try:
            list(stream_pairs(command, root / "bad.log", 10, 160))
        except ValueError:
            pass
        else:
            raise AssertionError("Truncated decoder output accepted")
        for header in (b"@", b"@/1"):
            try:
                list(fastq_stream(io.BytesIO(header + b"\nACGT\n+\nIIII\n")))
            except ValueError:
                pass
            else:
                raise AssertionError("Empty template identifier accepted")
        fake_sra = root / "fake.sra"
        fake_sra.write_bytes(b"fixture")
        acquisition = root / "sources.json"
        acquisition.write_text(
            json.dumps({"assets": {fake_sra.name: {"verified": True, "sha256": sha256(fake_sra)}}})
        )
        existing = root / "missing-derivation"
        existing.mkdir()
        (existing / "sampling.json").write_text(
            json.dumps(
                {
                    "sources": {str(fake_sra): sha256(fake_sra)},
                    "input_templates": 1,
                    "seed": 60141,
                    "samples": {"1000000": {}, "5000000": {}},
                }
            )
        )
        try:
            sample_sra(fake_sra, acquisition, root / "no-tools", existing, 1, 8)
        except ValueError as error:
            assert "decoding derivation" in str(error)
        else:
            raise AssertionError("Incomplete decoding evidence accepted")
        # The child inherits stdout. Killing only its launcher would leave the
        # decoder reader blocked until this child exits.
        hanging = [
            sys.executable,
            "-c",
            "import subprocess,sys; subprocess.run([sys.executable, '-c', "
            "'import time; time.sleep(10)'])",
        ]
        started = time.monotonic()
        try:
            list(stream_pairs(hanging, root / "timeout.log", 1, 0))
        except ValueError as error:
            assert "time budget" in str(error)
        else:
            raise AssertionError("Timed-out decoder accepted")
        assert time.monotonic() - started < 5, "Decoder child outlived its process-group timeout"
    print("PASS paired streams, byte-identical nested sampling, changed/truncated replay rejection")


if __name__ == "__main__":
    main()
