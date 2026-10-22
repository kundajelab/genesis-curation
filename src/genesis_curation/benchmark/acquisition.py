"""Acquire public source files and freeze their measured SHA256 identities."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .spec import identifier, identity, keys, positive, public_url, sha256, write_json


def acquire(manifest: Path, out: Path, *, connections: int = 1) -> Path:
    """Verify publisher checksums before promoting downloaded files into a local lock.

    A publisher MD5 is a transport-integrity check, not authentication. The source
    snapshot and newly measured SHA256 are both retained. Existing benchmark fetch
    continues to require SHA256-pinned assets.
    """
    if type(connections) is not int or not 1 <= connections <= 4:
        raise ValueError("Use one to four download connections")
    source = json.loads(manifest.read_text())
    fields = {"schema_version", "assets", "limits"}
    keys(source, fields, fields, "acquisition")
    if type(source["schema_version"]) is not int or source["schema_version"] != 1:
        raise ValueError("Unsupported acquisition schema")
    keys(source["limits"], {"download_bytes", "seconds"}, {"download_bytes", "seconds"}, "limits")
    for key, value in source["limits"].items():
        positive(value, key)
    if not isinstance(source["assets"], list) or not source["assets"]:
        raise ValueError("Acquisition needs at least one asset")
    names: set[str] = set()
    for asset in source["assets"]:
        required = {"id", "url", "bytes", "checksum", "source"}
        keys(asset, required, required, "asset")
        identifier(asset["id"])
        if (
            asset["id"] in names
            or asset["id"] in {"acquisition.json", "acquisition-state.json", "assets.lock.json"}
            or asset["id"].endswith(".partial")
        ):
            raise ValueError("Duplicate or reserved acquisition asset ID")
        names.add(asset["id"])
        public_url(asset["url"])
        positive(asset["bytes"], "asset bytes")
        keys(asset["checksum"], {"algorithm", "value"}, {"algorithm", "value"}, "checksum")
        algorithm = asset["checksum"]["algorithm"]
        length = {"md5": 32, "sha256": 64}.get(algorithm) if isinstance(algorithm, str) else None
        if (
            length is None
            or not isinstance(asset["checksum"]["value"], str)
            or not re.fullmatch(f"[0-9a-f]{{{length}}}", asset["checksum"]["value"])
        ):
            raise ValueError("A publisher MD5 or SHA256 is required")
        if not isinstance(asset["source"], dict) or not asset["source"]:
            raise ValueError("Source metadata/snapshot required")
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    lock = out / "acquisition.json"
    state_path = out / "acquisition-state.json"
    if lock.exists():
        if json.loads(lock.read_text()) != source:
            raise ValueError("Acquisition inputs changed; use a new output directory")
    else:
        if any(out.iterdir()):
            raise ValueError("New acquisition needs an empty directory")
        write_json(lock, source)
    state: dict[str, Any] = (
        json.loads(state_path.read_text())
        if state_path.exists()
        else {"identity": identity(source), "downloaded_bytes": 0, "seconds": 0.0, "assets": {}}
    )
    if state["identity"] != identity(source):
        raise ValueError("Acquisition state identity mismatch")
    remaining = sum(
        a["bytes"]
        - (
            (out / (a["id"] + ".partial")).stat().st_size
            if (out / (a["id"] + ".partial")).is_file()
            else 0
        )
        for a in source["assets"]
        if not (out / a["id"]).exists()
    )
    if remaining + state["downloaded_bytes"] > source["limits"]["download_bytes"]:
        raise ValueError("Acquisition exceeds remaining cumulative download budget")
    started = time.monotonic()
    elapsed_before = state["seconds"]
    mutex = threading.Lock()

    def checkpoint() -> None:
        state["seconds"] = elapsed_before + time.monotonic() - started
        write_json(state_path, state)

    try:
        for asset in source["assets"]:
            checkpoint()
            if state["seconds"] >= source["limits"]["seconds"]:
                raise ValueError("Acquisition time budget exceeded")
            target = out / asset["id"]
            if target.is_symlink():
                raise ValueError("Acquisition target must not be a symlink")
            expected = asset["checksum"]
            if target.exists():
                with target.open("rb") as stream:
                    digest = hashlib.file_digest(stream, expected["algorithm"]).hexdigest()
                if target.stat().st_size != asset["bytes"] or digest != expected["value"]:
                    raise ValueError("Existing acquisition checksum differs: " + asset["id"])
            else:
                partial = target.with_name(target.name + ".partial")
                if partial.is_symlink() or (partial.exists() and not partial.is_file()):
                    raise ValueError("Partial download must be a regular file")
                offset = partial.stat().st_size if partial.exists() else 0
                if offset > asset["bytes"]:
                    raise ValueError("Partial file exceeds publisher size")
                if shutil.disk_usage(out).free < asset["bytes"] + 1_000_000_000:
                    raise ValueError("Insufficient download storage")
                # Bounded ranges survive servers that close long responses. A
                # partial file is untrusted until the complete publisher digest
                # matches. Retrying never resets the cumulative transfer budget.
                chunk_bytes = 8 * 1024 * 1024

                def chunk(
                    start: int, asset: dict[str, Any] = asset, chunk_bytes: int = chunk_bytes
                ) -> bytes:
                    end = min(start + chunk_bytes, asset["bytes"]) - 1
                    request = urllib.request.Request(
                        asset["url"],
                        headers={
                            "User-Agent": "Genesis-benchmark/1",
                            "Range": f"bytes={start}-{end}",
                        },
                    )
                    blocks = []
                    received = 0
                    with urllib.request.urlopen(request, timeout=30) as response:
                        public_url(response.geturl())
                        if (
                            response.status != 206
                            or response.headers.get("Content-Range")
                            != f"bytes {start}-{end}/{asset['bytes']}"
                        ):
                            raise ValueError("Server did not honor the exact byte range")
                        while block := response.read(min(1024 * 1024, end - start + 2 - received)):
                            received += len(block)
                            with mutex:
                                state["downloaded_bytes"] += len(block)
                                checkpoint()
                                if (
                                    state["downloaded_bytes"] > source["limits"]["download_bytes"]
                                    or state["seconds"] > source["limits"]["seconds"]
                                ):
                                    raise ValueError("Acquisition byte/time budget exceeded")
                            if received > end - start + 1:
                                raise ValueError("Server exceeded requested byte range")
                            blocks.append(block)
                    if received != end - start + 1:
                        raise ValueError("Short byte range; partial download retained for resume")
                    return b"".join(blocks)

                with (
                    ThreadPoolExecutor(max_workers=connections) as pool,
                    partial.open("ab") as stream,
                ):
                    for block in pool.map(
                        chunk, range(offset, asset["bytes"], chunk_bytes), buffersize=connections
                    ):
                        stream.write(block)
                with partial.open("rb") as stream:
                    digest = hashlib.file_digest(stream, expected["algorithm"]).hexdigest()
                if partial.stat().st_size != asset["bytes"] or digest != expected["value"]:
                    raise ValueError("Publisher size/checksum mismatch: " + asset["id"])
                partial.replace(target)
            state["assets"][asset["id"]] = {
                **asset,
                "path": str(target),
                "sha256": sha256(target),
                "verified": True,
            }
            checkpoint()
        result = out / "assets.lock.json"
        write_json(
            result, {"schema_version": 1, "source_sha256": sha256(lock), "assets": state["assets"]}
        )
        return result
    finally:
        checkpoint()
