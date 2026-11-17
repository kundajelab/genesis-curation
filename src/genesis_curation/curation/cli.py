"""Study-oriented CLI using the browser's application contract."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..application import Application
from ..contracts.records import load, loads
from ..web import runtime, worker


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, default=os.environ.get("GENESIS_RUNTIME"))
    parser.add_argument("--providers", type=Path)
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument("--output", type=Path, help="Save response or downloaded export")
    parser.add_argument("--wait", action="store_true", help="Wait for submitted jobs")
    parser.set_defaults(curation_handler=execute)
    commands = parser.add_subparsers(dest="operation", required=True)
    raw = commands.add_parser("request", help="Any GUI operation, using its /api path")
    raw.add_argument("method", choices=("GET", "POST"))
    raw.add_argument("path")
    raw.add_argument("--file", type=Path, help="JSON request body")
    commands.add_parser("operations", help="List the complete GUI/CLI operation contract")
    commands.add_parser("summary")
    commands.add_parser("skills")
    commands.add_parser("providers")
    discover = commands.add_parser("discover")
    discover.add_argument("--species", action="append", required=True)
    discover.add_argument("--assay", action="append", required=True)
    discover.add_argument("--goal", default="Find public plant datasets")
    discover.add_argument("--provider")
    discover.add_argument("--constraints", type=Path)
    curate = commands.add_parser("curate")
    curate.add_argument("--study", required=True)
    curate.add_argument("--version", required=True)
    curate.add_argument("--provider")
    intake = commands.add_parser("intake")
    intake.add_argument("accession")
    intake.add_argument("--provider")
    imported = commands.add_parser("import")
    imported.add_argument("file", type=Path)
    imported.add_argument("--namespace", required=True)
    imported.add_argument("--id-column", default="sample_id")
    imported.add_argument(
        "--classification", choices=("public", "internal", "local-only"), default="local-only"
    )
    export = commands.add_parser("export")
    export.add_argument("--clean", action="store_true")
    review = commands.add_parser("review").add_subparsers(dest="action", required=True)
    review.add_parser("list")
    for action in ("show", "history", "draft", "decide", "investigate", "approve"):
        child = review.add_parser(action)
        child.add_argument("id", help="Study ID, or task ID for draft/decide/investigate")
        if action in {"draft", "decide", "investigate"}:
            child.add_argument("--file", type=Path, required=action != "draft")
        if action == "approve":
            child.add_argument("--version", required=True)
    jobs = commands.add_parser("jobs").add_subparsers(dest="action", required=True)
    jobs.add_parser("list")
    for action in ("show", "cancel", "wait"):
        jobs.add_parser(action).add_argument("id")
    qc = commands.add_parser("qc").add_subparsers(dest="action", required=True)
    qc.add_parser("summary")
    qc.add_parser("import").add_argument("file", type=Path)
    qc.add_parser("evaluate").add_argument("file", type=Path, help="QC profile JSON")
    for action in ("rows", "decide", "export"):
        child = qc.add_parser(action)
        child.add_argument("id")
        if action == "decide":
            child.add_argument("--file", type=Path, required=True)


def route(args: argparse.Namespace) -> tuple[str, str, Any]:
    op = args.operation
    key = quote(getattr(args, "id", ""), safe="")
    if op == "request":
        return args.method, args.path, load(args.file) if args.file else None
    if op in {"summary", "providers", "skills"}:
        return "GET", {"summary": "/workspace"}.get(op, "/" + op), None
    if op == "discover":
        return (
            "POST",
            "/workspace/campaigns",
            {
                "mode": "discover",
                "species": args.species,
                "assays": args.assay,
                "goal": args.goal,
                "provider": args.provider,
                "constraints": load(args.constraints) if args.constraints else {},
            },
        )
    if op == "curate":
        return (
            "POST",
            "/studies/" + quote(args.study, safe="") + "/prepare",
            {"version": args.version, "provider": args.provider},
        )
    if op == "intake":
        return "POST", "/studies", {"accession": args.accession, "provider": args.provider}
    if op == "import":
        return (
            "POST",
            "/imports/commit",
            {
                "filename": args.file.name,
                "text": args.file.read_text(),
                "namespace": args.namespace,
                "id_column": args.id_column,
                "classification": args.classification,
            },
        )
    if op == "export":
        return "POST", "/workspace/exports", {"clean": args.clean}
    action = args.action
    if op == "jobs":
        return (
            ("POST", "/jobs/" + key + "/cancel", {})
            if action == "cancel"
            else ("GET", "/jobs" + ("/" + key if key else ""), None)
        )
    if op == "review":
        if action == "list":
            return "GET", "/study-review", None
        if action in {"show", "history"}:
            return "GET", "/studies/" + key + ("/history" if action == "history" else ""), None
        if action == "approve":
            return "POST", "/studies/" + key + "/approve", {"version": args.version}
        if action == "draft" and not args.file:
            return "GET", "/review-tasks/" + key + "/draft", None
        return (
            "POST",
            "/review-tasks/" + key + "/" + {"decide": "decision"}.get(action, action),
            load(args.file),
        )
    if op == "qc":
        if action == "summary":
            return "GET", "/qc", None
        if action == "import":
            return (
                "POST",
                "/qc/imports",
                {"filename": args.file.name, "text": args.file.read_text()},
            )
        if action == "evaluate":
            return "POST", "/qc/analyses", {"profile": load(args.file)}
        return (
            ("GET" if action == "rows" else "POST"),
            "/qc/analyses/" + key + "/" + {"decide": "decisions"}.get(action, action),
            load(args.file) if action == "decide" else {},
        )
    raise ValueError("Unknown curation operation")


def wait(root: Path, config: Path, key: str) -> dict[str, Any]:
    """Own a temporary worker only when no persistent worker holds the lock."""
    with (root / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            owned = True
        except BlockingIOError:
            owned = False
        previous = ""
        while True:
            job = runtime.job(root, key)
            status = json.dumps(
                {"job": key, "status": job["status"], "progress": job.get("progress", {})},
                sort_keys=True,
            )
            if status != previous:
                print(status, file=sys.stderr, flush=True)
                previous = status
            if job["status"] not in {"QUEUED", "RUNNING"}:
                return job
            if owned:
                if job["status"] == "RUNNING":
                    raise ValueError(
                        "Worker was interrupted; inspect jobs before explicit recovery"
                    )
                if not worker.once(root, config):
                    raise ValueError("Worker paused; inspect storage and job state")
            else:
                time.sleep(0.5)


def execute(args: argparse.Namespace) -> tuple[Any, int]:
    if args.root is None:
        raise ValueError("Set --root or GENESIS_RUNTIME")
    root = Path(args.root).resolve()
    config = args.providers or root / "config/providers.json"
    state = root / "state"
    app = Application(state, config)
    if args.operation == "operations":
        from starlette.routing import Route

        from ..web.api import create_app

        web = create_app(state, config, "http://127.0.0.1:18765", dev=True)
        return {
            "operations": [
                {"path": r.path, "methods": sorted(r.methods)}
                for r in web.routes
                if isinstance(r, Route)
                and r.methods
                and r.path.startswith("/api/")
                and r.path not in {"/api/login", "/api/logout", "/api/auth-state", "/api/session"}
            ]
        }, 0
    if args.operation == "jobs" and args.action == "wait":
        value = wait(state, config, args.id)
        return value, 0 if value["status"] == "SUCCEEDED" else 3
    method, path, body = route(args)
    content, media = app.request(method, path, body)
    if "application/json" not in media:
        if not args.output:
            raise ValueError("Use --output to save this download")
        args.output.write_bytes(content)
        return {"file": str(args.output), "bytes": len(content)}, 0
    value = loads(content.decode())
    if args.wait and method == "POST" and isinstance(value, dict):
        key = value.get("job") or value.get("id")
        with runtime.connection(state) as db:
            queued = key and db.execute("SELECT 1 FROM jobs WHERE id=?", (key,)).fetchone()
        if queued:
            job = wait(state, config, key)
            if job["status"] != "SUCCEEDED":
                return job, 3
            value = job
            if args.output and (url := job.get("result", {}).get("url")):
                content, _ = app.request("GET", url)
                args.output.write_bytes(content)
                return {"file": str(args.output), "job": key}, 0
    if args.output:
        args.output.write_text(json.dumps(value, indent=2) + "\n")
    return (value if args.json else json.dumps(value, indent=2)), 0
