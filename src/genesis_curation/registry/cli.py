"""Local catalog command handlers, delegated from the existing Genesis CLI."""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
from pathlib import Path
from typing import Any

from ..contracts.records import dump, fingerprint, identity, load, record, validate
from ..curation.review import profile, reviewed_export, status
from . import store


def configure(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("init", "import", "search", "show", "history", "export", "backup", "restore"):
        child = commands.add_parser(name)
        child.add_argument("directory", type=Path)
        child.add_argument("--json", action="store_true")
        child.set_defaults(curation_handler=execute)
        if name == "import":
            group = child.add_mutually_exclusive_group(required=True)
            group.add_argument("--bundle", type=Path)
            group.add_argument("--campaign", type=Path)
            group.add_argument(
                "--record", type=Path, help="Import invocation/deployment provenance"
            )
        if name in {"show", "history"}:
            child.add_argument("dataset")
        if name == "show":
            child.add_argument(
                "--kind", help="Read an immutable record: interpret DATASET as its version hash"
            )
        if name in {"search", "history"}:
            child.add_argument("--limit", type=int, default=100)
            child.add_argument(
                "--after",
                default="" if name == "search" else 0,
                type=str if name == "search" else int,
            )
        if name == "search":
            child.add_argument("--filter", action="append", default=[])
            child.add_argument("--text")
            child.add_argument("--qc-status")
            child.add_argument("--review-status")
            child.add_argument(
                "--category", choices=("metadata", "qc", "eligibility"), default="metadata"
            )
            child.add_argument("--selection-output", type=Path)
            child.add_argument("--format", choices=("json", "tsv"), default="json")
        if name in {"export", "backup"}:
            child.add_argument("--output", required=True, type=Path)
        if name == "export":
            child.add_argument("--selection", required=True, type=Path)
            child.add_argument("--reviewed", action="store_true")
            child.add_argument("--profile", type=Path)
        if name == "restore":
            child.add_argument("--backup", required=True, type=Path)


def execute(args: argparse.Namespace) -> tuple[Any, int]:
    directory = args.directory.resolve()
    if args.operation == "init":
        return store.initialize(directory), 0
    if args.operation == "import":
        if args.campaign:
            return store.import_campaign(directory, args.campaign), 0
        if args.record:
            value = validate(load(args.record))
            if value["kind"] not in {"invocation", "deployment"}:
                raise ValueError("Use bundle import or review commands for scientific records")
            with store.write(directory) as db:
                store.put(db, value)
            return {"imported": value["version"]}, 0
        value = load(args.bundle)
        if (
            value.get("export_kind") == "reviewed-manifest-catalog"
            and value.get("schema_version") == 1
        ):
            value = value[
                "bundle"
            ]  # Decisions require restore, not an import that overwrites history.
        return store.import_bundle(directory, value), 0
    if args.operation == "backup":
        store.backup(directory, args.output)
        return {"backup": str(args.output)}, 0
    if args.operation == "restore":
        store.restore(args.backup, directory)
        return {"restored": str(directory)}, 0
    with contextlib.closing(store.connect(directory)) as db:
        db.execute("BEGIN")
        if args.operation == "search":
            filters = {}
            for item in args.filter:
                if "=" not in item:
                    raise ValueError("Filters use FIELD=VALUE")
                key, value = item.split("=", 1)
                filters[key] = value
            rows = store.search(
                db, filters=filters, text=args.text, after=args.after, limit=args.limit
            )
            candidates = [{**row, "status": status(db, row["id"])} for row in rows]
            selected = [
                row
                for row in candidates
                if (not args.qc_status or row["status"]["qc"] == args.qc_status)
                and (
                    not args.review_status
                    or row["status"]["reviews"][args.category] == args.review_status
                )
            ]
            if args.selection_output:
                data = {
                    "registry_id": db.execute(
                        "SELECT value FROM meta WHERE key='registry_id'"
                    ).fetchone()[0],
                    "candidates": [
                        {"id": row["id"], "manifest_version": row["manifest"]} for row in selected
                    ],
                }
                dump(
                    args.selection_output,
                    record("selection", data, identity("selection", fingerprint(data))),
                    immutable=True,
                )
            if args.format == "tsv":
                stream = io.StringIO()
                writer = csv.DictWriter(
                    stream,
                    fieldnames=["id", "name", "assay", "species", "study", "worker"],
                    delimiter="\t",
                    extrasaction="ignore",
                )
                writer.writeheader()
                writer.writerows(selected)
                return stream.getvalue(), 0
            return {
                "results": selected,
                "next_after": rows[-1]["id"] if len(rows) == args.limit else None,
                "scanned": len(rows),
            }, 0
        if args.operation == "history":
            return store.history(db, args.dataset, limit=args.limit, after=args.after), 0
        if args.operation == "show":
            if args.kind:
                return store.get(db, args.kind, args.dataset), 0
            row = store.current(db, args.dataset)
            return {
                "dataset": row,
                "status": status(db, args.dataset),
                "manifest": store.get(db, "manifest", row["manifest"]),
            }, 0
        if args.operation == "export":
            selection = validate(load(args.selection), "selection")
            if (
                selection["data"]["registry_id"]
                != db.execute("SELECT value FROM meta WHERE key='registry_id'").fetchone()[0]
            ):
                raise ValueError("Selection belongs to another registry")
            if args.reviewed or args.profile:
                result = reviewed_export(
                    db, selection, load(args.profile) if args.profile else profile()
                )
            else:
                for candidate in selection["data"]["candidates"]:
                    if (
                        store.current(db, candidate["id"])["manifest"]
                        != candidate["manifest_version"]
                    ):
                        raise ValueError("Stale export selection")
                result = store.export_bundle(db, [c["id"] for c in selection["data"]["candidates"]])
            dump(args.output, result, immutable=True)
            return {"output": str(args.output), "sha256": fingerprint(result)}, 0
    raise ValueError("Unknown registry operation")
