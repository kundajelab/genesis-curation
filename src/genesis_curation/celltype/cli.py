"""Explicit RNA annotation handoff, proposal import and reviewed cell export."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from . import adapter


def configure(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="operation", required=True)
    prepare = commands.add_parser("prepare", help="Record an external RNA annotation handoff")
    for name in ("skill-repo", "h5ad", "context", "output"):
        prepare.add_argument("--" + name, type=Path, required=True)
    proposal = commands.add_parser("import", help="Import ordered cluster labels as proposals")
    for name in ("handoff", "annotations", "observations", "cells", "output"):
        proposal.add_argument("--" + name, type=Path, required=True)
    export = commands.add_parser("export", help="Export individually reviewed annotation rules")
    for name in ("input", "decisions", "output"):
        export.add_argument("--" + name, type=Path, required=True)
    for child in (prepare, proposal, export):
        child.set_defaults(curation_handler=execute)


def execute(args: argparse.Namespace) -> tuple[Any, int]:
    if args.operation == "prepare":
        return adapter.prepare(args.skill_repo, args.h5ad, args.context, args.output), 0
    if args.operation == "import":
        return adapter.propose(
            args.handoff, args.annotations, args.observations, args.cells, args.output
        ), 0
    return adapter.export(args.input, args.decisions, args.output), 0
