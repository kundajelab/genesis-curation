"""Reviewable plans, controller journals and owned SSH tunnels for model services."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ..contracts.records import canonical, dump, fingerprint, now, record
from ..execution import transport
from ..registry import store
from . import service_worker
from .security import check, schema

MODULE = "genesis_curation.llm.service_worker"


def plan(
    config: dict[str, Any], model: dict[str, Any], observed: dict[str, Any] | None = None
) -> dict[str, Any]:
    check(config, schema("deployment-config-v1"))
    transport.validate_worker(config["node"]["worker"])
    if observed is None:
        observed = transport.request(
            config["node"]["worker"],
            MODULE,
            "inspect",
            Path(config["node"]["worker"]["root"]),
            config,
        )
    result = {"schema_version": 1, "config": config, "model": model, "observed": observed}
    result["plan_hash"] = fingerprint(result)
    service_worker.verify_plan(result)
    service_worker.preconditions(result, observed)
    return result


def remote_path(value: dict[str, Any]) -> Path:
    return Path(value["config"]["node"]["worker"]["root"]) / "llm-services" / value["plan_hash"]


def journal(directory: Path, value: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    result = record("deployment", status, value["plan_hash"], version=2)
    with store.write(directory) as db:
        store.put(db, value["model"])
        store.put(
            db,
            record(
                "source",
                {
                    "location": "deployment-plan:" + value["plan_hash"],
                    "worker": "controller",
                    "sha256": fingerprint(value),
                    "snapshot": canonical(value),
                    "media_type": "application/json",
                },
                value["plan_hash"],
            ),
        )
        store.put(db, result)
        db.execute(
            "INSERT INTO service_heads VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "state=excluded.state",
            (value["plan_hash"], fingerprint(value), result["version"]),
        )
    return result


def control_path(directory: Path, value: dict[str, Any]) -> Path:
    # Unix domain socket paths are limited to approximately 108 bytes on Linux.
    folder = directory.resolve() / "tunnels"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / value["plan_hash"][:16]
    if len(str(path)) > 100:
        raise ValueError("Registry path too long for a private SSH control socket")
    return path


def tunnel(directory: Path, value: dict[str, Any], *, stop: bool = False) -> bool:
    config, worker = value["config"], value["config"]["node"]["worker"]
    if worker["transport"] == "local":
        return True
    socket = control_path(directory, value)
    command = ["ssh", *transport.SSH_OPTIONS, "-S", str(socket)]
    # A scoped control socket authenticates ownership. Never kill an arbitrary port/PID.
    if socket.exists():
        operation = "exit" if stop else "check"
        result = subprocess.run(
            [*command, "-O", operation, worker["host"]],
            capture_output=True,
            timeout=15,
            check=False,
        )
        if result.returncode == 0:
            return True
        if stop:
            return False
        raise ValueError("Stale/unreachable tunnel control socket; inspect it before reconnecting")
    if stop:
        return True
    forward = f"127.0.0.1:{config['controller_port']}:127.0.0.1:{config['port']}"
    result = subprocess.run(
        [
            *command,
            "-M",
            "-f",
            "-N",
            "-oExitOnForwardFailure=yes",
            "-oControlPersist=no",
            "-oServerAliveInterval=15",
            "-oServerAliveCountMax=2",
            "-L",
            forward,
            worker["host"],
        ],
        capture_output=True,
        timeout=20,
        check=False,
    )
    return result.returncode == 0


def apply(directory: Path, value: dict[str, Any], authorization: str) -> dict[str, Any]:
    service_worker.verify_plan(value)
    if authorization != value["plan_hash"]:
        raise ValueError("Apply requires authorization of the exact reviewed plan hash")
    folder = directory / "deployments" / value["plan_hash"]
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    dump(folder / "plan.json", value, immutable=True)
    worker = value["config"]["node"]["worker"]
    # The worker serializes admission and launch. Controller retries are idempotent by plan hash.
    try:
        result = transport.request(worker, MODULE, "launch", remote_path(value).parent, value)
    except ConnectionError, OSError, subprocess.SubprocessError:
        return operate(directory, value, "status")
    return journal(directory, value, result)


def operate(directory: Path, value: dict[str, Any], action: str) -> dict[str, Any]:
    if action not in {"status", "logs", "stop"}:
        raise ValueError("Unsupported deployment operation")
    service_worker.verify_plan(value)
    worker = value["config"]["node"]["worker"]
    tunnel_stopped = True
    if action == "stop":
        try:
            tunnel_stopped = tunnel(directory, value, stop=True)
        except ValueError, OSError, subprocess.SubprocessError:
            tunnel_stopped = False
    try:
        result = transport.request(worker, MODULE, action, remote_path(value))
    except ConnectionError, OSError, subprocess.SubprocessError:
        if action == "logs":
            raise ValueError("Deployment logs unavailable; node state is unknown") from None
        result = {
            "plan_hash": value["plan_hash"],
            "node_id": value["config"]["node"]["id"],
            "model_id": value["model"]["data"]["model_id"],
            "model_revision": value["model"]["data"]["revision"],
            "desired_state": "STOPPED" if action == "stop" else "READY",
            "observed_state": "UNKNOWN",
            "container_id": None,
            "endpoint": None,
            "reservations": value["config"]["node"]["budgets"],
            "reason": "Node unavailable; reservations retained",
            "updated": now(),
        }
    if action == "logs":
        return result
    if result["observed_state"] == "READY":
        try:
            connected = tunnel(directory, value)
        except ValueError, OSError, subprocess.SubprocessError:
            connected = False
        if not connected:
            result.update(
                observed_state="UNHEALTHY",
                endpoint=None,
                reason="Authenticated controller tunnel unavailable",
            )
        elif worker["transport"] == "ssh":
            result["endpoint"] = f"http://127.0.0.1:{value['config']['controller_port']}/v1"
    if not tunnel_stopped:
        result["reason"] = str(result.get("reason") or "") + (
            "; controller tunnel stop was not confirmed; inspect its owned socket"
        )
    return journal(directory, value, result)
