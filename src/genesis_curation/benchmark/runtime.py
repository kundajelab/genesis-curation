"""Bounded execution, complete logs, verified caching and explicit public downloads."""

from __future__ import annotations

import datetime
import functools
import json
import os
import shutil
import signal
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

from .spec import public_url, sha256, write_json


@functools.cache
def gnu_time() -> bool:
    """Whether /usr/bin/time is GNU time, whose -v report the benchmark parses.

    macOS ships BSD time, which rejects -v, so commands would fail before running.
    """
    try:
        result = subprocess.run(
            ["/usr/bin/time", "--version"], capture_output=True, text=True, check=False
        )
    except OSError:
        return False
    return "GNU" in result.stdout + result.stderr


def disk_bytes(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink())


def group_rss(group: int) -> int | None:
    """Linux process-group RSS sum; shared pages can be counted more than once."""
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    total = 0
    found = False
    for entry in proc.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == group:
                found = True
                total += int(fields[21]) * os.sysconf("SC_PAGE_SIZE")
        except OSError, IndexError, ValueError:
            continue  # A process can exit between enumeration and reading stat.
    return total if found else None


class Runtime:
    def __init__(self, plan: dict[str, Any], out: Path, *, resume: bool) -> None:
        self.plan, self.out, self.resume = plan, out.resolve(), resume
        retention = self.out / "retention.json"
        if retention.exists():
            record = json.loads(retention.read_text())
            if not isinstance(record, dict) or record.get("schema_version") != 1:
                raise ValueError("Unrecognized retention record; preserve it and use a new output")
            if record.get("prune_status") != "NOT_REQUESTED":
                raise ValueError(
                    "Experiment work was pruned or removal was interrupted; "
                    "preserve the archived evidence and use a new output directory"
                )
        self.start = time.monotonic()
        self.limits = plan["spec"]["limits"]
        self.events: list[dict[str, Any]] = []
        self.images: dict[str, Any] = {}
        self.budget_path = self.out / "runtime-budget.json"
        self.previous_seconds = (
            json.loads(self.budget_path.read_text())["elapsed_seconds"]
            if self.budget_path.exists()
            else 0.0
        )
        self.last_check = -1.0
        self.out.mkdir(parents=True, exist_ok=True)
        lock = self.out / "experiment.json"
        if lock.exists():
            previous = json.loads(lock.read_text())
            if not resume or previous["experiment_id"] != plan["experiment_id"]:
                raise ValueError(
                    "Existing results require --resume and identical scientific identity; "
                    "use a new output directory"
                )
        elif resume:
            raise ValueError("Cannot resume a missing experiment")
        else:
            write_json(lock, plan)
        self.check()

    def check(self) -> None:
        elapsed = self.previous_seconds + time.monotonic() - self.start
        if elapsed > self.limits["seconds"]:
            raise ValueError("Campaign wall-time limit exceeded")
        if elapsed - self.last_check < 1:
            return
        self.last_check = elapsed
        write_json(self.budget_path, {"elapsed_seconds": elapsed})
        if disk_bytes(self.out) > self.limits["disk_bytes"]:
            raise ValueError("Campaign output-storage limit exceeded")

    def asset(self, name: str) -> Path:
        return Path(self.plan["assets"][name]["path"])

    def task_name(self, folder: Path) -> str:
        """Name a task by its folder relative to the (resolved) output root."""
        return str(folder.resolve().relative_to(self.out))

    def cached(self, folder: Path, inputs: list[Path], recipe: object) -> bool:
        marker = folder / "task.json"
        if not self.resume or not marker.exists():
            return False
        record = json.loads(marker.read_text())
        hashes = {str(p): sha256(p) for p in inputs}
        valid = (
            record["status"] == "COMPLETE"
            and record["inputs"] == hashes
            and record["recipe"] == recipe
            and bool(record["outputs"])
        )
        valid = valid and all(
            Path(p).is_file() and sha256(Path(p)) == h for p, h in record["outputs"].items()
        )
        if valid:
            self.events.append({"task": self.task_name(folder), "status": "CACHED"})
        return valid

    def seal(
        self,
        folder: Path,
        inputs: list[Path],
        outputs: list[Path],
        recipe: object,
        *,
        elapsed: float = 0.0,
    ) -> None:
        if not outputs or any(not p.is_file() for p in outputs):
            raise ValueError("Required task outputs are missing")
        write_json(
            folder / "task.json",
            {
                "status": "COMPLETE",
                "recipe": recipe,
                "inputs": {str(p): sha256(p) for p in inputs},
                "outputs": {str(p): sha256(p) for p in outputs},
                "wall_seconds": elapsed,
            },
        )
        self.events.append({"task": self.task_name(folder), "status": "COMPLETED"})
        self.check()

    def command(
        self,
        label: str,
        argv: list[str],
        cwd: Path,
        inputs: list[Path],
        *,
        image: str | None = None,
        network: bool = False,
        env: dict[str, str] | None = None,
    ) -> None:
        self.check()
        cwd.mkdir(parents=True, exist_ok=True)
        log = self.out / "commands" / label
        attempt = 1
        while (log / f"attempt-{attempt}").exists():
            attempt += 1
        log = log / f"attempt-{attempt}"
        log.mkdir(parents=True)
        command = argv
        if Path(argv[0]).name == "macs3" and len(argv) > 1 and argv[1] == "callpeak":
            launcher = Path(__file__).parents[1] / "macs3_launch.py"
            python = (
                str(Path(argv[0]).parent / "python") if Path(argv[0]).is_absolute() else "python"
            )
            command = [
                python,
                str(launcher),
                "--cpus",
                str(self.limits["cpus"]),
                "--receipt",
                str(log / "macs3.runtime.json"),
                "--",
                *argv[1:],
            ]
        tool_command = command
        container_name = f"genesis-bench-{os.getpid()}-{label}"[:120]
        if image:
            image_ref = self.plan["spec"]["images"][image]
            inspected = subprocess.check_output(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{json .Id}} {{json .Architecture}} {{json .RepoDigests}}",
                    image_ref,
                ],
                text=True,
            ).strip()
            self.images[image] = {"requested": image_ref, "resolved": inspected}
            # Every source asset is read-only; only this campaign output is writable.
            mounts = {p.parent for p in inputs} | {Path(__file__).resolve().parents[3]}
            mounts = {p for p in mounts if p != self.out and self.out not in p.parents}
            command = [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--name",
                container_name,
                "--cpus",
                str(self.limits["cpus"]),
                "--memory",
                f"{self.limits['memory_gb']}g",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--network",
                "bridge" if network else "none",
            ]
            for mount in sorted(mounts):
                command += ["-v", f"{mount}:{mount}:ro"]
            command += [
                "-v",
                f"{self.out}:{self.out}:rw",
                "-w",
                str(cwd),
                image_ref,
                "bash",
                "-c",
                '"$@"; result=$?; cat /sys/fs/cgroup/memory.peak > "$BENCH_MEMORY" 2>/dev/null; '
                'cat /sys/fs/cgroup/io.stat > "$BENCH_IO" 2>/dev/null; exit "$result"',
                "benchmark",
                *tool_command,
            ]
            # Add only explicit non-secret runtime variables before the image name.
            position = command.index(image_ref)
            command[position:position] = [
                "-e",
                f"BENCH_MEMORY={log / 'container-memory.peak'}",
                "-e",
                f"BENCH_IO={log / 'container-io.stat'}",
            ]
        timed = (
            ["/usr/bin/time", "-v", "-o", str(log / "time.txt"), *command]
            if gnu_time()
            else command
        )
        record = {
            "argv": command,
            "tool_argv": argv,
            "cwd": str(cwd),
            "image": self.images.get(image),
            "source": self.plan["source"],
            "inputs": {str(p): sha256(p) for p in inputs},
            "utc": datetime.datetime.now(datetime.UTC).isoformat(),
        }
        start = time.monotonic()
        status = "FAILED"
        peak_rss: int | None = None
        with (log / "stdout").open("wb") as stdout, (log / "stderr").open("wb") as stderr:
            process = subprocess.Popen(
                timed,
                cwd=cwd,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
                env=None if env is None else os.environ | env,
            )
            try:
                while process.poll() is None:
                    time.sleep(0.1)
                    if image is None:
                        measured = group_rss(process.pid)
                        if measured is not None:
                            peak_rss = max(peak_rss or 0, measured)
                            if peak_rss > self.limits["memory_gb"] * 1024**3:
                                raise ValueError("Native process-group memory budget exceeded")
                    self.check()
                status = "COMPLETE" if process.returncode == 0 else "FAILED"
            except BaseException:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                if image:
                    subprocess.run(
                        ["docker", "stop", "-t", "1", container_name],
                        capture_output=True,
                        check=False,
                    )
                raise
            finally:
                record.update(
                    status=status,
                    exit_status=process.returncode,
                    wall_seconds=time.monotonic() - start,
                    sampled_process_group_peak_rss_bytes=peak_rss,
                    memory_scope=(
                        "Linux process-group RSS sum sampled about every 0.1 s; "
                        "shared pages may be counted twice"
                    ),
                )
                write_json(log / "run.json", record)
        if status != "COMPLETE":
            raise ValueError(f"Command failed: {label}; full logs retained in {log}")


def fetch(plan: dict[str, Any]) -> None:
    if plan["dataset"]["status"] != "READY":
        raise ValueError(
            "Dataset reference/metadata unresolved; candidate catalogs cannot be fetched"
        )
    missing = [a for a in plan["assets"].values() if not Path(a["path"]).is_file()]
    if any("url" not in a or "bytes" not in a for a in missing):
        raise ValueError("Every missing asset needs a public URL, exact size and SHA256")
    budget = plan["spec"]["limits"]["download_bytes"]
    if sum(a["bytes"] for a in missing) > budget:
        raise ValueError("Download byte budget exceeded before fetching")
    start = time.monotonic()
    for asset in missing:
        public_url(asset["url"])
        target = Path(asset["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(target.parent).free < asset["bytes"]:
            raise ValueError("Insufficient free disk for download")
        temporary = target.with_suffix(target.suffix + ".partial")
        if temporary.exists():
            raise ValueError("Existing partial download preserved: " + str(temporary))
        try:
            with (
                urllib.request.urlopen(asset["url"], timeout=30) as response,
                temporary.open("xb") as stream,
            ):
                public_url(response.geturl())
                total = 0
                while block := response.read(1024 * 1024):
                    total += len(block)
                    if (
                        total > asset["bytes"]
                        or time.monotonic() - start > plan["spec"]["limits"]["seconds"]
                    ):
                        raise ValueError("Download size or time limit exceeded")
                    stream.write(block)
            if total != asset["bytes"] or sha256(temporary) != asset["sha256"]:
                raise ValueError("Downloaded artifact size/checksum mismatch")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
