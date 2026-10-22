"""Publish verified evidence with local paths and sensitive log fields sanitized.

Original archive hashes and published member hashes are distinct. Input data,
measurements and biological identifiers are not edited by this export.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

from genesis_curation.publication import sanitize


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def export(source: Path, out: Path, replacements: dict[str, str]) -> None:
    if not replacements or any(
        not k.startswith("/") or not v.startswith("<") for k, v in replacements.items()
    ):
        raise ValueError("Map explicit absolute path prefixes to visible placeholder labels")
    if out.exists():
        raise FileExistsError("Choose a new public export directory")
    manifest = json.loads((source / "manifest.json").read_text())
    original = source / "evidence.tar.gz"
    if sha256(original) != manifest["archive_sha256"]:
        raise ValueError("Original archive checksum changed")
    out.mkdir(parents=True)

    def redact(data: bytes) -> bytes:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return data
        for old, new in sorted(replacements.items(), key=lambda pair: -len(pair[0])):
            text = text.replace(old, new)
        return sanitize(text.encode("utf-8"))

    members: dict[str, dict[str, Any]] = {}
    packed = out / "evidence.tar.gz"
    with tarfile.open(original, "r:gz") as source_tar, packed.open("wb") as raw:
        with gzip.GzipFile(
            filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=6
        ) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as target:
                for item in source_tar:
                    path = PurePosixPath(item.name)
                    if (
                        not item.isfile()
                        or path.is_absolute()
                        or ".." in path.parts
                        or item.name in members
                        or item.size > 200_000_000
                    ):
                        raise ValueError("Unsafe or unexpected evidence archive member")
                    stream = source_tar.extractfile(item)
                    if stream is None:
                        raise ValueError("Missing archive member")
                    before = stream.read()
                    expected = manifest["members"].get(item.name)
                    if (
                        not expected
                        or digest(before) != expected["sha256"]
                        or len(before) != expected["bytes"]
                    ):
                        raise ValueError("Original member verification failed")
                    after = redact(before)
                    members[item.name] = {
                        "original_sha256": digest(before),
                        "sha256": digest(after),
                        "bytes": len(after),
                        "path_redacted": any(old.encode() in before for old in replacements),
                        "publication_redacted": sanitize(before) != before,
                    }
                    info = tarfile.TarInfo(item.name)
                    info.size, info.mode = len(after), 0o644
                    target.addfile(info, io.BytesIO(after))
    if set(members) != set(manifest["members"]):
        raise ValueError("Archive member set differs from its manifest")
    verified = set()
    with tarfile.open(packed, "r:gz") as archive:
        for item in archive:
            stream = archive.extractfile(item)
            if stream is None or digest(stream.read()) != members[item.name]["sha256"]:
                raise ValueError("Public export verification failed")
            verified.add(item.name)
    if verified != set(members):
        raise ValueError("Published member set is incomplete")
    for name in ("metrics.tsv", "processing.tsv", "resources.tsv", "library-qc.tsv"):
        if (source / name).exists():
            (out / name).write_bytes(redact((source / name).read_bytes()))
    result = {
        "schema_version": 1,
        "status": manifest["status"],
        "experiment_id": manifest["experiment_id"],
        "original_archive_sha256": manifest["archive_sha256"],
        "archive_sha256": sha256(packed),
        "original_manifest_sha256": sha256(source / "manifest.json"),
        "archive_bytes": packed.stat().st_size,
        "members": members,
        "path_placeholders": sorted(set(replacements.values())),
        "exporter_sha256": sha256(Path(__file__)),
        "scope": (
            "Public sanitized derivative. Original and published bytes have "
            "separate hashes. FASTQs, BAMs and reference indexes are excluded. "
            "Reconstruct local paths using the experiment recipe."
        ),
    }
    (out / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--path-map", type=Path, required=True, help="Local JSON object; do not publish it"
    )
    args = parser.parse_args()
    export(args.source, args.out, json.loads(args.path_map.read_text()))
