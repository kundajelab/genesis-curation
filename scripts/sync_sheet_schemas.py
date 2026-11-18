#!/usr/bin/env python3
"""Copy the sample-sheet schemas from a genesis-preprocessing checkout into the package.

    python scripts/sync_sheet_schemas.py PATH/TO/genesis-preprocessing

Writes src/genesis_curation/sheets/schemas/<workflow>.samples.schema.json and SOURCE.json,
which records the source commit and each file's SHA-256 (checked by verify_sheets_rna.py).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "src/genesis_curation/sheets/schemas"


def sync(checkout: Path) -> dict[str, object]:
    commit = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    files = {}
    for source in sorted((checkout / "assets").glob("*.samples.schema.json")):
        data = source.read_bytes()
        json.loads(data)
        (TARGET / source.name).write_bytes(data)
        files[source.name] = hashlib.sha256(data).hexdigest()
    if not files:
        raise ValueError(f"No assets/*.samples.schema.json under {checkout}")
    note = {"repository": "kundajelab/genesis-preprocessing", "commit": commit, "sha256": files}
    (TARGET / "SOURCE.json").write_text(json.dumps(note, indent=2) + "\n")
    return note


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    print(json.dumps(sync(parser.parse_args().checkout), indent=2))
