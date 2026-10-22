"""Explicit validation commands; no implicit pipeline hooks."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .adapters import run_manifests
from .records import dump, load, record, validate
from .validation import Budget, bundle


def scan_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--level", choices=("metadata", "full"), default="metadata")
    parser.add_argument("--max-bytes", type=int, default=1024 * 1024 * 1024)
    parser.add_argument("--max-records", type=int, default=1_000_000)
    parser.add_argument("--max-seconds", type=float, default=300)
    parser.add_argument("--bigwig-python", type=Path)


def run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--assay", choices=("DAP-seq", "bulk-ATAC"), required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--worker", required=True)
    parser.add_argument("--study")
    parser.add_argument("--sheet", type=Path)
    parser.add_argument("--references", type=Path)
    parser.add_argument("--campaign-id")
    parser.add_argument("--job-id")
    parser.add_argument("--attempt", type=int)


def checked_run(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    manifests = run_manifests(
        root, args.assay, args.source_id, args.worker, args.study, args.sheet, args.references
    )
    linkage = (args.campaign_id, args.job_id, args.attempt)
    if any(x is not None for x in linkage):
        if any(x is None for x in linkage) or args.attempt < 1:
            raise ValueError("Supply campaign ID, job ID and positive attempt together")
        for manifest in manifests:
            manifest["data"]["run"].update(
                campaign_id=args.campaign_id, job_id=args.job_id, attempt=args.attempt
            )
        manifests = [record("manifest", m["data"], m["id"]) for m in manifests]
    return bundle(
        manifests,
        level=args.level,
        budget=Budget(args.max_bytes, args.max_records, args.max_seconds),
        bigwig_python=args.bigwig_python,
        worker_identity=args.worker,
    )


def configure(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("inputs", "run", "manifest"):
        child = commands.add_parser(name)
        child.add_argument("path", type=Path)
        child.add_argument("--json", action="store_true")
        child.set_defaults(curation_handler=execute)
        if name == "inputs":
            child.add_argument("--assay", choices=("DAP-seq", "bulk-ATAC"), required=True)
            child.add_argument("--references", type=Path, required=True)
        else:
            child.add_argument("--output", type=Path)
            scan_options(child)
            if name == "run":
                run_options(child)
            else:
                child.add_argument("--worker", default="local")
                child.add_argument(
                    "--scan", action="store_true", help="Also inspect referenced local artifacts"
                )


def execute(args: argparse.Namespace) -> tuple[Any, int]:
    if args.operation == "inputs":
        if args.assay == "bulk-ATAC":
            from ..atac.inputs import libraries, references

            rows = libraries(args.path, references(args.references))
        else:
            from ..samples import load_samples

            rows = load_samples(args.path, args.references)
        return {
            "schema_version": 1,
            "result": "PASS",
            "scope": "legacy-input-validation",
            "records": len(rows),
        }, 0
    if args.operation == "run":
        result = checked_run(args, args.path)
    else:
        source = validate(load(args.path))
        if not args.scan:
            return {
                "result": "PASS",
                "scope": "record-contract-only",
                "kind": source["kind"],
                "version": source["version"],
            }, 0
        manifests = (
            [source]
            if source["kind"] == "manifest"
            else validate(source, "bundle")["data"]["manifests"]
        )
        result = bundle(
            manifests,
            level=args.level,
            budget=Budget(args.max_bytes, args.max_records, args.max_seconds),
            bigwig_python=args.bigwig_python,
            worker_identity=args.worker,
        )
    if args.output:
        dump(args.output, result, immutable=True)
    assessments = result["data"]["validations"]
    error = any(f["data"]["result"] == "ERROR" for v in assessments for f in v["data"]["findings"])
    incomplete = any(not v["data"]["complete"] for v in assessments)
    return result, 1 if error else (3 if args.level == "full" and incomplete else 0)
