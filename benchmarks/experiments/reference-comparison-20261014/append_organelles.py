"""Build an explicit derived FASTA with selected, unchanged organellar contigs."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from genesis_curation.benchmark.references import text_file
from genesis_curation.benchmark.spec import sha256, write_json


def append(source: Path, donor: Path, names: list[str], out: Path) -> Path:
    if not names or len(set(names)) != len(names):
        raise ValueError("Declare unique organellar contig names")
    receipt = out.with_suffix(out.suffix + ".json")
    inputs = {"nuclear_source": sha256(source), "organelle_donor": sha256(donor)}
    if receipt.exists():
        value = json.loads(receipt.read_text())
        if (
            value["inputs"] != inputs
            or value["selected_contigs"] != names
            or sha256(out) != value["sha256"]
        ):
            raise ValueError("Derived reference identity changed")
        return out
    if out.exists() or out.is_symlink():
        raise ValueError("Choose a new derived reference path")
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_suffix(out.suffix + ".partial")
    if partial.is_symlink():
        raise ValueError("Refuse a symlink partial reference")
    seen, selected = set(), set()
    with (
        partial.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, filename="", mode="wb", mtime=0) as target,
    ):
        with text_file(source) as stream:
            for line in stream:
                if line.startswith(">"):
                    name = line[1:].split()[0]
                    if name in seen or name in names:
                        raise ValueError("Repeated contig or organelle already in source")
                    seen.add(name)
                target.write((line.rstrip() + "\n").encode())
        active = False
        with text_file(donor) as stream:
            for line in stream:
                if line.startswith(">"):
                    name = line[1:].split()[0]
                    active = name in names
                    if active:
                        if name in selected:
                            raise ValueError("Duplicate donor contig")
                        selected.add(name)
                if active:
                    target.write((line.rstrip() + "\n").encode())
    if selected != set(names) or inputs != {
        "nuclear_source": sha256(source),
        "organelle_donor": sha256(donor),
    }:
        raise ValueError("Missing donor sequence or changed source")
    partial.replace(out)
    write_json(
        receipt,
        {
            "schema_version": 1,
            "inputs": inputs,
            "selected_contigs": names,
            "sha256": sha256(out),
            "bytes": out.stat().st_size,
            "derivation": (
                "Nuclear source plus exact named donor contigs; "
                "headers and wrapping normalized only"
            ),
        },
    )
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--donor", type=Path, required=True)
    parser.add_argument("--contig", action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(append(args.source, args.donor, args.contig, args.out))
