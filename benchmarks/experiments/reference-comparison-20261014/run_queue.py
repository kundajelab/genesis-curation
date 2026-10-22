"""Run an explicit local experiment list sequentially with a frozen runner checkout."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from genesis_curation.benchmark.spec import sha256, write_json


def process_table() -> dict[int, tuple[int, str, str]]:
    """Map each visible pid to (parent pid, start-time identity, state).

    The start time guards against signalling a recycled pid. Linux reads /proc; other systems
    (e.g. macOS) have no /proc, so they use ps, whose lstart is the start time to the second.
    """
    table: dict[int, tuple[int, str, str]] = {}
    proc = Path("/proc")
    if proc.is_dir():
        for entry in proc.iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                table[int(entry.name)] = (int(fields[1]), fields[19], fields[0])
            except OSError, IndexError, ValueError:
                continue
        return table
    listing = subprocess.run(
        ["ps", "-A", "-o", "pid=,ppid=,stat=,lstart="], capture_output=True, text=True, check=True
    ).stdout
    for line in listing.splitlines():
        fields = line.split(None, 3)
        if len(fields) == 4 and fields[0].isdecimal() and fields[1].isdecimal():
            table[int(fields[0])] = (int(fields[1]), fields[3].strip(), fields[2])
    return table


def stop_owned_tree(process: subprocess.Popen) -> None:
    """Stop a runner and its tools, including descendants in separate sessions."""
    snapshot = process_table()
    descendants = {process.pid}
    while additional := {
        pid
        for pid, (parent, _, _) in snapshot.items()
        if parent in descendants and pid not in descendants
    }:
        descendants.update(additional)
    owned = {pid: snapshot[pid] for pid in descendants if pid in snapshot}

    def signal_owned(sig: int) -> bool:
        alive = False
        current = process_table()
        # Signal children before their parent can disappear and orphan them.
        for pid in sorted(owned, key=lambda p: p == process.pid):
            entry = current.get(pid)
            if entry is None or entry[1] != owned[pid][1] or entry[2].startswith("Z"):
                continue
            alive = True
            if sig:
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass
        return alive

    signal_owned(signal.SIGTERM)
    deadline = time.monotonic() + 10
    while signal_owned(0) and time.monotonic() < deadline:
        time.sleep(0.1)
    signal_owned(signal.SIGKILL)
    process.wait(timeout=10)


def disk_bytes(roots: list[Path]) -> int:
    unique = sorted({p.resolve() for p in roots})
    selected = [
        p for p in unique if not any(p != other and p.is_relative_to(other) for other in unique)
    ]
    return sum(
        p.stat().st_size
        for root in selected
        if root.exists()
        for p in root.rglob("*")
        if p.is_file() and not p.is_symlink()
    )


def require_free_space(destination: Path, reserve_bytes: int) -> None:
    """Account for other files on the filesystem, including acquired inputs."""
    parent = destination.parent
    while not parent.exists():
        parent = parent.parent
    free = shutil.disk_usage(parent).free
    if free < reserve_bytes:
        raise ValueError(
            f"Insufficient filesystem space: {free} bytes free; {reserve_bytes} required"
        )


def run(
    queue: Path,
    checkout: Path,
    python: Path,
    work: Path,
    wait_for: Path | None,
    scratch: list[Path],
) -> None:
    work.mkdir(parents=True, exist_ok=True)
    with (work / ".experiment-queue.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        subprocess.run([str(python), "-c", "import pysam"], check=True, cwd=checkout)
        started = time.monotonic()
        started_utc = datetime.now(UTC).isoformat()
        source = checkout / "src"
        frozen = {
            str(p.relative_to(source)): sha256(p)
            for p in (source / "genesis_curation/benchmark").glob("*.py")
        }
        frozen["genesis_curation/atac/fragments.py"] = sha256(
            source / "genesis_curation/atac/fragments.py"
        )
        state_path = work / "queue-progress.json"
        state: dict[str, Any] = (
            json.loads(state_path.read_text())
            if state_path.exists()
            else {"schema_version": 1, "jobs": {}}
        )
        if any(row["status"] == "RUNNING" for row in state["jobs"].values()):
            raise ValueError("Reconcile the previous RUNNING process before restarting this queue")
        if state.get("frozen_runner", frozen) != frozen:
            raise ValueError("Queue runner identity changed")
        state.setdefault("started_utc", started_utc)
        deadline = datetime.fromisoformat(state["started_utc"]).timestamp() + 7 * 86400
        state["frozen_runner"] = frozen
        write_json(state_path, state)
        if wait_for:
            while not (wait_for / "results.json").exists() or (wait_for / ".running").exists():
                if time.monotonic() - started > 21600 or time.time() >= deadline:
                    raise TimeoutError("Initial experiment wait exceeded its execution budget")
                time.sleep(5)
        while True:
            if time.time() >= deadline:
                raise TimeoutError("Seven-day experiment queue limit reached")
            jobs = json.loads(queue.read_text())
            ready = [
                j for j in jobs if j["id"] not in state["jobs"] and Path(j["experiment"]).is_file()
            ]
            if not ready:
                pending = [j["id"] for j in jobs if j["id"] not in state["jobs"]]
                state["pending"] = pending
                write_json(state_path, state)
                if not pending:
                    return
                time.sleep(10)
                continue
            job = ready[0]
            if any(sha256(source / name) != digest for name, digest in frozen.items()):
                raise ValueError("Frozen runner source changed")
            experiment, destination = Path(job["experiment"]), Path(job["results"])
            spec = json.loads(experiment.read_text())
            if spec["limits"]["cpus"] > 14 or spec["limits"]["memory_gb"] > 80:
                raise ValueError("An experiment exceeds the reserved 14 CPUs / 80 GiB")
            available = (
                int(
                    next(
                        line.split()[1]
                        for line in Path("/proc/meminfo").read_text().splitlines()
                        if line.startswith("MemAvailable:")
                    )
                )
                * 1024
            )
            if available < 96 * 1024**3:
                raise ValueError(
                    "Insufficient available memory for benchmark plus preparation reserve"
                )
            if destination.exists():
                raise ValueError("Queue will not overwrite or implicitly resume existing results")
            require_free_space(destination, spec["limits"]["disk_bytes"])
            roots = scratch + [Path(j["results"]) for j in jobs]
            current_bytes = disk_bytes(roots)
            if current_bytes + spec["limits"]["disk_bytes"] > 150_000_000_000:
                raise ValueError(
                    "Insufficient aggregate scratch budget; archive completed indexes first"
                )
            command = [
                str(python),
                "-m",
                "genesis_curation.benchmark",
                "run",
                str(experiment),
                "--out",
                str(destination),
            ]
            row = {
                "status": "RUNNING",
                "experiment_sha256": sha256(experiment),
                "started_utc": datetime.now(UTC).isoformat(),
            }
            state["jobs"][job["id"]] = row
            write_json(state_path, state)
            print(job["id"], "RUNNING", flush=True)
            with (work / (job["id"] + ".log")).open("x") as log:
                process = subprocess.Popen(
                    command,
                    cwd=checkout,
                    env=dict(os.environ, PYTHONPATH=str(source)),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                budget_error = None
                try:
                    while process.poll() is None:
                        used = disk_bytes(roots)
                        state["sampled_scratch_peak_bytes"] = max(
                            used, state.get("sampled_scratch_peak_bytes", 0)
                        )
                        if used > 150_000_000_000 or time.time() >= deadline:
                            budget_error = "Scratch or seven-day execution budget exceeded"
                            stop_owned_tree(process)
                            break
                        if shutil.disk_usage(destination).free < 1_000_000_000:
                            budget_error = "Filesystem space fell below the 1 GB safety reserve"
                            stop_owned_tree(process)
                            break
                        write_json(state_path, state)
                        time.sleep(10)
                finally:
                    if process.poll() is None:
                        stop_owned_tree(process)
                    else:
                        process.wait()
            result_path = destination / "results.json"
            result = json.loads(result_path.read_text()) if result_path.exists() else {}
            row.update(
                status=result.get("status", "FAILED"),
                exit_code=process.returncode,
                ended_utc=datetime.now(UTC).isoformat(),
            )
            if budget_error:
                row.update(status="FAILED", reason=budget_error)
            write_json(state_path, state)
            print(job["id"], row["status"], flush=True)
            if budget_error:
                raise RuntimeError(budget_error)
            if row["status"] == "COMPLETE" and (
                job.get("archive_indexes") or job.get("archive_work")
            ):
                archive = Path(__file__).parent.parent / "plant-methods-20261013/archive.py"
                subprocess.run(
                    [
                        str(python),
                        str(archive.resolve()),
                        str(destination),
                        "--out",
                        str(work / (job["id"] + "-evidence")),
                        "--prune-work" if job.get("archive_work") else "--prune-indexes",
                    ],
                    check=True,
                    cwd=checkout,
                    env=dict(os.environ, PYTHONPATH=str(source)),
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("queue", type=Path)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--wait-for", type=Path)
    parser.add_argument(
        "--scratch",
        type=Path,
        action="append",
        default=[],
        help="Additional temporary roots, including samples; original acquired inputs are separate",
    )
    args = parser.parse_args()
    if os.sched_getaffinity(0) != set(range(14)):
        parser.error("Run this experiment recipe under taskset -c 0-13")
    # Resolving a virtualenv interpreter symlink changes Python's environment.
    run(
        args.queue.resolve(),
        args.checkout.resolve(),
        args.python.absolute(),
        args.work.resolve(),
        args.wait_for,
        args.scratch,
    )
