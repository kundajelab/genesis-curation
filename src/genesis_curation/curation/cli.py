"""Explicit individual review; there is no approve-all operation."""

from __future__ import annotations

import argparse
import contextlib
from pathlib import Path
from typing import Any

from ..contracts.records import load, loads
from ..registry import store
from . import review


def configure(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in (
        "propose",
        "queue",
        "show",
        "diff",
        "approve",
        "reject",
        "request-info",
        "history",
    ):
        child = commands.add_parser(name)
        child.add_argument("directory", type=Path)
        child.add_argument("--json", action="store_true")
        child.add_argument("--scope", choices=("inputs", "results"), default="results")
        child.set_defaults(curation_handler=execute)
        if name == "propose":
            child.add_argument("--file", type=Path, required=True)
        elif name == "queue":
            child.add_argument("--category", choices=review.CATEGORIES, default="metadata")
            child.add_argument("--limit", type=int, default=100)
            child.add_argument("--after", default="")
        else:
            child.add_argument("dataset")
        if name == "diff":
            child.add_argument("--target", required=True)
        if name in {"approve", "reject", "request-info"}:
            child.add_argument("--category", required=True, choices=review.CATEGORIES)
            child.add_argument("--token", required=True)
            child.add_argument("--reason", required=True)
            child.add_argument("--evidence", type=Path)
            child.add_argument("--target")
            child.add_argument("--profile", type=Path)
        if name == "history":
            child.add_argument("--limit", type=int, default=100)
            child.add_argument("--after", type=int, default=0)


def execute(args: argparse.Namespace) -> tuple[Any, int]:
    if args.operation == "propose":
        return review.submit(args.directory, load(args.file), scope=args.scope), 0
    if args.operation in {"approve", "reject", "request-info"}:
        return review.decide(
            args.directory,
            args.dataset,
            args.category,
            args.operation,
            args.token,
            args.reason,
            load(args.evidence)["evidence"] if args.evidence else [],
            args.target,
            load(args.profile) if args.profile else None,
            scope=args.scope,
        ), 0
    with contextlib.closing(store.connect(args.directory, scope=args.scope)) as db:
        db.execute("BEGIN")
        if args.operation == "queue":
            rows = store.search(db, after=args.after, limit=args.limit)
            next_after = rows[-1]["id"] if len(rows) == args.limit else None
            if args.scope == "inputs":
                rows = [
                    r
                    for r in rows
                    if db.execute(
                        "SELECT 1 FROM input_heads WHERE dataset=?", (r["id"],)
                    ).fetchone()
                ]
            return {
                "results": [
                    review.status(db, row["id"])
                    for row in rows
                    if review.effective(db, row["id"], args.category) != "APPROVED"
                ],
                "next_after": next_after,
            }, 0
        if args.operation == "history":
            if not 1 <= args.limit <= 1000 or args.after < 0:
                raise ValueError("Invalid pagination")
            rows = db.execute(
                (
                    f"SELECT sequence,body FROM {store.decisions_table(db)} "
                    "WHERE dataset=? AND sequence>? ORDER "
                    "BY sequence LIMIT ?"
                ),
                (args.dataset, args.after, args.limit),
            ).fetchall()
            return {
                "results": [{"sequence": row[0], "decision": loads(row[1])} for row in rows],
                "next_after": rows[-1][0] if len(rows) == args.limit else None,
            }, 0
        if args.operation == "diff":
            row = store.current(db, args.dataset)
            proposed = store.get(db, "proposal", args.target)
            proposal = proposed["data"]
            if proposal["dataset_id"] != args.dataset:
                raise ValueError("Proposal belongs to another dataset")
            current = loads(row["metadata"])
            stale = proposal["manifest_version"] != row["manifest"]
            if proposed["schema_version"] == 2:
                from .harmonize import check_proposal

                try:
                    check_proposal(db, proposed)
                except ValueError:
                    stale = True
            return {
                "changes": {
                    key: {"before": current.get(key), "after": value}
                    for key, value in proposal["changes"].items()
                },
                "stale": stale,
                "token": review.token(db, args.dataset),
            }, 0
        return {
            "status": review.status(db, args.dataset),
            "current": store.current(db, args.dataset),
        }, 0
