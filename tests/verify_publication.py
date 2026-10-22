"""Public evidence omits review tokens and retains explicit byte provenance."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import patch

from genesis_curation import publication as public

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    value = hashlib.sha256(b"synthetic review concurrency state").hexdigest()
    fields = ["token", "input_token", "expected_token"]
    for field in fields:
        data = json.dumps({field: value, "manifest_sha256": value}).encode()
        result = json.loads(public.sanitize(data))
        assert result[field] == "REDACTED"
        assert result["manifest_sha256"] == value
        escaped = json.dumps(data.decode()).encode()
        assert value not in json.loads(json.loads(public.sanitize(escaped)))[field]
    for command in (
        f"genesis review approve --token {value}",
        f"--token='{value}'",
        repr(["genesis", "review", "approve", "--token", value]),
        json.dumps({"argv": ["--token", value]}),
        json.dumps({"review_tokens": {"dataset-a": value}}),
    ):
        result = public.sanitize(command.encode())
        assert value.encode() not in result
        assert public.sanitize(result) == result
    credential = "synthetic" + "-credential-for-publication-test"
    for data in (
        json.dumps({"password": credential + ' with "quoted" spaces'}),
        f"Authorization: Bearer {credential}",
        json.dumps({"Authorization": "Bearer " + credential}),
        f"api_key={credential}",
    ):
        sanitized = public.sanitize(data.encode())
        assert credential.encode() not in sanitized
        assert public.sanitize(sanitized) == sanitized
        if data.startswith("{"):
            json.loads(sanitized)
    scientific = f"chr1\t0\t100\t{value}\t0.123\r\n".encode()
    assert public.sanitize(scientific) == scientific
    empty_credential = json.dumps({"api_key": None, "secret": False}).encode()
    assert public.sanitize(empty_credential) == empty_credential
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        source = base / "private"
        source.mkdir()
        log = json.dumps({"token": value}).encode()
        (source / "failure.log").write_bytes(log)
        (source / "metrics.tsv").write_bytes(scientific)
        packed = source / "logs.tar.gz"
        with tarfile.open(packed, "w:gz") as archive:
            for name, data in [("logs/status.stdout", log), ("metrics.tsv", scientific)]:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        first, second = base / "first", base / "second"
        manifest = public.export(source, first)
        assert public.export(source, second) == manifest
        assert (first / "logs.tar.gz").read_bytes() == (second / "logs.tar.gz").read_bytes()
        assert (source / "failure.log").read_bytes() == log
        assert not public.check(first)
        (first / "metrics.tsv").write_bytes(b"changed measurement")
        assert any("publication-manifest.json" in item for item in public.check(first))
        (first / "metrics.tsv").write_bytes(scientific)
        assert public.check(source)
        assert all(value not in finding for finding in public.check(source))
        members = public.archive_members(first / "logs.tar.gz")
        for name, row in manifest["files"].items():
            assert public.digest((source / name).read_bytes()) == row["original_sha256"]
            assert public.digest((first / name).read_bytes()) == row["sha256"]
        assert members["metrics.tsv"] == scientific
        with patch.object(public, "MAX_MEMBER", 1):
            try:
                public.archive_members(packed)
            except ValueError:
                pass
            else:
                raise AssertionError("Archive member limit was ignored")
        for name in ("../outside", "/absolute", "duplicate", "symlink"):
            with tarfile.open(packed, "w:gz") as archive:
                for _ in range(2 if name == "duplicate" else 1):
                    info = tarfile.TarInfo(name)
                    if name == "symlink":
                        info.type = tarfile.SYMTYPE
                        info.linkname = "outside"
                    archive.addfile(info, io.BytesIO(b""))
            failed = base / "failed"
            try:
                public.export(source, failed)
            except ValueError:
                assert not failed.exists()
            else:
                raise AssertionError("Unsafe archive was published")
    print("PASS sanitized export, archive bounds and original/public hashes")


if __name__ == "__main__":
    main()
