"""Delegated prepared-study commands; existing review and export policies remain authoritative."""

from __future__ import annotations

import argparse
import contextlib
from pathlib import Path
from typing import Any

from ..contracts.records import dump, fingerprint, identity, load, record, validate
from ..curation.review import profile, reviewed_export
from ..registry import store
from . import inputs, planning, workflow


def scans(parser: argparse.ArgumentParser, *, optional: bool) -> None:
    for name, kind in (("max_bytes", int), ("max_records", int), ("max_seconds", float)):
        parser.add_argument(
            "--" + name.replace("_", "-"),
            type=kind,
            default=None if optional else planning.DEFAULT_SCAN[name],
        )
    parser.add_argument(
        "--level", choices=("metadata", "full"), default=None if optional else "metadata"
    )


def configure(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("init", "plan", "run", "resume", "status", "collect", "export"):
        child = commands.add_parser(name)
        child.add_argument("directory", type=Path)
        child.add_argument("--json", action="store_true")
        child.set_defaults(curation_handler=execute)
        if name == "init":
            child.add_argument("--study-id", required=True)
            child.add_argument("--assay", choices=("DAP-seq", "bulk-ATAC"), required=True)
            child.add_argument("--sheet", type=Path, required=True)
            child.add_argument("--references", type=Path, required=True)
            child.add_argument("--registry", type=Path)
            child.add_argument(
                "--classification",
                choices=("public", "internal", "local-only"),
                default="local-only",
            )
        if name == "plan":
            child.add_argument("--execution", type=Path, required=True)
            child.add_argument("--output", type=Path, required=True)
            scans(child, optional=False)
        if name == "run":
            child.add_argument("--plan", type=Path, required=True)
        if name in {"run", "resume", "collect"}:
            child.add_argument("--once", action="store_true")
        if name == "collect":
            scans(child, optional=True)
            child.add_argument("--retry", action="store_true")
            child.add_argument(
                "--reader",
                action="append",
                default=[],
                help="Worker-qualified bigWig interpreter: WORKER=/absolute/python",
            )
        if name == "export":
            choice = child.add_mutually_exclusive_group(required=True)
            choice.add_argument("--selection", type=Path)
            choice.add_argument("--dataset", action="append")
            child.add_argument("--profile", type=Path)
            child.add_argument("--output", type=Path, required=True)


def export(args: argparse.Namespace, directory: Path) -> dict[str, Any]:
    with contextlib.closing(store.connect(inputs.catalog(directory))) as db:
        db.execute("BEGIN")
        members = inputs.read_study(directory)["data"]["members"]
        if args.selection:
            selected = validate(load(args.selection), "selection")
        else:
            if len(args.dataset) > 1000 or len(set(args.dataset)) != len(args.dataset):
                raise ValueError("Select at most 1,000 distinct dataset IDs")
            data = {
                "registry_id": db.execute(
                    "SELECT value FROM meta WHERE key='registry_id'"
                ).fetchone()[0],
                "candidates": [
                    {"id": key, "manifest_version": store.current(db, key)["manifest"]}
                    for key in args.dataset
                ],
            }
            selected = record("selection", data, identity("selection", fingerprint(data)))
        if any(c["id"] not in members for c in selected["data"]["candidates"]):
            raise ValueError("Export selection includes a dataset outside this study revision")
        result = reviewed_export(db, selected, load(args.profile) if args.profile else profile())
    dump(args.output, result, immutable=True)
    return {
        "output": str(args.output),
        "sha256": fingerprint(result),
        "denominators": result["denominators"],
        "excluded": result["excluded"],
    }


def execute(args: argparse.Namespace) -> tuple[Any, int]:
    directory = args.directory.resolve()
    if args.operation == "init":
        return inputs.initialize(
            directory,
            args.study_id,
            args.assay,
            args.sheet,
            args.references,
            args.registry,
            args.classification,
        ), 0
    if args.operation == "plan":
        scan = {key: getattr(args, key) for key in planning.DEFAULT_SCAN}
        result = planning.plan(directory, args.execution, args.output, scan)
        return result, 3 if result["data"]["status"] == "BLOCKED" else 0
    if args.operation == "status":
        result = workflow.status(directory)
        if (directory / "progress.json").exists():
            result["blockers"] = load(directory / "progress.json")["blockers"]
        return result, 0
    if args.operation == "export":
        return export(args, directory), 0
    scan = None
    readers = {}
    if args.operation == "collect":
        for item in args.reader:
            worker, separator, path = item.partition("=")
            if not separator or worker in readers:
                raise ValueError("Readers use distinct WORKER=/absolute/python entries")
            readers[worker] = path
        changes = {
            key: getattr(args, key)
            for key in planning.DEFAULT_SCAN
            if getattr(args, key) is not None
        }
        if changes:
            scan = {**workflow.options(directory, workflow.active(directory))["scan"], **changes}
            planning.validate_scan(scan)
    result = workflow.run(
        directory,
        load(args.plan) if args.operation == "run" else None,
        once=args.once,
        collect_only=args.operation == "collect",
        scan=scan,
        retry=args.retry if args.operation == "collect" else False,
        readers=readers,
    )
    incomplete = result.get("blockers") or any(
        r["execution"] in {"NEEDS_REVIEW", "UNKNOWN"}
        or r["collection"] in {"FAILED", "UNKNOWN"}
        or r["structural"] == "ERROR"
        for r in result["datasets"]
    )
    return result, 3 if incomplete else 0
