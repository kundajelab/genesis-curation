"""Fresh metadata-only state and explicit workspace initialization."""

from __future__ import annotations

import contextlib
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from helpers import rejects

from genesis_curation.registry import store
from genesis_curation.web import runtime, workspace


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="genesis-v1-") as folder:
        root = Path(folder)
        runtime.initialize(root, "curator", "synthetic-v1-password")
        with contextlib.closing(store.connect(root / "catalog")) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
            assert db.execute("PRAGMA application_id").fetchone()[0] == store.APPLICATION_ID
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert not tables & {"datasets", "artifacts", "attempts", "qc_reports"}
        assert workspace.summary(root)["counts"] == []
        assert workspace.export_bundle(root, "clean", True)["rows"] == 0
        print("PASS fresh workspace: empty inventory and no implicit demo data")
        old = root / "prototype"
        old.mkdir()
        with sqlite3.connect(old / "registry.sqlite") as db:
            db.execute("PRAGMA user_version=11")
        rejects(lambda: store.initialize(old), "Unsupported")
        print("PASS unsupported prototype rejection")
    help_text = subprocess.check_output(
        [sys.executable, "-m", "genesis_curation.cli", "--help"], text=True
    )
    assert "{web,metadata,ai,curation,workspace,sheets,eval}" in help_text
    print("PASS metadata-only command surface")


if __name__ == "__main__":
    main()
