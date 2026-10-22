"""Run independent datasets locally or on existing SSH workers."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import html
import http.server
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..atac.inputs import identifier


def connect(folder: Path) -> sqlite3.Connection:
    folder.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(folder / "state.sqlite", timeout=30)
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, worker TEXT, payload TEXT, "
        "state TEXT, attempt INT, reason TEXT)"
    )
    db.execute("CREATE TABLE IF NOT EXISTS events (time REAL, job TEXT, event TEXT, note TEXT)")
    return db


def event(db: sqlite3.Connection, job: str, name: str, note: str = "") -> None:
    db.execute("INSERT INTO events VALUES (?,?,?,?)", (time.time(), job, name, note))


def initialize(folder: Path, manifest: Path) -> None:
    plan = json.loads(manifest.read_text())
    if plan.get("schema_version") != 1 or not plan.get("jobs"):
        raise ValueError("Expected a version 1 campaign with jobs")
    workers = plan["workers"]
    for name, worker in workers.items():
        identifier(name)
        if worker["transport"] not in {"local", "ssh"} or not 1 <= worker["slots"] <= 128:
            raise ValueError("Invalid worker transport or capacity")
        for key in ("repo", "python", "root"):
            if not Path(worker[key]).is_absolute() or any(c in worker[key] for c in "\n\r\x00"):
                raise ValueError("Worker paths must be absolute")
        if worker["transport"] == "ssh" and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.@-]*", worker["host"]
        ):
            raise ValueError("Use an existing SSH host alias without options")
    seen = set()
    for job in plan["jobs"]:
        identifier(job["id"])
        if job["id"] in seen or job["worker"] not in workers:
            raise ValueError("Duplicate job or unknown worker")
        seen.add(job["id"])
        payload = job["payload"]
        if not re.fullmatch("[a-f0-9]{40}", payload["git_sha"]):
            raise ValueError("A full Git SHA is required")
        if not payload["argv"] or not all(
            isinstance(a, str) and "\x00" not in a for a in payload["argv"]
        ):
            raise ValueError("argv must be a nonempty list of arguments")
        if not payload["inputs"]:
            raise ValueError("Each job needs checksummed input files")
        for item in payload["inputs"]:
            if not Path(item["path"]).is_absolute() or not re.fullmatch(
                "[a-f0-9]{64}", item["sha256"]
            ):
                raise ValueError("Input paths must be absolute and checksummed")
    folder.mkdir(parents=True, exist_ok=True)
    text = json.dumps(plan, sort_keys=True, indent=2) + "\n"
    target = folder / "campaign.json"
    if target.exists() and target.read_text() != text:
        raise ValueError("Campaign changed: use a new campaign directory")
    target.write_text(text)
    with contextlib.closing(connect(folder)) as db, db:
        for job in plan["jobs"]:
            db.execute(
                "INSERT OR IGNORE INTO jobs VALUES (?,?,?,'QUEUED',0,'')",
                (job["id"], job["worker"], json.dumps(job["payload"], sort_keys=True)),
            )


def request(
    worker: dict[str, Any], action: str, attempt: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    from .transport import request as worker_request

    return worker_request(
        worker, "genesis_curation.execution.worker", action, Path(worker["root"]) / attempt, payload
    )


def tick(folder: Path, *, allowed_jobs: set[str] | None = None) -> None:
    plan = json.loads((folder / "campaign.json").read_text())
    campaign = hashlib.sha256((folder / "campaign.json").read_bytes()).hexdigest()[:16]
    with contextlib.closing(connect(folder)) as db, db:
        # Reserve transactions before external actions; only one controller may tick at once.
        db.execute("BEGIN IMMEDIATE")
        jobs = db.execute("SELECT * FROM jobs ORDER BY id").fetchall()
        for row in jobs:
            if row["state"] not in {"RUNNING", "UNKNOWN"}:
                continue
            worker = plan["workers"][row["worker"]]
            attempt = f"{campaign}-{row['id']}-{row['attempt']}"
            try:
                status = request(worker, "status", attempt)
                state = status["state"]
                if state == "SUCCEEDED":
                    state, reason = "SUCCEEDED", ""
                elif state == "FAILED":
                    state, reason = "NEEDS_REVIEW", status.get("reason", "worker failed")
                    # Exit 75 explicitly requests a retry; OOM and SSH errors do not.
                    if status.get("exit_code") == 75 and row["attempt"] < 3:
                        state, reason = "QUEUED", "temporary failure; bounded retry"
                elif (
                    state in {"STARTING", "RUNNING"}
                    and time.time() - status.get("updated", 0) < 120
                ):
                    state, reason = "RUNNING", ""
                else:
                    state, reason = (
                        "UNKNOWN",
                        "Worker state is stale or absent; investigate before resubmitting",
                    )
            except ConnectionError, subprocess.TimeoutExpired, OSError, ValueError:
                state, reason = (
                    "UNKNOWN",
                    "Cannot reach worker; authentication or connectivity needs review",
                )
            db.execute("UPDATE jobs SET state=?,reason=? WHERE id=?", (state, reason, row["id"]))
            if state != row["state"]:
                event(db, row["id"], state, reason)
        for name, worker in plan["workers"].items():
            occupied = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE worker=? AND state IN ('RUNNING','UNKNOWN')",
                (name,),
            ).fetchone()[0]
            queued = db.execute(
                "SELECT * FROM jobs WHERE worker=? AND state='QUEUED' ORDER BY id LIMIT ?",
                (name, max(0, worker["slots"] - occupied)),
            ).fetchall()
            for row in queued:
                if allowed_jobs is not None and row["id"] not in allowed_jobs:
                    continue
                attempt = row["attempt"] + 1
                payload = {
                    **json.loads(row["payload"]),
                    "repo": worker["repo"],
                    "python": worker["python"],
                }
                # Reserve before launching. UNKNOWN jobs are never resubmitted.
                db.execute(
                    "UPDATE jobs SET state='UNKNOWN',attempt=?,reason='launch reserved' WHERE id=?",
                    (attempt, row["id"]),
                )
                event(db, row["id"], "LAUNCH_RESERVED")
                db.commit()
                try:
                    request(worker, "launch", f"{campaign}-{row['id']}-{attempt}", payload)
                    db.execute("UPDATE jobs SET state='RUNNING',reason='' WHERE id=?", (row["id"],))
                except ConnectionError, subprocess.TimeoutExpired, OSError, ValueError:
                    event(db, row["id"], "UNKNOWN", "Launch result unavailable; do not resubmit")
                db.commit()


def rows(folder: Path) -> list[dict[str, Any]]:
    with contextlib.closing(connect(folder)) as db:
        return [
            dict(row)
            for row in db.execute("SELECT id,worker,state,attempt,reason FROM jobs ORDER BY id")
        ]


def resolve(folder: Path, job: str, note: str) -> None:
    if not note.strip():
        raise ValueError("A resolution note is required")
    with contextlib.closing(connect(folder)) as db, db:
        row = db.execute("SELECT state FROM jobs WHERE id=?", (job,)).fetchone()
        if not row or row["state"] != "NEEDS_REVIEW":
            raise ValueError(
                "Only confirmed failed jobs can be retried; reconcile UNKNOWN workers first"
            )
        db.execute("UPDATE jobs SET state='QUEUED',reason='' WHERE id=?", (job,))
        event(db, job, "HUMAN_RETRY", note)


def reconcile(folder: Path, job: str, note: str, confirmed_stopped: bool) -> None:
    """Record a human's verification after a lost worker supervisor."""
    if not confirmed_stopped or not note.strip():
        raise ValueError(
            "Verify that the old process and its scheduler jobs stopped, then record why"
        )
    plan = json.loads((folder / "campaign.json").read_text())
    campaign = hashlib.sha256((folder / "campaign.json").read_bytes()).hexdigest()[:16]
    with contextlib.closing(connect(folder)) as db, db:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
        if not row or row["state"] != "UNKNOWN":
            raise ValueError("Only UNKNOWN jobs need reconciliation")
        status = request(
            plan["workers"][row["worker"]], "status", f"{campaign}-{job}-{row['attempt']}"
        )
        if (
            status["state"] in {"STARTING", "RUNNING"}
            and time.time() - status.get("updated", 0) < 120
        ):
            raise ValueError("Worker still reports an active process; investigate on that worker")
        if status["state"] == "SUCCEEDED":
            state = "SUCCEEDED"
        else:
            state = "NEEDS_REVIEW"
        db.execute("UPDATE jobs SET state=?,reason=? WHERE id=?", (state, note, job))
        event(db, job, "HUMAN_RECONCILIATION", note)


def serve(folder: Path, port: int, registry: Path | None = None) -> None:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if registry and self.path.startswith("/reports/"):
                from ..curation.status_page import report

                try:
                    body = report(registry, self.path)
                except ValueError, OSError, sqlite3.Error:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header(
                    "Content-Disposition", 'attachment; filename="approved-report.html"'
                )
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path != "/":
                self.send_error(404)
                return
            content = (
                "<!doctype html>"
                "<html>"
                '<meta charset="utf-8">'
                '<meta http-equiv="refresh" content="15">'
                "<title>Genesis status</title>"
                "<style>body{font:16px system-ui;margin:3em;color:#17324d}"
                "table{border-collapse:collapse}"
                "td,th{padding:12px;border-bottom:1px solid #ddd;text-align:left}</style>"
                "<h1>Genesis runs</h1>"
                "<p>Read-only status. Resolve issues with the command line.</p>"
                "<table>"
                "<tr>"
                "<th>Dataset</th>"
                "<th>Worker</th>"
                "<th>Status</th>"
                "<th>Attempt</th>"
                "<th>Details</th>"
                "</tr>"
            )
            for row in rows(folder):
                content += (
                    "<tr>"
                    + "".join(
                        f"<td>{html.escape(str(row[key]))}</td>"
                        for key in ("id", "worker", "state", "attempt", "reason")
                    )
                    + "</tr>"
                )
            content += "</table>"
            if registry:
                from ..curation.status_page import summary

                try:
                    content += summary(registry, folder)
                except ValueError, OSError, sqlite3.Error:
                    content += (
                        "<p>Curation registry unavailable; execution status is independent.</p>"
                    )
            body = (content + "</html>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main() -> None:
    from . import launch

    parser = argparse.ArgumentParser(
        prog="genesis",
        description="Plant sequencing, study execution and evidence-backed curation.",
    )
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("doctor")
    launch.configure(commands)
    from ..curation import commands as curation_commands

    curation_commands.configure(commands)
    for name in ("run", "resume", "status", "issues", "resolve", "reconcile", "serve"):
        child = commands.add_parser(name)
        child.add_argument("directory", type=Path)
        if name == "run":
            child.add_argument("--manifest", type=Path, required=True)
        if name in {"run", "resume"}:
            child.add_argument("--once", action="store_true")
        if name in {"resolve", "reconcile"}:
            child.add_argument("job")
            child.add_argument("--note", required=True)
        if name == "reconcile":
            child.add_argument("--confirmed-stopped", action="store_true")
        if name == "serve":
            child.add_argument("--port", type=int, default=8765)
            child.add_argument("--registry", type=Path)
    argv = sys.argv[1:]
    # A separator keeps delegated options opaque to this parser, including leading
    # -p/--profile and --help. The existing launchers retain ownership of their CLI.
    if argv and argv[0] in launch.COMMANDS:
        argv = [argv[0], "--", *argv[1:]]
    args = parser.parse_args(argv)
    if args.action in launch.COMMANDS:
        try:
            launch.execute(args.action, args.arguments[1:])
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            parser.exit(2, f"Cannot start {args.action}: {error}\n")
        return
    if hasattr(args, "curation_handler"):
        curation_commands.execute(args)
        return
    if args.action == "doctor":
        print(
            json.dumps(
                {
                    "runtime": launch.inspect_runtime(),
                    **{
                        name: shutil.which(name)
                        for name in (
                            "nextflow",
                            "docker",
                            "apptainer",
                            "podman-hpc",
                            "sbatch",
                            "qsub",
                            "bsub",
                            "ssh",
                        )
                    },
                },
                indent=2,
            )
        )
        try:
            result = (
                subprocess.run(
                    ["docker", "info", "--format", "{{.ServerVersion}}"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                if shutil.which("docker")
                else None
            )
        except (OSError, subprocess.SubprocessError) as error:
            result = None
            print(f"Docker check unavailable: {error}")
        print("Docker accessible:", bool(result and result.returncode == 0))
        return
    folder = args.directory.resolve()
    if args.action == "run":
        initialize(folder, args.manifest)
    if args.action in {"run", "resume"}:
        import fcntl

        with (folder / "controller.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            while True:
                tick(folder)
                state = rows(folder)
                print(json.dumps(state), flush=True)
                if args.once or not any(r["state"] in {"QUEUED", "RUNNING"} for r in state):
                    break
                time.sleep(15)
    elif args.action in {"status", "issues"}:
        print(
            json.dumps(
                [
                    r
                    for r in rows(folder)
                    if args.action == "status" or r["state"] in {"NEEDS_REVIEW", "UNKNOWN"}
                ],
                indent=2,
            )
        )
    elif args.action == "resolve":
        resolve(folder, args.job, args.note)
    elif args.action == "reconcile":
        reconcile(folder, args.job, args.note, args.confirmed_stopped)
    elif args.action == "serve":
        serve(folder, args.port, args.registry)


if __name__ == "__main__":
    main()
