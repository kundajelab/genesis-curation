"""Sanitize public evidence copies without changing execution records or scientific hashes.

This is a targeted publication guard, not a replacement for a credential scanner.
Review-state fingerprints are not credentials, but are unnecessary in public logs.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import shutil
import tarfile
from contextlib import nullcontext
from pathlib import Path, PurePosixPath
from typing import Any

MAX_MEMBER = 200_000_000
MAX_EXPANDED = 2_000_000_000
MAX_MEMBERS = 50_000
QUOTE = r"\\*[\"']"
REVIEW = re.compile(
    rf"((?:{QUOTE})(?:token|input_token|expected_token)(?:{QUOTE})\s*:\s*(?:{QUOTE}))"
    rf"[a-f0-9]{{64}}(?={QUOTE})"
)
ARGUMENT = re.compile(
    rf"(--token(?:(?:{QUOTE})\s*,\s*(?:{QUOTE})|\s+|=)(?:{QUOTE})?)"
    r"[a-f0-9]{64}(?![a-f0-9])"
)
TOKEN_MAP = re.compile(rf"(?:{QUOTE})review_tokens(?:{QUOTE})\s*:\s*\{{[^{{}}]*\}}")
MAP_VALUE = re.compile(rf"(:\s*(?:{QUOTE}))[a-f0-9]{{64}}(?={QUOTE})")
CREDENTIAL = re.compile(
    rf"(?i)(\b(?:api[_-]?key|access[_-]?token|password|secret|hf_token|openai_api_key)"
    rf"(?:{QUOTE})?\s*[:=]\s*(?:{QUOTE})?)([^\s,;\"'\\}}]+)"
)
QUOTED_CREDENTIAL = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|password|secret|hf_token|openai_api_key)"
    r"[\"']?\s*[:=]\s*)(?P<q>[\"'])(?:\\.|(?!(?P=q)).)*(?P=q)"
)
AUTHORIZATION = re.compile(
    rf"(?i)(\bauthorization(?:{QUOTE})?\s*[:=]\s*(?:{QUOTE})?"
    r"(?:bearer|basic)\s+)([^\s,;\"'\\}]+)"
)
PROVIDER = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|"
    r"AKIA[A-Z0-9]{16}|(?:hf_|sk-)[A-Za-z0-9_-]{20,})\b"
)
PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sanitize(data: bytes) -> bytes:
    """Keep all unrelated bytes, including line endings and scientific digests."""
    lowered = data.lower()
    if not any(
        marker in lowered
        for marker in (
            b"token",
            b"secret",
            b"password",
            b"api",
            b"authorization",
            b"private key",
            b"ghp_",
            b"gho_",
            b"ghu_",
            b"ghs_",
            b"ghr_",
            b"github_pat_",
            b"akia",
            b"hf_",
            b"sk-",
        )
    ):
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    if PRIVATE_KEY.search(text):
        raise ValueError("Private key material requires removal before publication")
    text = REVIEW.sub(r"\1REDACTED", text)
    text = ARGUMENT.sub(r"\1REDACTED", text)
    text = TOKEN_MAP.sub(lambda m: MAP_VALUE.sub(r"\1REDACTED", m[0]), text)
    text = QUOTED_CREDENTIAL.sub(r"\1\g<q>[REDACTED]\g<q>", text)
    text = CREDENTIAL.sub(
        lambda m: (
            m[0] if m[2] in {"null", "true", "false", "None", "{", "["} else m[1] + "[REDACTED]"
        ),
        text,
    )
    text = AUTHORIZATION.sub(r"\1[REDACTED]", text)
    return PROVIDER.sub("[REDACTED]", text).encode("utf-8")


def record(before: bytes, after: bytes) -> dict[str, Any]:
    return {
        "original_sha256": digest(before),
        "sha256": digest(after),
        "bytes": len(after),
        "redacted": before != after,
    }


def archive_members(path: Path) -> dict[str, bytes]:
    """Read bounded regular members without extracting paths to the filesystem."""
    members: dict[str, bytes] = {}
    total = 0
    seen: set[str] = set()
    with tarfile.open(path, "r:*") as archive:
        for item in archive:
            name = PurePosixPath(item.name)
            if name.is_absolute() or ".." in name.parts or item.name in seen:
                raise ValueError("Unsafe or duplicate archive member")
            seen.add(item.name)
            if len(seen) > MAX_MEMBERS:
                raise ValueError("Archive member limit exceeded")
            if item.isdir():
                continue
            total += item.size
            if not item.isfile() or item.size > MAX_MEMBER or total > MAX_EXPANDED:
                raise ValueError("Unsafe archive member type or expansion limit exceeded")
            stream = archive.extractfile(item)
            if stream is None:
                raise ValueError("Unreadable archive member")
            with stream:
                members[item.name] = stream.read()
    return members


def is_archive(path: Path) -> bool:
    return path.name.endswith((".tar.gz", ".tgz", ".tar"))


def export(source: Path, out: Path) -> dict[str, Any]:
    """Create a public derivative and a manifest binding both sets of bytes."""
    source, out = source.resolve(), out.resolve()
    if not source.is_dir() or out.exists() or out.is_relative_to(source):
        raise ValueError("Choose a new output directory outside the source directory")
    out.mkdir(parents=True)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "scope": "Sanitized publication copy; original receipt hashes describe private originals",
        "files": {},
    }
    try:
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise ValueError("Publication sources must not contain symlinks")
            if not path.is_file():
                continue
            if path.stat().st_size > MAX_MEMBER:
                raise ValueError("Publication file limit exceeded")
            relative = str(path.relative_to(source))
            if relative == "publication-manifest.json":
                raise ValueError("Export original evidence, not an existing publication directory")
            target = out / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            before = path.read_bytes()
            if is_archive(path):
                members = archive_members(path)
                sanitized = {name: sanitize(data) for name, data in members.items()}
                if sanitized == members:
                    target.write_bytes(before)
                else:
                    with target.open("wb") as raw:
                        stream = (
                            nullcontext(raw)
                            if target.suffix == ".tar"
                            else gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0)
                        )
                        with stream as gz:
                            with tarfile.open(fileobj=gz, mode="w|") as archive:
                                for name, data in sorted(sanitized.items()):
                                    info = tarfile.TarInfo(name)
                                    info.size, info.mode = len(data), 0o644
                                    archive.addfile(info, io.BytesIO(data))
                if archive_members(target) != sanitized:
                    raise ValueError("Published archive verification failed")
                row = record(before, target.read_bytes())
                row["members"] = {
                    name: record(members[name], data) for name, data in sanitized.items()
                }
            else:
                after = sanitize(before)
                target.write_bytes(after)
                row = record(before, after)
            manifest["files"][relative] = row
        (out / "publication-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    except BaseException:
        shutil.rmtree(out)
        raise
    return manifest


def verify_manifest(path: Path) -> None:
    """Verify published bytes; original hashes remain provenance claims."""
    manifest = json.loads(path.read_bytes())
    if manifest["schema_version"] != 1:
        raise ValueError("Unsupported publication schema")
    root = path.parent.resolve()
    for name, row in manifest["files"].items():
        relative = PurePosixPath(name)
        target = root / name
        if relative.is_absolute() or ".." in relative.parts or target.is_symlink():
            raise ValueError("Unsafe manifest path")
        if not target.resolve().is_relative_to(root) or target.stat().st_size > MAX_MEMBER:
            raise ValueError("Unsafe manifest target")
        data = target.read_bytes()
        if digest(data) != row["sha256"] or len(data) != row["bytes"]:
            raise ValueError("Publication checksum mismatch")
        if "members" in row:
            members = archive_members(target)
            if members.keys() != row["members"].keys():
                raise ValueError("Publication archive membership mismatch")
            for member, data in members.items():
                expected = row["members"][member]
                if digest(data) != expected["sha256"] or len(data) != expected["bytes"]:
                    raise ValueError("Publication member checksum mismatch")


def check(root: Path) -> list[str]:
    """Return locations only; never include matched values in diagnostics."""
    if not root.is_dir():
        raise ValueError("Publication root must be an existing directory")
    findings: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            findings.append(str(path.relative_to(root)) + ": symlink")
            continue
        if not path.is_file():
            continue
        name = str(path.relative_to(root))
        try:
            if path.stat().st_size > MAX_MEMBER:
                raise ValueError("File limit exceeded")
            if path.name == "publication-manifest.json":
                verify_manifest(path)
            contents = archive_members(path) if is_archive(path) else {"": path.read_bytes()}
            for member, data in contents.items():
                if sanitize(data) != data:
                    findings.append(name + ("::" + member if member else ""))
        except ValueError, KeyError, TypeError, AttributeError, tarfile.TarError, OSError:
            findings.append(name + ": unreadable or unsafe publication")
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("export")
    publish.add_argument("source", type=Path)
    publish.add_argument("out", type=Path)
    inspect = commands.add_parser("check")
    inspect.add_argument("root", type=Path)
    args = parser.parse_args()
    if args.command == "export":
        export(args.source, args.out)
    else:
        findings = check(args.root)
        for finding in findings:
            print(finding)
        raise SystemExit(bool(findings))


if __name__ == "__main__":
    main()
