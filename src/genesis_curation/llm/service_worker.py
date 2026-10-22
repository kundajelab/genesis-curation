"""Node-local supervisor for one digest-pinned vLLM container, independent of Nextflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..contracts.records import canonical, dump, fingerprint, load, now, validate
from ..registry.store import lock
from . import providers
from .security import check, credential, redact, schema

TERMINAL = {"STOPPED", "FAILED"}


def run(argv: list[str], *, timeout: float = 20, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, env=env, check=False
    )
    if result.returncode:
        raise ValueError("Serving runtime command failed: " + redact(result.stderr[-2048:]))
    return result.stdout.strip()


def inspect_node(config: dict[str, Any]) -> dict[str, Any]:
    worker, budgets = config["node"]["worker"], config["node"]["budgets"]
    mem = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, value = line.partition(":")
        mem[key] = int(value.strip().split()[0]) // 1024
    cache = Path(config["cache"])
    ancestor = cache
    while not ancestor.exists():
        ancestor = ancestor.parent
    disks = shutil.disk_usage(ancestor)
    raw = run(
        [
            "nvidia-smi",
            "--query-gpu=uuid,memory.total,memory.free,driver_version",
            "--format=csv,noheader,nounits",
        ]
    )
    gpus = []
    for line in raw.splitlines():
        uuid, total, free, driver = [value.strip() for value in line.split(",")]
        gpus.append(
            {"uuid": uuid, "total_mib": int(total), "free_mib": int(free), "driver": driver}
        )
    image_id = run(["docker", "image", "inspect", "--format", "{{.Id}}", config["image"]])
    runtime = run(["docker", "version", "--format", "{{.Server.Version}}"])
    hf_version = run([config["hf_cli"], "--version"])
    git_sha = run(["git", "-C", worker["repo"], "rev-parse", "HEAD"])
    if run(["git", "-C", worker["repo"], "status", "--porcelain", "--untracked-files=no"]):
        raise ValueError("Model services require a clean, reviewed worker checkout")
    return {
        "schema_version": 1,
        "node_id": config["node"]["id"],
        "cpu": os.cpu_count(),
        "memory_total_mib": mem["MemTotal"],
        "memory_available_mib": mem["MemAvailable"],
        "disk_free_bytes": disks.free,
        "gpus": gpus,
        "image_id": image_id,
        "docker_version": runtime,
        "hf_version": hf_version,
        "git_sha": git_sha,
        "budgets": budgets,
        "observed_at": now(),
    }


def preconditions(plan: dict[str, Any], observed: dict[str, Any]) -> None:
    config, model = plan["config"], plan["model"]["data"]
    node, limits = config["node"], config["node"]["budgets"]
    if not node["site_approved"] or node["login_node"] or "llm-serving" not in node["roles"]:
        raise ValueError("Node is not approved for model serving")
    if node["allocation"] and datetime.fromisoformat(
        node["allocation"]["expires_at"]
    ) <= datetime.now(UTC):
        raise ValueError("Site allocation has expired")
    if (
        not config["license_accepted"]
        or not model["license"]
        or model["gated"]
        and not config["gated_access_confirmed"]
    ):
        raise ValueError("Model license/gated access requires explicit prior review")
    if model["weight_format"] != "safetensors" or not model["chat_template"]:
        raise ValueError("Safetensors and a reviewed chat template are required")
    quantization = model["quantization"]
    if quantization and not quantization.get("quant_method"):
        raise ValueError("Unknown model quantization metadata")
    if config.get("quantization") != (quantization or {}).get("quant_method"):
        raise ValueError(
            "Quantization must exactly match the explicitly reviewed model configuration"
        )
    if not model["architecture"] or any(
        a not in config["supported_architectures"] for a in model["architecture"]
    ):
        raise ValueError("Model architecture not in the site's reviewed runtime compatibility list")
    if model["context_length"] is None or config["context_length"] > model["context_length"]:
        raise ValueError("Requested context exceeds known model context")
    if config["tensor_parallel_size"] != len(node["gpu_uuids"]) or not node["gpu_uuids"]:
        raise ValueError("Tensor parallel size must match explicitly selected GPUs on this node")
    if not node["dedicated"] and (
        not limits["pipeline_memory_mib"]
        or set(node["gpu_uuids"]) & set(limits["pipeline_gpu_uuids"])
    ):
        raise ValueError("Shared nodes need separate pipeline GPUs and an explicit RAM reservation")
    available = {gpu["uuid"]: gpu for gpu in observed["gpus"]}
    # Explicit KV/concurrency budget plus weights and runtime overhead; this is a planning estimate.
    required = (
        model["weight_bytes"] / 1048576 + config["kv_cache_mib"] + config["runtime_overhead_mib"]
    ) / len(node["gpu_uuids"])
    for gpu_id in node["gpu_uuids"]:
        gpu = available.get(gpu_id)
        if (
            not gpu
            or min(
                gpu["free_mib"],
                limits["gpu_memory_mib"],
                gpu["total_mib"] * config["gpu_memory_utilization"],
            )
            < required
        ):
            raise ValueError("Insufficient GPU memory for the reviewed weights/KV/runtime budget")
        if gpu["total_mib"] * config["gpu_memory_utilization"] > limits["gpu_memory_mib"]:
            raise ValueError("vLLM memory utilization exceeds the per-GPU reservation")
        if gpu["free_mib"] < gpu["total_mib"] * config["gpu_memory_utilization"]:
            raise ValueError("Insufficient free GPU memory for the configured vLLM reservation")
    disk = sum(item["size"] for item in model["files"]) * 2
    if disk > min(observed["disk_free_bytes"], limits["disk_bytes"]):
        raise ValueError("Insufficient disk budget for staging and resumable cache")
    if (
        limits["memory_mib"] + limits["pipeline_memory_mib"] > observed["memory_available_mib"]
        or limits["cpu"] > observed["cpu"]
    ):
        raise ValueError("Insufficient host RAM/CPU budget")
    original = plan.get("observed")
    if original and any(
        original[key] != observed[key]
        for key in ("image_id", "git_sha", "docker_version", "hf_version")
    ):
        raise ValueError("Node checkout/image/runtime changed since plan review")


def verify_plan(plan: dict[str, Any]) -> None:
    if (
        set(plan) != {"schema_version", "plan_hash", "config", "model", "observed"}
        or plan["schema_version"] != 1
    ):
        raise ValueError("Unsupported deployment plan")
    check(plan["config"], schema("deployment-config-v1"))
    validate(plan["model"], "model_inspection")
    if plan["plan_hash"] != fingerprint({k: v for k, v in plan.items() if k != "plan_hash"}):
        raise ValueError("Deployment plan content hash changed")
    config = plan["config"]
    from ..execution.transport import validate_worker

    validate_worker(config["node"]["worker"])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./_:-]*@sha256:[a-f0-9]{64}", config["image"]):
        raise ValueError("Serving image must be pinned by SHA256 digest")
    for key in ("hf_cli", "cache", "api_key_file"):
        if not Path(config[key]).is_absolute() or any(c in config[key] for c in "\r\n\x00"):
            raise ValueError("Serving paths must be absolute and free of control characters")
    if any(not re.fullmatch(r"GPU-[a-fA-F0-9-]+", gpu) for gpu in config["node"]["gpu_uuids"]):
        raise ValueError("Select full physical GPU UUIDs; MIG allocation requires a future backend")


def state(
    folder: Path, value: str, *, reason: str = "", container: str | None = None
) -> dict[str, Any]:
    plan = load(folder / "plan.json")
    previous = load(folder / "state.json") if (folder / "state.json").exists() else {}
    result = {
        "plan_hash": plan["plan_hash"],
        "node_id": plan["config"]["node"]["id"],
        "model_id": plan["model"]["data"]["model_id"],
        "model_revision": plan["model"]["data"]["revision"],
        "desired_state": "STOPPED" if (folder / "stop.request").exists() else "READY",
        "observed_state": value,
        "container_id": container or previous.get("container_id"),
        "endpoint": f"http://127.0.0.1:{plan['config']['port']}/v1" if value == "READY" else None,
        "reservations": plan["config"]["node"]["budgets"],
        "reason": reason,
        "updated": now(),
    }
    dump(folder / "state.json", result)
    if (
        previous.get("observed_state") != value
        or previous.get("desired_state") != result["desired_state"]
    ):
        with (folder / "events.log").open("a") as events:
            events.write(canonical(result) + "\n")
            events.flush()
    return result


def container_info(plan: dict[str, Any]) -> dict[str, Any] | None:
    name = "genesis-llm-" + plan["plan_hash"][:24]
    # Filter first to distinguish an absent container from a daemon communication failure.
    present = run(["docker", "ps", "-a", "--filter", "name=^/" + name + "$", "--format", "{{.ID}}"])
    if not present:
        return None
    raw = run(
        [
            "docker",
            "inspect",
            "--format",
            "{{json .Id}}\n{{json .State.Running}}\n{{json .Config.Labels}}\n{{json "
            ".Config.Image}}",
            name,
        ]
    )
    identifier, running, labels, image = [json.loads(line) for line in raw.splitlines()]
    if labels.get("genesis.plan") != plan["plan_hash"] or image != plan["config"]["image"]:
        raise ValueError("Container ownership/image mismatch; refusing to operate on it")
    return {"id": identifier, "running": running}


def stop_container(plan: dict[str, Any]) -> None:
    owned = container_info(plan)
    if owned and owned["running"]:
        run(["docker", "stop", "--time", "10", owned["id"]], timeout=20)
    after = container_info(plan)
    if after and after["running"]:
        raise ValueError("Owned container has not stopped")


def process_identity(pid: int) -> str | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] if fields[0] != "Z" else None
    except OSError, IndexError:
        return None


def supervisor_alive(folder: Path) -> bool:
    if not (folder / "supervisor.json").exists():
        return False
    owner = load(folder / "supervisor.json")
    return bool(owner["start"] and process_identity(owner["pid"]) == owner["start"])


def status(folder: Path) -> dict[str, Any]:
    if not (folder / "plan.json").exists():
        raise ValueError("Unknown deployment")
    plan = load(folder / "plan.json")
    verify_plan(plan)
    previous = (
        load(folder / "state.json")
        if (folder / "state.json").exists()
        else state(folder, "UNKNOWN", reason="Missing service journal")
    )
    try:
        owned = container_info(plan)
        alive = supervisor_alive(folder)
        if cancelled(folder, plan["config"]):
            return stop(folder)
        if (
            alive
            and (datetime.now(UTC) - datetime.fromisoformat(previous["updated"])).total_seconds()
            > 30
        ):
            return state(
                folder, "UNKNOWN", reason="Stale supervisor heartbeat; reservations retained"
            )
        if not alive:
            if owned and owned["running"]:
                # The container remains owned and reserved; a fresh probe is required for READY.
                if readiness(plan):
                    return state(
                        folder, "READY", reason="Reconciled owned container", container=owned["id"]
                    )
                return state(
                    folder, "UNHEALTHY", reason="Owned container failed structured readiness"
                )
            if previous["observed_state"] in TERMINAL:
                return previous
            return state(
                folder,
                "UNKNOWN",
                reason="Supervisor missing; reconcile or explicitly stop the owned deployment",
            )
        return previous
    except ValueError, OSError, subprocess.SubprocessError:
        return state(folder, "UNKNOWN", reason="Runtime unavailable; reservations retained")


def stop(folder: Path) -> dict[str, Any]:
    plan = load(folder / "plan.json")
    verify_plan(plan)
    (folder / "stop.request").touch(mode=0o600)
    state(folder, "STOPPING")
    try:
        stop_container(plan)
        if supervisor_alive(folder):
            return state(
                folder,
                "STOPPING",
                reason="Supervisor is stopping; reservations retained until confirmed",
            )
        return state(folder, "STOPPED")
    except ValueError, OSError, subprocess.SubprocessError:
        return state(
            folder,
            "UNKNOWN",
            reason="Stop could not establish ownership/termination; reservations retained",
        )


def cancelled(folder: Path, config: dict[str, Any]) -> bool:
    allocation = config["node"]["allocation"]
    return (folder / "stop.request").exists() or bool(
        allocation and datetime.fromisoformat(allocation["expires_at"]) <= datetime.now(UTC)
    )


def stage(folder: Path, plan: dict[str, Any]) -> Path:
    config, model = plan["config"], plan["model"]["data"]
    target = Path(config["cache"]) / model["revision"]
    target.mkdir(parents=True, exist_ok=True)
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "LANG", "SSL_CERT_FILE", "SSL_CERT_DIR"}
    }
    if config["hf_token_file"]:
        env["HF_TOKEN"] = credential({"file": config["hf_token_file"]})
    argv = [
        config["hf_cli"],
        "download",
        model["model_id"],
        "--revision",
        model["revision"],
        "--local-dir",
        str(target),
        "--include",
        *[item["path"] for item in model["files"]],
    ]
    with (folder / "staging.log").open("a") as log:
        os.chmod(folder / "staging.log", 0o600)
        with subprocess.Popen(argv, env=env, stdout=log, stderr=log) as process:
            deadline = time.monotonic() + 3600
            heartbeat = time.monotonic()
            while process.poll() is None:
                if time.monotonic() - heartbeat >= 10:
                    state(folder, "STAGING")
                    heartbeat = time.monotonic()
                if cancelled(folder, config) or time.monotonic() > deadline:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    raise ValueError(
                        "Staging cancelled or exceeded one-hour budget; partial cache preserved"
                    )
                time.sleep(0.25)
            if process.returncode:
                raise ValueError("Revision-pinned model staging failed")
    actual = []
    allowed = {item["path"] for item in model["files"]} | {"genesis-snapshot.json"}
    for candidate in target.rglob("*"):
        relative = candidate.relative_to(target)
        if relative.parts[0] == ".cache":
            continue
        if candidate.is_symlink() or candidate.is_file() and relative.as_posix() not in allowed:
            raise ValueError(
                "Model cache contains an unverified extra file; inspect it before reuse"
            )
    for item in model["files"]:
        path = (target / item["path"]).resolve(strict=True)
        if not path.is_relative_to(target.resolve()) or path.stat().st_size != item["size"]:
            raise ValueError("Staged model path/size mismatch")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if item["sha256"] and digest != item["sha256"]:
            raise ValueError("Staged model SHA256 mismatch")
        if not item["sha256"]:
            if not item.get("git_oid"):
                raise ValueError("Non-LFS model file lacks a pinned Git blob digest")
            git_hash = hashlib.sha1(usedforsecurity=False)
            git_hash.update(f"blob {item['size']}\0".encode())
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    git_hash.update(chunk)
            if git_hash.hexdigest() != item["git_oid"]:
                raise ValueError("Staged model Git blob mismatch")
        actual.append({**item, "sha256": digest})
    dump(
        target / "genesis-snapshot.json",
        {"model": model["model_id"], "revision": model["revision"], "files": actual},
        immutable=True,
    )
    return target


def docker_command(plan: dict[str, Any], folder: Path, model_path: Path) -> list[str]:
    config, model = plan["config"], plan["model"]["data"]
    limits, gpus = config["node"]["budgets"], config["node"]["gpu_uuids"]
    return [
        "docker",
        "run",
        "--detach",
        "--name",
        "genesis-llm-" + plan["plan_hash"][:24],
        "--label",
        "genesis.plan=" + plan["plan_hash"],
        "--pull=never",
        "--read-only",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit",
        "512",
        "--ipc=private",
        "--shm-size",
        "1g",
        "--tmpfs",
        "/tmp:rw,nosuid,size=1g",
        "--memory",
        str(limits["memory_mib"]) + "m",
        "--cpus",
        str(limits["cpu"]),
        "--gpus",
        '"device=' + ",".join(gpus) + '"',
        "--network",
        "bridge",
        "--publish",
        f"127.0.0.1:{config['port']}:8000",
        "--env-file",
        str(folder / "service.env"),
        "--env",
        "HF_HUB_OFFLINE=1",
        "--env",
        "TRANSFORMERS_OFFLINE=1",
        "--env",
        "VLLM_CACHE_ROOT=/tmp/vllm",
        "--env",
        "VLLM_NO_USAGE_STATS=1",
        "--env",
        "HF_HUB_DISABLE_TELEMETRY=1",
        "--env",
        "DO_NOT_TRACK=1",
        "--mount",
        f"type=bind,src={model_path},dst=/model,readonly",
        "--entrypoint",
        "python3",
        config["image"],
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        "/model",
        "--served-model-name",
        model["model_id"],
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--load-format",
        "safetensors",
        "--disable-log-requests",
        "--max-model-len",
        str(config["context_length"]),
        "--max-num-seqs",
        str(config["concurrency"]),
        "--tensor-parallel-size",
        str(config["tensor_parallel_size"]),
        "--gpu-memory-utilization",
        str(config["gpu_memory_utilization"]),
    ]


def readiness(plan: dict[str, Any]) -> bool:
    config, model = plan["config"], plan["model"]["data"]
    secret = credential({"file": config["api_key_file"]})
    try:
        code, raw = providers.post(
            {
                "endpoint": f"http://127.0.0.1:{config['port']}/v1",
                "external": False,
                "timeout_seconds": 5,
            },
            {
                "model": model["model_id"],
                "messages": [{"role": "user", "content": "Return JSON with ok true"}],
                "max_tokens": 32,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "ready", "strict": True, "schema": schema("probe-v1")},
                },
            },
            secret,
        )
        if code != 200:
            return False
        providers.parse_response(redact(raw, (secret,)), model["model_id"], schema("probe-v1"))
        return True
    except ValueError, OSError:
        return False


def supervise(folder: Path) -> None:
    os.umask(0o077)
    plan = load(folder / "plan.json")
    verify_plan(plan)
    config = plan["config"]
    with lock(folder):
        dump(
            folder / "supervisor.json", {"pid": os.getpid(), "start": process_identity(os.getpid())}
        )
        try:
            if cancelled(folder, config):
                state(folder, "STOPPED")
                return
            state(folder, "STAGING")
            preconditions(plan, inspect_node(config))
            staged = stage(folder, plan)
            if cancelled(folder, config):
                state(folder, "STOPPED")
                return
            preconditions(plan, inspect_node(config))
            secret = credential({"file": config["api_key_file"]})
            (folder / "service.env").write_text("VLLM_API_KEY=" + secret + "\n")
            state(folder, "STARTING")
            run(docker_command(plan, folder, staged), timeout=30)
            deadline = time.monotonic() + config["readiness_seconds"]
            while not cancelled(folder, config) and time.monotonic() < deadline:
                owned = container_info(plan)
                if not owned or not owned["running"]:
                    raise ValueError("Owned container exited before readiness")
                if readiness(plan):
                    state(folder, "READY", container=owned["id"])
                    break
                time.sleep(1)
            else:
                raise ValueError("Readiness timed out or deployment was stopped")
            while not cancelled(folder, config):
                owned = container_info(plan)
                if not owned or not owned["running"]:
                    raise ValueError("Owned service exited")
                state(folder, "READY" if readiness(plan) else "UNHEALTHY", container=owned["id"])
                time.sleep(5)
            state(folder, "STOPPING")
            stop_container(plan)
            state(folder, "STOPPED")
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            try:
                stop_container(plan)
                state(
                    folder,
                    "STOPPED" if cancelled(folder, config) else "FAILED",
                    reason=redact(str(error)),
                )
            except ValueError, OSError, subprocess.SubprocessError:
                state(folder, "UNKNOWN", reason="Unable to confirm owned service termination")
        finally:
            (folder / "service.env").unlink(missing_ok=True)


def launch(root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    verify_plan(plan)
    if root != Path(plan["config"]["node"]["worker"]["root"]) / "llm-services":
        raise ValueError("Service root differs from the reviewed node root")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock(root):
        folder = root / plan["plan_hash"]
        if folder.exists():
            if load(folder / "plan.json") != plan:
                raise ValueError("Existing deployment plan differs")
            return status(folder)
        preconditions(plan, inspect_node(plan["config"]))
        reservations = []
        for previous in root.glob("*/plan.json"):
            observed = status(previous.parent)
            if observed["observed_state"] not in TERMINAL:
                other = load(previous)["config"]
                if (
                    set(other["node"]["gpu_uuids"]) & set(plan["config"]["node"]["gpu_uuids"])
                    or other["port"] == plan["config"]["port"]
                ):
                    raise ValueError("Resource collision with a reserved model service")
                reservations.append(other["node"]["budgets"])
        current = inspect_node(plan["config"])
        limits = plan["config"]["node"]["budgets"]
        if (
            sum(r["memory_mib"] for r in reservations)
            + limits["memory_mib"]
            + limits["pipeline_memory_mib"]
            > current["memory_total_mib"]
            or sum(r["cpu"] for r in reservations) + limits["cpu"] > current["cpu"]
        ):
            raise ValueError("Node-wide service reservations exceed resources")
        if (
            sum(r["disk_bytes"] for r in reservations) + limits["disk_bytes"]
            > current["disk_free_bytes"]
        ):
            raise ValueError("Node-wide service disk reservations exceed currently free space")
        folder.mkdir(mode=0o700)
        dump(folder / "plan.json", plan, immutable=True)
        state(folder, "PLANNED")
        with (folder / "supervisor.log").open("a") as log:
            process = subprocess.Popen(
                [
                    plan["config"]["node"]["worker"]["python"],
                    "-m",
                    "genesis_curation.llm.service_worker",
                    "supervise",
                    str(folder),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
                cwd=plan["config"]["node"]["worker"]["repo"],
            )
            dump(
                folder / "supervisor.json",
                {"pid": process.pid, "start": process_identity(process.pid)},
            )
        return load(folder / "state.json")


def logs(folder: Path) -> dict[str, Any]:
    plan = load(folder / "plan.json")
    secret = credential({"file": plan["config"]["api_key_file"]})
    hf_secret = (
        credential({"file": plan["config"]["hf_token_file"]})
        if plan["config"]["hf_token_file"]
        else ""
    )
    result = {}
    for name in ("staging.log", "supervisor.log", "events.log"):
        path = folder / name
        if path.exists():
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 65536))
                result[name] = redact(
                    stream.read(65536).decode(errors="replace"), (secret, hf_secret)
                )
    owned = container_info(plan)
    if owned:
        output = subprocess.run(
            ["docker", "logs", "--tail", "100", owned["id"]],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        result["container"] = redact((output.stdout + output.stderr)[-65536:], (secret,))
    return {"plan_hash": plan["plan_hash"], "logs": result}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("inspect", "launch", "status", "logs", "stop", "supervise")
    )
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == "supervise":
        supervise(args.path)
        return
    payload = json.load(sys.stdin) if args.action in {"inspect", "launch"} else {}
    if not isinstance(payload, dict):
        raise ValueError("Worker request must be a JSON object")
    if args.action == "inspect":
        result = inspect_node(payload)
    elif args.action == "launch":
        result = launch(args.path, payload)
    else:
        result = {"status": status, "logs": logs, "stop": stop}[args.action](args.path)
    print(canonical(result))


if __name__ == "__main__":
    main()
