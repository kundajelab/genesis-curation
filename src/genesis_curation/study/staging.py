"""Stage small immutable compiler documents inside an explicitly owned worker namespace."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from ..contracts.records import canonical, dump, fingerprint, load

DOCUMENTS = {"samples.tsv", "references.json", "site.config", "metadata.json"}


def stage(folder: Path, payload: dict[str, Any]) -> None:
    spec = payload["study"]
    if type(spec.get("schema_version")) is not int or spec["schema_version"] != 1:
        raise ValueError("Unsupported study worker payload")
    root = Path(spec["input_root"])
    owned = folder.parent.resolve() / "study-inputs"
    if not root.is_absolute() or not root.resolve().is_relative_to(owned):
        raise ValueError("Study documents must be inside this worker's study-inputs namespace")
    parts = root.resolve().relative_to(owned).parts
    if (
        len(parts) != 2
        or not re.fullmatch(r"[a-f0-9]{64}", parts[0])
        or not re.fullmatch(r"job-[a-f0-9]{20}", parts[1])
    ):
        raise ValueError("Invalid compiled input namespace")
    documents = spec["documents"]
    if not isinstance(documents, list) or not 2 <= len(documents) <= 4:
        raise ValueError("A study job needs 2–4 bounded compiled documents")
    expected = {i["path"]: i["sha256"] for i in payload["inputs"]}
    seen, total = set(), 0
    for document in documents:
        name, content = document["name"], document["text"].encode()
        if name not in DOCUMENTS or name in seen or len(content) > 2 * 1024 * 1024:
            raise ValueError("Invalid, duplicate or oversized study document")
        seen.add(name)
        total += len(content)
        checksum = hashlib.sha256(content).hexdigest()
        if checksum != document["sha256"] or expected.get(str(root / name)) != checksum:
            raise ValueError("Compiled study document digest mismatch")
    if total > 8 * 1024 * 1024 or not {"samples.tsv", "metadata.json"} <= seen:
        raise ValueError("Missing required documents or study staging budget exceeded")
    root.parent.mkdir(parents=True, exist_ok=True)
    # Publish a complete directory, so a controller interruption cannot expose partial inputs.
    if not root.exists():
        with tempfile.TemporaryDirectory(prefix=".study-", dir=root.parent) as temporary:
            target = Path(temporary) / "inputs"
            target.mkdir(mode=0o700)
            for document in documents:
                path = target / document["name"]
                path.write_text(document["text"])
                path.chmod(0o600)
            try:
                target.rename(root)
            except FileExistsError:
                pass
    if root.is_symlink() or {p.name for p in root.iterdir()} != seen:
        raise ValueError("Compiled input namespace has unexpected files")
    for document in documents:
        path = root / document["name"]
        if path.is_symlink() or not path.is_file() or path.read_text() != document["text"]:
            raise ValueError("Existing compiled study documents changed")
    contract = spec["contract"]
    if contract["sheet"] != str(root / "samples.tsv"):
        raise ValueError("Collection sheet differs from the compiled input")
    out = Path(contract["root"])
    if not out.is_absolute() or out.resolve().is_relative_to(Path(payload["repo"]).resolve()):
        raise ValueError("Study output must be an absolute path outside the checkout")
    if any(Path(p).resolve().is_relative_to(out.resolve()) for p in expected):
        raise ValueError("Study output must not contain source inputs")
    owner = {"schema_version": 1, "contract": fingerprint(contract), "git_sha": payload["git_sha"]}
    marker = out / ".genesis-study-owner.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        with tempfile.TemporaryDirectory(prefix=".study-run-", dir=out.parent) as temporary:
            target = Path(temporary) / "run"
            target.mkdir(mode=0o700)
            dump(target / marker.name, owner, immutable=True)
            try:
                target.rename(out)
            except FileExistsError:
                pass
    if out.is_symlink() or not marker.is_file() or load(marker) != owner:
        raise ValueError("Output directory is not owned by this exact study job")
    # Keep provenance and private path metadata protected under the existing user's identity.
    os.chmod(marker, 0o600)
    if len(canonical(payload).encode()) > 12 * 1024 * 1024:
        raise ValueError("Study worker payload exceeds 12 MiB")
