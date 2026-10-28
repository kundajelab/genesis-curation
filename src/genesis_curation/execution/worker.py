"""Idempotent worker entry point; uses existing local or SSH authentication."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


def atomic(path: Path, value: object) -> None:
    temporary = path.with_suffix(".temporary")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n")
    temporary.replace(path)


def inspect(folder: Path) -> dict[str, Any]:
    status = folder / "status.json"
    if status.exists():
        return json.loads(status.read_text())
    return {"state": "UNKNOWN" if folder.exists() else "ABSENT"}


def launch(folder: Path, payload: dict[str, Any]) -> dict[str, Any]:
    identity = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    try:
        folder.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        if (folder / "payload.json").exists():
            previous = json.loads((folder / "payload.json").read_text())
            if previous != payload:
                raise ValueError("Attempt identity collision: payload changed") from None
        return inspect(folder)
    atomic(folder / "payload.json", payload)
    atomic(
        folder / "status.json", {"state": "STARTING", "identity": identity, "updated": time.time()}
    )
    # The detached supervisor survives the SSH session. Logs stay private on the worker.
    with (folder / "supervisor.log").open("ab") as log:
        subprocess.Popen(
            [payload["python"], "-m", "genesis_curation.execution.worker", "execute", str(folder)],
            cwd=payload["repo"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    return inspect(folder)


def execute(folder: Path) -> None:
    import fcntl

    payload = json.loads((folder / "payload.json").read_text())
    with (folder / "supervisor.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        completed = threading.Event()
        started = time.time()

        def heartbeat() -> None:
            while not completed.is_set():
                atomic(
                    folder / "status.json",
                    {
                        "state": "RUNNING",
                        "started": started,
                        "updated": time.time(),
                        "supervisor_pid": os.getpid(),
                    },
                )
                completed.wait(10)

        thread = threading.Thread(target=heartbeat)
        thread.start()
        code, reason = 2, "worker preparation failed"
        try:
            actual = subprocess.check_output(
                ["git", "-C", payload["repo"], "rev-parse", "HEAD"], text=True
            ).strip()
            dirty = subprocess.check_output(
                ["git", "-C", payload["repo"], "status", "--porcelain"], text=True
            )
            if actual != payload["git_sha"] or dirty:
                raise ValueError("Worker checkout differs from the pinned clean revision")
            for item in payload["inputs"]:
                with Path(item["path"]).open("rb") as source:
                    if hashlib.file_digest(source, "sha256").hexdigest() != item["sha256"]:
                        raise ValueError("Worker input checksum mismatch")
            with (folder / "stdout").open("wb") as stdout, (folder / "stderr").open("wb") as stderr:
                code = subprocess.run(
                    payload["argv"],
                    cwd=payload["repo"],
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    check=False,
                ).returncode
            reason = "completed" if code == 0 else "command failed; inspect worker logs"
        except ValueError as error:
            # Fixed verification messages above contain no external command output.
            reason = str(error)
        except OSError, subprocess.SubprocessError:
            # Do not copy external error text, which may contain credentials, into the status page.
            reason = "checkout, input verification, or execution failed; inspect the worker"
        finally:
            completed.set()
            thread.join()
            atomic(
                folder / "status.json",
                {
                    "state": "SUCCEEDED" if code == 0 else "FAILED",
                    "exit_code": code,
                    "reason": reason,
                    "started": started,
                    "updated": time.time(),
                },
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["launch", "status", "execute"])
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    if args.action == "execute":
        execute(args.folder)
        return
    import sys

    value = (
        launch(args.folder, json.load(sys.stdin))
        if args.action == "launch"
        else inspect(args.folder)
    )
    print(json.dumps(value))


if __name__ == "__main__":
    main()
