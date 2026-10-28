"""Discover plant single-cell ATAC inputs without inferring biological identities."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from . import evidence, inventory, model, registry, workflow


def configure(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="scatac_command", required=True)
    child = commands.add_parser("register")
    child.add_argument("--input", type=Path, required=True)
    child.add_argument("--registry", type=Path, required=True)
    child.set_defaults(curation_handler=execute)
    child = commands.add_parser(
        "import-workflow", help="Group bundles from one genesis-preprocessing scATAC library"
    )
    child.add_argument("--run", type=Path, required=True, help="output/<species>/<sample_id>")
    child.add_argument("--fasta", type=Path, required=True)
    child.add_argument("--reference-id", required=True)
    child.add_argument("--assembly", required=True)
    child.add_argument("--species", required=True)
    child.add_argument("--mitochondrial", nargs="*", default=[])
    child.add_argument("--plastid", nargs="*", default=[])
    child.add_argument("--bigwig-python", type=Path, required=True)
    child.add_argument("--output", type=Path, required=True)
    child.add_argument("--resume", action="store_true")
    child.set_defaults(curation_handler=execute)
    child = commands.add_parser("qc")
    child.add_argument("--input", type=Path, required=True)
    child.set_defaults(curation_handler=execute)
    model_actions = commands.add_parser("model").add_subparsers(dest="model_action", required=True)
    for name in ("export", "validate"):
        child = model_actions.add_parser(name)
        child.add_argument("--input", type=Path, required=True)
        if name == "export":
            child.add_argument("--config", type=Path, required=True)
            child.add_argument("--output", type=Path, required=True)
            child.add_argument("--resume", action="store_true")
        child.set_defaults(curation_handler=execute)
    child = model_actions.add_parser("inspect", help="Read-only, bounded model handoff report")
    child.add_argument("--input", type=Path, required=True)
    child.add_argument("--format", choices=("json", "markdown"), default="json")
    child.add_argument("--output", type=Path)
    child.add_argument("--max-bytes", type=int, default=64 * 1024 * 1024)
    child.add_argument("--max-records", type=int, default=100_000)
    child.add_argument("--max-seconds", type=float, default=30)
    child.add_argument("--bigwig-python", type=Path)
    child.set_defaults(curation_handler=execute, operation="scatac-model-inspect")
    actions = commands.add_parser("inventory").add_subparsers(dest="operation", required=True)
    for name in ("import", "audit", "export", "evidence"):
        child = actions.add_parser(name)
        child.add_argument("directory", type=Path)
        child.set_defaults(curation_handler=execute)
        if name == "import":
            child.add_argument("--source", type=Path, required=True)
            child.add_argument("--source-id", required=True)
            child.add_argument("--registry", type=Path, required=True)
            child.add_argument("--sheet", default="scATAC Fable Curated")
        if name == "evidence":
            child.add_argument("--source", type=Path, required=True)
        if name == "export":
            child.add_argument("--output", type=Path, required=True)


def execute(args: argparse.Namespace) -> tuple[Any, int]:
    if args.scatac_command == "register":
        return registry.register(args.input, args.registry), 0
    if args.scatac_command == "import-workflow":
        fields = {
            "fasta": args.fasta,
            "reference_id": args.reference_id,
            "assembly": args.assembly,
            "species": args.species,
            "mitochondrial_contigs": args.mitochondrial,
            "plastid_contigs": args.plastid,
        }
        return workflow.import_library(
            args.run, fields, args.bigwig_python, args.output, resume=args.resume
        ), 0
    if args.scatac_command == "qc":
        from .common import verify_output

        value = verify_output(args.input, "scatac-group")
        return {"qc": value["data"]["qc"], "review_status": "UNREVIEWED"}, 0
    if args.scatac_command == "model":
        if args.model_action == "inspect":
            from .inspection import command

            return command(args)
        if args.model_action == "validate":
            return model.validate(args.input), 0
        return model.export(args.input, args.config, args.output, resume=args.resume), 0
    if args.operation == "import":
        return inventory.import_workbook(
            args.source, args.directory, args.registry, args.source_id, args.sheet
        ), 0
    if args.operation == "evidence":
        return evidence.attach(args.directory, args.source), 0
    if args.operation == "export":
        return inventory.export(args.directory, args.output), 0
    return inventory.audit(args.directory), 0
