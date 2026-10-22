"""Download a bounded public reference panel and record its integrity evidence.

SHA256 values measured on a first download are not publisher attestations. This
recipe preserves that distinction and checks publisher MD5/Git object IDs or
legacy BSD checksums when provided. Subsequent runs require the recorded bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from genesis_curation.benchmark.spec import identity, sha256, write_json


def verify(path: Path, expected: dict[str, Any]) -> dict[str, Any]:
    kind, value = expected["kind"], expected["value"]
    if kind == "md5":
        digest = hashlib.md5(usedforsecurity=False)
    elif kind == "git-blob-sha1":
        digest = hashlib.sha1(usedforsecurity=False)
        digest.update(f"blob {path.stat().st_size}\0".encode())
    elif kind == "sha256":
        digest = hashlib.sha256()
    elif kind == "bsd-sum":
        actual = subprocess.run(
            ["sum", "-r", str(path)], check=True, text=True, capture_output=True
        ).stdout.split()[:2]
        if list(map(int, actual)) != value:
            raise ValueError("Publisher BSD checksum differs: " + path.name)
        return {
            "kind": kind,
            "value": value,
            "strength": "legacy non-cryptographic checksum",
        }
    elif kind == "observed-only":
        return {
            "kind": kind,
            "value": None,
            "strength": "HTTPS retrieval and locally measured SHA256 only; no publisher checksum",
        }
    else:
        raise ValueError("Unknown integrity scheme")
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    if digest.hexdigest() != value:
        raise ValueError("Publisher digest differs: " + path.name)
    return {
        "kind": kind,
        "value": value,
        "strength": "publisher value matched; not an authentication signature",
    }


def fetch(manifest: Path, out: Path) -> dict[str, Any]:
    doc = json.loads(manifest.read_text())
    if doc["schema_version"] != 1 or not 0 < doc["download_budget_bytes"] <= 3_000_000_000:
        raise ValueError("Unsupported manifest or reference download budget")
    out.mkdir(parents=True, exist_ok=True)
    state_path = out / "downloads.json"
    state = (
        json.loads(state_path.read_text())
        if state_path.exists()
        else {
            "schema_version": 1,
            "manifest_identity": identity(doc),
            "downloaded_bytes": 0,
            "assets": {},
        }
    )
    if state["manifest_identity"] != identity(doc):
        raise ValueError("Download manifest changed; choose a new directory")
    for asset in doc["assets"]:
        name, url = asset["name"], asset["url"]
        parsed = urlsplit(url)
        if (
            Path(name).name != name
            or name.startswith(".")
            or parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Expected a basename and public HTTPS URL")
        target = out / name
        if name in state["assets"]:
            if sha256(target) != state["assets"][name]["sha256"]:
                raise ValueError("Downloaded reference changed")
            continue
        if target.exists() or target.is_symlink():
            raise ValueError("Unrecorded target already exists")
        partial = out / (name + ".partial")
        if partial.is_symlink():
            raise ValueError("Refuse symlink download target")
        error = None
        for attempt in range(3):
            try:
                # Restart a failed transfer; all attempts count against the same budget.
                with (
                    urllib.request.urlopen(
                        urllib.request.Request(url, headers={"User-Agent": "Genesis-benchmark/1"}),
                        timeout=60,
                    ) as response,
                    partial.open("wb") as stream,
                ):
                    content_type = response.headers.get("Content-Type", "")
                    if "text/html" in content_type:
                        raise ValueError("Expected an asset, received an HTML page")
                    while block := response.read(1024 * 1024):
                        state["downloaded_bytes"] += len(block)
                        if (
                            state["downloaded_bytes"] > doc["download_budget_bytes"]
                            or stream.tell() + len(block) > asset["max_bytes"]
                        ):
                            raise ValueError("Reference download budget exceeded")
                        stream.write(block)
                    resolved_url = response.url
                integrity = verify(partial, asset["integrity"])
                digest = sha256(partial)
                partial.rename(target)
                state["assets"][name] = {
                    "url": url,
                    "resolved_url": resolved_url,
                    "sha256": digest,
                    "bytes": target.stat().st_size,
                    "integrity": integrity,
                    "retrieved_at": datetime.now(UTC).isoformat(),
                }
                error = None
                print(name, target.stat().st_size, digest, flush=True)
                break
            except (OSError, TimeoutError) as exc:
                error = exc
                print(name, "attempt", attempt + 1, type(exc).__name__, flush=True)
                if attempt < 2:
                    time.sleep(5)
            finally:
                write_json(state_path, state)
        if error:
            raise error
    return state


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    fetch(args.manifest, args.out)
