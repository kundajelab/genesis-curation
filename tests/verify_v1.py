"""Fresh metadata-only state and truthful neutral demo provenance."""

from __future__ import annotations

import contextlib
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from helpers import rejects

from genesis_curation.contracts.records import canonical
from genesis_curation.registry import store
from genesis_curation.web import runtime, workspace


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="genesis-v1-") as folder:
        root = Path(folder)
        runtime.initialize(root, "curator", "synthetic-v1-password")
        with contextlib.closing(store.connect(root / "catalog")) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 1
            assert db.execute("PRAGMA application_id").fetchone()[0] == store.APPLICATION_ID
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert not tables & {"datasets", "artifacts", "attempts", "qc_reports"}
        source = root / "demo.jsonl"
        header = {"format": "genesis-demo/1", "rows": 1, "source_snapshot": "a" * 64}
        item = {
            "table": "reference-1",
            "row": 2,
            "modality": "Bulk ATAC",
            "original": {"species": "Arabidopsis thaliana", "study_accession": "SRP123456"},
            "cells": {},
        }
        source.write_text(canonical(header) + "\n" + canonical(item) + "\n")
        manifest = workspace.import_demo(root, source)
        assert manifest["rows"] == 1 and manifest["origin"] == "demo-reference"
        assert workspace.import_demo(root, source) == manifest
        row = workspace.page(root, "Bulk ATAC")["items"][0]
        detail = workspace.history(root, row["id"])
        assert row["status"] == "Imported" and not row["subject"]
        assert detail["record"]["origin"] == "demo-reference"
        assert [e["body"]["action"] for e in detail["events"]] == ["demo-import"]
        assert not detail["changes"]
        assert workspace.export_bundle(root, "clean", True)["rows"] == 0
        print("PASS neutral demo import: complete, idempotent, traceable and unapproved")
        header["rows"] = 2
        source.write_text(canonical(header) + "\n" + canonical(item) + "\n")
        rejects(lambda: workspace.import_demo(root, source), "row count")
        assert workspace.page(root, "Bulk ATAC")["total"] == 1
        old = root / "prototype"
        old.mkdir()
        with sqlite3.connect(old / "registry.sqlite") as db:
            db.execute("PRAGMA user_version=11")
        rejects(lambda: store.initialize(old), "Unsupported")
        print("PASS invalid import rollback and unsupported prototype rejection")
    help_text = subprocess.check_output(
        [sys.executable, "-m", "genesis_curation.cli", "--help"], text=True
    )
    assert "{web,metadata,ai}" in help_text
    print("PASS metadata-only command surface")


if __name__ == "__main__":
    main()
