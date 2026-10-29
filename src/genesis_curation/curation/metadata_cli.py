"""Import and export current metadata workspace formats."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..web import catalog, studies, workspace


def configure(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("import", "load-demo", "export"):
        child = commands.add_parser(name)
        child.set_defaults(curation_handler=execute)
        child.add_argument("--root", type=Path, required=True)
        if name != "export":
            child.add_argument("--file", type=Path, required=True)
        if name == "import":
            child.add_argument("--id-column", default="sample_id")
            child.add_argument("--namespace", default="metadata")
            child.add_argument(
                "--classification", choices=("public", "internal", "local-only"), default="public"
            )
        if name == "export":
            child.add_argument("--name", default="metadata")
            child.add_argument("--clean", action="store_true")


def execute(args: argparse.Namespace) -> tuple[Any, int]:
    if args.operation == "load-demo":
        return workspace.import_demo(args.root, args.file), 0
    if args.operation == "export":
        from ..contracts.records import identifier

        return workspace.export_bundle(args.root, identifier(args.name), args.clean), 0
    if args.file.suffix.lower() == ".xlsx":
        return workspace.import_workbook(args.root, args.file), 0
    ids = catalog.import_rows(
        args.root / "catalog",
        args.file.read_text(),
        args.file.name,
        args.namespace,
        args.id_column,
        args.classification,
        "curator",
    )
    studies.link_imports(args.root / "catalog", ids, "curator")
    return {"subjects": ids}, 0
