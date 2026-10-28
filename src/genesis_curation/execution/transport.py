"""Authenticated local/SSH JSON transport shared by processing and model services."""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from ..contracts.records import canonical, loads

SSH_OPTIONS = [
    "-oBatchMode=yes",
    "-oStrictHostKeyChecking=yes",
    "-oForwardAgent=no",
    "-oConnectTimeout=10",
]


def validate_worker(worker: dict[str, Any]) -> None:
    if worker.get("transport") not in {"local", "ssh"}:
        raise ValueError("Unsupported node transport")
    for key in ("repo", "python", "root"):
        value = worker.get(key)
        if (
            not isinstance(value, str)
            or not Path(value).is_absolute()
            or any(c in value for c in "\r\n\x00")
        ):
            raise ValueError("Node paths must be absolute and contain no control characters")
    if worker["transport"] == "ssh" and not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.@-]*", worker.get("host", "")
    ):
        raise ValueError("Use an existing SSH host alias without options")


def command(worker: dict[str, Any], argv: list[str]) -> tuple[list[str], dict[str, Any]]:
    validate_worker(worker)
    if worker["transport"] == "ssh":
        return ["ssh", *SSH_OPTIONS, worker["host"], shlex.join(argv)], {}
    return argv, {"cwd": worker["repo"]}


def request(
    worker: dict[str, Any],
    module: str,
    action: str,
    path: Path,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 30,
) -> dict[str, Any]:
    if module not in {
        "genesis_curation.execution.worker",
        "genesis_curation.llm.service_worker",
        "genesis_curation.llm.diagnostics",
    }:
        raise ValueError("Unsupported worker protocol")
    argv, options = command(worker, [worker["python"], "-m", module, action, str(path)])
    result = subprocess.run(
        argv,
        input=canonical(payload) if payload is not None else None,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
        **options,
    )
    if result.returncode or len(result.stdout) > 16 * 1024 * 1024:
        raise ConnectionError("Worker communication failed; check authentication and worker logs")
    return loads(result.stdout)
