"""Reassess stored evidence without rerunning scientific processing."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..contracts.records import load, loads
from ..registry import store
from .evaluate import diagnostic_policy, evaluate


def configure(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="operation", required=True)
    child = commands.add_parser("evaluate")
    child.add_argument("directory", type=Path)
    child.add_argument("dataset")
    child.add_argument("--policy", type=Path)
    child.add_argument("--json", action="store_true")
    child.set_defaults(curation_handler=execute)


def execute(args: argparse.Namespace) -> tuple[Any, int]:
    policy = load(args.policy) if args.policy else diagnostic_policy()
    with store.write(args.directory) as db:
        row = store.current(db, args.dataset)
        if not row["validation"]:
            raise ValueError("Import a validation record before evaluating QC")
        value = evaluate(
            store.get(db, "manifest", row["manifest"]),
            store.get(db, "validation", row["validation"]),
            policy,
            loads(row["metadata"]),
        )
        store.put(db, policy, args.dataset)
        store.put(db, value, args.dataset)
        for finding in value["data"]["findings"]:
            store.put(db, finding, args.dataset)
        db.execute("UPDATE datasets SET assessment=? WHERE id=?", (value["version"], args.dataset))
    return value, 0
