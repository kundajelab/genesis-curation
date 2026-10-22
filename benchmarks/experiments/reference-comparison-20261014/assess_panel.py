"""Run bounded reference, annotation and replicate assessments as inputs complete."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from genesis_curation.benchmark.runtime import Runtime
from genesis_curation.benchmark.spec import identifier, identity, sha256, write_json


def specification(job: dict[str, Any]) -> tuple[list[str], list[Path], str]:
    """Construct only the three declared assessment commands, without a shell."""
    folder = Path(__file__).parent
    runs = [Path(job[k]) for k in (("left", "right") if job["kind"] == "reference" else ("run",))]
    prerequisites = [r / n for r in runs for n in ("experiment.json", "results.json")]
    if job["kind"] == "reference":
        script = folder / "compare_references.py"
        arguments = [
            "--left",
            job["left"],
            "--right",
            job["right"],
            "--mapping",
            job["mapping"],
            "--liftover",
            job["liftover"],
        ]
        prerequisites += [Path(job["mapping"]), Path(job["liftover"])]
        result = "comparison.json"
    elif job["kind"] == "annotation":
        script = folder / "compare_annotations.py"
        arguments = [job["run"]]
        for bundle in job["bundles"]:
            arguments += ["--bundle", bundle]
            prerequisites.append(Path(bundle))
        result = "comparison.json"
    elif job["kind"] == "diagnostic":
        script = folder / "caller_diagnostics.py"
        arguments = [
            job["run"],
            "--environment",
            job["environment"],
            "--auxiliary",
            job["auxiliary"],
        ]
        result = "diagnostics.json"
    else:
        raise ValueError("Unsupported assessment kind")
    prerequisites.append(script)
    return [sys.executable, str(script), *arguments], prerequisites, result


def run(manifest: Path, out: Path) -> None:
    if len(os.sched_getaffinity(0)) > 2:
        raise ValueError("Assessments require an explicit two-CPU affinity")
    document = json.loads(manifest.read_text())
    if document["schema_version"] != 1:
        raise ValueError("Unsupported assessment list")
    jobs = document["jobs"]
    for job in jobs:
        identifier(job["id"])
    if len({j["id"] for j in jobs}) != len(jobs):
        raise ValueError("Assessment IDs must be unique")
    out.mkdir(parents=True, exist_ok=True)
    with (out / ".assessment.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = out / "status.json"
        state = (
            json.loads(path.read_text())
            if path.exists()
            else {
                "schema_version": 1,
                "jobs": {},
                "manifest_sha256": sha256(manifest),
                "started_utc": datetime.now(UTC).isoformat(),
            }
        )
        if state["manifest_sha256"] != sha256(manifest):
            raise ValueError("Assessment list changed; use a new output")
        if any(j["status"] == "RUNNING" for j in state["jobs"].values()):
            raise ValueError("Reconcile the previous RUNNING assessment before restarting")
        deadline = datetime.fromisoformat(state["started_utc"]).timestamp() + 7 * 86400
        if document.get("deadline_utc"):
            deadline = min(deadline, datetime.fromisoformat(document["deadline_utc"]).timestamp())
        while time.time() < deadline:
            pending = [j for j in jobs if j["id"] not in state["jobs"]]
            state["pending"] = [j["id"] for j in pending]
            write_json(path, state)
            if not pending:
                return
            for job in pending:
                command, prerequisites, result_name = specification(job)
                if not all(p.is_file() for p in prerequisites):
                    continue
                result_inputs = [p for p in prerequisites if p.name == "results.json"]
                if any((p.parent / ".running").exists() for p in result_inputs):
                    continue
                if any(json.loads(p.read_text())["status"] != "COMPLETE" for p in result_inputs):
                    state["jobs"][job["id"]] = {
                        "status": "BLOCKED",
                        "reason": "Incomplete experiment",
                    }
                    write_json(path, state)
                    continue
                target = out / job["id"]
                if target.exists():
                    raise ValueError("Existing assessment output needs explicit reconciliation")
                hashes = {str(p): sha256(p) for p in prerequisites}
                row = {
                    "status": "RUNNING",
                    "started_utc": datetime.now(UTC).isoformat(),
                    "inputs": hashes,
                }
                state["jobs"][job["id"]] = row
                write_json(path, state)
                limits = {
                    "cpus": 2,
                    "memory_gb": 8,
                    "disk_bytes": 10_000_000_000,
                    "seconds": min(21600, int(deadline - time.time())),
                }
                plan = {
                    "experiment_id": identity({"job": job, "inputs": hashes}),
                    "source": hashes,
                    "spec": {"limits": limits},
                }
                try:
                    runtime = Runtime(plan, target, resume=False)
                    runtime.command(
                        job["id"],
                        command + ["--out", str(target / "result")],
                        target,
                        prerequisites,
                    )
                    if hashes != {str(p): sha256(p) for p in prerequisites}:
                        raise ValueError("Assessment inputs or recipe changed while running")
                    receipt = target / "result" / result_name
                    if json.loads(receipt.read_text())["status"] != "COMPLETE":
                        raise ValueError("Assessment did not certify completion")
                    row.update(
                        status="COMPLETE", result=str(receipt), result_sha256=sha256(receipt)
                    )
                except Exception as error:
                    row.update(status="FAILED", error=str(error))
                row["ended_utc"] = datetime.now(UTC).isoformat()
                write_json(path, state)
                print(job["id"], row["status"], flush=True)
                break
            else:
                time.sleep(30)
        raise TimeoutError("Seven-day assessment limit reached")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.manifest.resolve(), args.out.resolve())
