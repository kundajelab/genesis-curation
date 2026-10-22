"""Delegated source ingestion and metadata harmonization commands."""

from __future__ import annotations

import argparse
import contextlib
from pathlib import Path
from typing import Any

from ..contracts.records import dump, loads
from ..llm.providers import select
from ..registry import store
from . import harmonize, review


def configure(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("ingest", "propose", "diff", "apply"):
        child = commands.add_parser(name)
        child.set_defaults(curation_handler=execute)
        child.add_argument("directory", type=Path)
        child.add_argument("dataset")
        child.add_argument("--json", action="store_true")
        child.add_argument("--scope", choices=("inputs", "results"), default="results")
        if name == "ingest":
            child.add_argument("--source", type=Path, action="append", default=[])
            child.add_argument("--accession", action="append", default=[])
            child.add_argument(
                "--classification", required=True, choices=tuple(harmonize.CLASSIFICATIONS)
            )
        if name == "propose":
            child.add_argument("--config", type=Path)
            child.add_argument("--provider")
            child.add_argument(
                "--fields", type=Path, help="Curator-authored fields with exact evidence"
            )
            child.add_argument("--output", type=Path)
        if name in {"diff", "apply"}:
            child.add_argument("--target", required=True)
        if name == "apply":
            child.add_argument("--output", required=True, type=Path)


def execute(args: argparse.Namespace) -> tuple[Any, int]:
    if args.operation == "ingest":
        return harmonize.ingest(
            args.directory,
            args.dataset,
            args.source,
            args.accession,
            args.classification,
            scope=args.scope,
        ), 0
    if args.operation == "propose":
        if bool(args.config) != bool(args.provider) or args.fields and args.provider:
            raise ValueError("Choose manual extraction, --fields, or both --config and --provider")
        result = harmonize.propose(
            args.directory,
            args.dataset,
            select(args.config, args.provider) if args.config else None,
            args.fields,
            scope=args.scope,
        )
        if args.output:
            dump(args.output, result, immutable=True)
        return result, 0
    if args.operation == "apply":
        return harmonize.apply(
            args.directory, args.dataset, args.target, args.output, scope=args.scope
        ), 0
    with contextlib.closing(store.connect(args.directory, scope=args.scope)) as db:
        proposal = store.get(db, "proposal", args.target)
        if proposal["data"]["dataset_id"] != args.dataset:
            raise ValueError("Proposal belongs to another dataset")
        row = store.current(db, args.dataset)
        try:
            harmonize.check_proposal(db, proposal)
            stale = False
        except ValueError:
            stale = True
        return {
            "proposal": proposal,
            "before": loads(row["metadata"]),
            "stale": stale,
            "token": review.token(db, args.dataset),
        }, 0
