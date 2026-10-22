"""Register verified outputs as immutable, unreviewed scientific artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..contracts.records import identity, load, record
from ..registry import store
from .common import verify_output

KINDS = {
    "scatac-fragments",
    "scatac-pseudobulk-core",
    "scatac-group-core",
    "scatac-group",
    "scatac-model",
}


def register(directory: Path, registry: Path) -> dict[str, Any]:
    header = load(directory / "complete.json")
    kind = header.get("kind")
    if kind not in KINDS:
        raise ValueError("Unsupported scientific output")
    manifest = verify_output(directory, kind)
    value = record(
        "scatac_output",
        {
            "location": str(directory.resolve()),
            "output_kind": kind,
            "manifest_version": manifest["version"],
            "manifest": manifest,
            "review_status": "UNREVIEWED",
            "model_ready": False,
        },
        identity("scatac-output", manifest["version"]),
        version=4,
    )
    store.initialize(registry)
    with store.write(registry) as db:
        store.put(db, value)
    return {
        "record": value["version"],
        "manifest": manifest["version"],
        "review_status": "UNREVIEWED",
        "model_ready": False,
    }
