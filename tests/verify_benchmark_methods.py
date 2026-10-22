"""Offline checks for acquisition, nested sampling and comparison contracts."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.request import Request

from verify_benchmark_spec import rejects

from genesis_curation.benchmark.acquisition import acquire
from genesis_curation.benchmark.fixture import create
from genesis_curation.benchmark.methods import TOOLS, validate_methods
from genesis_curation.benchmark.reads import fastq, sample
from genesis_curation.benchmark.runner import run
from genesis_curation.benchmark.spec import load, sha256, write_json


class Response(io.BytesIO):
    def __init__(self, value: bytes, start: int, end: int, total: int) -> None:
        super().__init__(value)
        self.status = 206
        self.headers = {"Content-Range": f"bytes {start}-{end}/{total}"}

    def geturl(self) -> str:
        return "https://example.org/reads.gz"


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        payload = b"downloaded, checksummed source bytes\n"
        manifest = root / "source.json"
        source: dict[str, Any] = {
            "schema_version": 1,
            "limits": {"download_bytes": 1000, "seconds": 30},
            "assets": [
                {
                    "id": "reads.gz",
                    "url": "https://example.org/reads.gz",
                    "bytes": len(payload),
                    "checksum": {
                        "algorithm": "md5",
                        "value": hashlib.md5(payload).hexdigest(),
                    },
                    "source": {"accession": "synthetic"},
                }
            ],
        }
        write_json(manifest, source)

        def response(request: Request, **_kwargs: object) -> Response:
            value = request.headers["Range"].removeprefix("bytes=")
            start, end = map(int, value.split("-"))
            return Response(payload[start : end + 1], start, end, len(payload))

        with patch("urllib.request.urlopen", side_effect=response) as get:
            destination = acquire(manifest, root / "download", connections=2)
            locked = json.loads(destination.read_text())
            assert locked["assets"]["reads.gz"]["sha256"] == hashlib.sha256(payload).hexdigest()
            acquire(manifest, root / "download")
            assert get.call_count == 1, "Verified resume redownloaded an asset"
            state = root / "download/acquisition-state.json"
            assert json.loads(state.read_text())["downloaded_bytes"] == len(payload)
            # Simulate a short response with a correctly accounted contiguous prefix.
            (root / "download/reads.gz").unlink()
            (root / "download/reads.gz.partial").write_bytes(payload[:5])
            acquire(manifest, root / "download")
            assert json.loads(state.read_text())["downloaded_bytes"] == len(payload) * 2 - 5
        (root / "download/reads.gz").write_bytes(b"corrupt")
        rejects(lambda: acquire(manifest, root / "download"))
        for change in (
            "acquisition.json",
            "acquisition-state.json",
            "asset.partial",
            "../bad",
        ):
            invalid = copy.deepcopy(source)
            invalid["assets"][0]["id"] = change
            write_json(manifest, invalid)
            rejects(lambda: acquire(manifest, root / "bad"))
        write_json(manifest, source)
        with patch(
            "urllib.request.urlopen",
            side_effect=lambda *_a, **_k: Response(b"bad", 0, len(payload) - 1, len(payload)),
        ):
            rejects(lambda: acquire(manifest, root / "short"))
        assert not (root / "short/assets.lock.json").exists()

        paths = []
        for mate in (1, 2):
            path = root / f"R{mate}.fastq"
            path.write_text("".join(f"@template-{i}/{mate}\nACGT\n+\nIIII\n" for i in range(20)))
            paths.append(path)
        for reads, label in ((paths, "pe"), (paths[:1], "se")):
            record = json.loads(sample(reads, root / label, depths=[3, 7, 50], seed=14).read_text())
            assert record["input_templates"] == 20
            assert record["samples"]["50"]["templates"] == 20
            small = [n for n, _ in fastq(root / label / "depth-3.R1.fastq.gz")]
            large = [n for n, _ in fastq(root / label / "depth-7.R1.fastq.gz")]
            assert len(small) == 3 and len(large) == 7 and set(small) < set(large)
        other = sample(paths, root / "again", depths=[3], seed=14)
        assert json.loads(other.read_text())["samples"]["3"]["files"][0]["sha256"] == sha256(
            root / "pe/depth-3.R1.fastq.gz"
        )
        try:
            sample(paths, root / "pe", depths=[3], seed=14)
        except FileExistsError:
            pass
        else:
            raise AssertionError("Sampling overwrote existing outputs")

        checkout = root / "checkout"
        environment = checkout / "benchmarks/environment"
        environment.mkdir(parents=True)
        (environment / "pixi.lock").write_text("fixture lock")
        for env, names in (("default", TOOLS), ("macs2", {"macs2"})):
            for name in names:
                binary = environment / ".pixi/envs" / env / "bin" / name
                binary.parent.mkdir(parents=True, exist_ok=True)
                binary.write_text("#!/bin/sh\nexit 0\n")
                binary.chmod(0o700)
        experiment = create(
            root / "fixture",
            assay="bulk-ATAC",
            small=True,
            adapter="methods",
            checkout=checkout,
        )
        plan = load(experiment)
        assert plan["status"] == "READY" and len(plan["arms"]) == 8
        running = root / "running"
        running.mkdir()
        (running / ".running").write_text("another owner")
        with patch("genesis_curation.benchmark.runner.Runtime") as runtime:
            try:
                run(plan, running)
            except FileExistsError:
                pass
            else:
                raise AssertionError("A concurrent writer was admitted")
            runtime.assert_not_called()
        assert list(running.iterdir()) == [running / ".running"]
        spec = json.loads(experiment.read_text())
        for key, value in (
            ("duplicate_tool", "none"),
            ("adapter_r1", "INVALID"),
            ("caller", "unknown"),
        ):
            changed = copy.deepcopy(spec)
            changed["methods"][0][key] = value
            rejects(lambda changed=changed: validate_methods(changed, experiment.parent))
        changed = copy.deepcopy(spec)
        changed["parameters"]["mapq"] = [0, 30]
        rejects(lambda: validate_methods(changed, experiment.parent))
        changed = copy.deepcopy(spec)
        changed["methods"][0]["aligner"] = "bwa-mem2-2.2.1"
        rejects(lambda: validate_methods(changed, experiment.parent))
        pinned = root / "old-aligner"
        (pinned / "bin").mkdir(parents=True)
        binary = pinned / "bin/bwa-mem2"
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o700)
        (pinned / "conda-meta").mkdir()
        record = pinned / "conda-meta/bwa-mem2-fixture.json"
        write_json(record, {"version": "2.2.1"})
        lock = pinned / "pixi.lock"
        lock.write_text("frozen original aligner")
        changed["environment"]["bwa_mem2_221"] = {
            "prefix": str(pinned),
            "lock": str(lock),
            "lock_sha256": sha256(lock),
        }
        validate_methods(copy.deepcopy(changed), experiment.parent)
        write_json(record, {"version": "2.3"})
        rejects(lambda: validate_methods(copy.deepcopy(changed), experiment.parent))
    print(
        "PASS publisher verification/resume, short downloads, reserved paths, "
        "SE/PE nested sampling, method contracts"
    )


if __name__ == "__main__":
    main()
