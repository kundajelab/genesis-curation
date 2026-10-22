"""No-GPU model lifecycle acceptance using owned local/SSH worker simulations."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from verify_curation import rejects

from genesis_curation.contracts.records import canonical, dump, fingerprint, load, record
from genesis_curation.execution import transport
from genesis_curation.llm import deployments, models
from genesis_curation.llm import service_worker as worker
from genesis_curation.registry import store


def fixture(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    config = {
        "schema_version": 1,
        "node": {
            "schema_version": 1,
            "id": "node-a",
            "worker": {
                "transport": "local",
                "repo": str(root),
                "python": sys.executable,
                "root": str(root / "node"),
            },
            "roles": ["llm-serving"],
            "gpu_uuids": ["GPU-aaaa-bbbb"],
            "dedicated": True,
            "allocation": None,
            "budgets": {
                "memory_mib": 2048,
                "cpu": 1,
                "disk_bytes": 10000000,
                "gpu_memory_mib": 4000,
                "pipeline_memory_mib": 0,
                "pipeline_gpu_uuids": [],
            },
            "site_approved": True,
            "login_node": False,
        },
        "image": "vllm/vllm-openai@sha256:" + "1" * 64,
        "hf_cli": str(root / "hf"),
        "cache": str(root / "cache"),
        "port": 19090,
        "controller_port": 19091,
        "context_length": 1024,
        "concurrency": 1,
        "tensor_parallel_size": 1,
        "kv_cache_mib": 32,
        "runtime_overhead_mib": 64,
        "gpu_memory_utilization": 0.5,
        "supported_architectures": ["SyntheticForCausalLM"],
        "license_accepted": True,
        "gated_access_confirmed": False,
        "readiness_seconds": 1,
        "api_key_file": str(root / "key"),
        "hf_token_file": None,
    }
    (root / "key").write_text("synthetic-service-secret")
    (root / "key").chmod(0o600)
    weights = b"synthetic-not-a-real-model"
    snapshot = {
        "api": {
            "id": "synthetic/model",
            "sha": "a" * 40,
            "gated": False,
            "cardData": {"license": "synthetic-fixture-only"},
            "siblings": [
                {
                    "rfilename": "model.safetensors",
                    "size": len(weights),
                    "lfs": {"sha256": hashlib.sha256(weights).hexdigest(), "size": len(weights)},
                }
            ],
        },
        "config": {"architectures": ["SyntheticForCausalLM"], "max_position_embeddings": 4096},
        "tokenizer": {"chat_template": "synthetic-template"},
    }
    model = models.inspect("synthetic/model", "main", snapshot)
    observed = {
        "schema_version": 1,
        "node_id": "node-a",
        "cpu": 4,
        "memory_total_mib": 8192,
        "memory_available_mib": 8192,
        "disk_free_bytes": 10000000,
        "gpus": [
            {"uuid": "GPU-aaaa-bbbb", "total_mib": 8000, "free_mib": 7000, "driver": "synthetic"}
        ],
        "image_id": "sha256:" + "2" * 64,
        "docker_version": "synthetic",
        "hf_version": "synthetic",
        "git_sha": "3" * 40,
        "budgets": config["node"]["budgets"],
        "observed_at": "2026-01-01T00:00:00+00:00",
    }
    cache = Path(config["cache"]) / model["data"]["revision"]
    cache.mkdir(parents=True)
    (cache / "model.safetensors").write_bytes(weights)
    return config, model, observed


def changed(value: dict[str, Any]) -> dict[str, Any]:
    return {
        **value,
        "plan_hash": fingerprint(
            {key: child for key, child in value.items() if key != "plan_hash"}
        ),
    }


def checks(root: Path) -> None:
    config, model, observed = fixture(root)
    plan = deployments.plan(config, model, observed)
    assert model["data"]["revision"] == "a" * 40
    for field, value in (
        ("license_accepted", False),
        ("context_length", 999999),
        ("supported_architectures", []),
        ("image", "vllm:latest"),
        ("gpu_memory_utilization", 0.9),
    ):
        bad = {**config, field: value}
        rejects(lambda bad=bad: deployments.plan(bad, model, observed))
    for field in ("memory_available_mib", "disk_free_bytes"):
        rejects(lambda field=field: deployments.plan(config, model, {**observed, field: 0}))
    insufficient = copy.deepcopy(observed)
    insufficient["gpus"][0]["free_mib"] = 1
    rejects(lambda: deployments.plan(config, model, insufficient), "GPU memory")
    bad_node = copy.deepcopy(config)
    bad_node["node"]["allocation"] = {
        "id": "expired",
        "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    }
    rejects(lambda: deployments.plan(bad_node, model, observed), "expired")
    data = {**model["data"], "gated": True}
    gated = record("model_inspection", data, model["id"], version=2)
    rejects(lambda: deployments.plan(config, gated, observed), "gated")
    altered = copy.deepcopy(plan)
    altered["config"]["context_length"] = 512
    rejects(lambda: worker.verify_plan(altered), "hash")
    rejects(lambda: worker.preconditions(plan, {**observed, "git_sha": "4" * 40}), "changed")
    directory = root / "registry"
    store.initialize(directory)
    rejects(lambda: deployments.apply(directory, plan, "wrong"), "exact")
    node_root = Path(config["node"]["worker"]["root"]) / "llm-services"
    fake_process = Mock(pid=99999999)
    with (
        patch.object(worker, "inspect_node", return_value=observed),
        patch.object(worker.subprocess, "Popen", return_value=fake_process) as launched,
    ):
        state = worker.launch(node_root, plan)
        assert state["observed_state"] == "PLANNED"
        folder = node_root / plan["plan_hash"]
        with patch.object(worker, "status", return_value=state):
            assert worker.launch(node_root, plan) == state
        assert launched.call_count == 1
        # A different plan using the same GPU is rejected even after a controller restart.
        other = deployments.plan({**config, "context_length": 512}, model, observed)
        with patch.object(worker, "status", return_value={**state, "observed_state": "UNKNOWN"}):
            rejects(lambda: worker.launch(node_root, other), "collision")
    # Staging uses immutable revision, exact allowlist, scoped env, and verifies actual file bytes.
    fake = Mock()
    fake.poll.return_value = 0
    fake.returncode = 0
    fake.__enter__ = Mock(return_value=fake)
    fake.__exit__ = Mock(return_value=False)
    with patch.object(worker.subprocess, "Popen", return_value=fake) as staged:
        target = worker.stage(folder, plan)
        assert (
            staged.call_args.args[0][staged.call_args.args[0].index("--revision") + 1] == "a" * 40
        )
        assert "AWS_SECRET_ACCESS_KEY" not in staged.call_args.kwargs["env"]
        assert load(target / "genesis-snapshot.json")["revision"] == "a" * 40
        (target / "model.safetensors").write_text("corrupt")
        rejects(lambda: worker.stage(folder, plan), "mismatch")
    argv = worker.docker_command(plan, folder, target)
    assert "--network" in argv and "host" not in argv
    assert "127.0.0.1:19090:8000" in argv and "--trust-remote-code" not in argv
    assert "synthetic-service-secret" not in canonical(argv)
    assert "--env-file" in argv and "--read-only" in argv
    # Safe stop verifies both plan label and image; never kills the process at an arbitrary port.
    foreign = "\n".join(
        [
            json.dumps("container-id"),
            "true",
            json.dumps({"genesis.plan": "wrong"}),
            json.dumps(config["image"]),
        ]
    )
    with patch.object(worker, "run", side_effect=["container-id", foreign]) as commands:
        rejects(lambda: worker.stop_container(plan), "ownership")
        assert all("stop" not in call.args[0] for call in commands.call_args_list)
    with (
        patch.object(worker, "container_info", return_value={"id": "owned", "running": True}),
        patch.object(worker, "supervisor_alive", return_value=False),
        patch.object(worker, "readiness", return_value=True),
    ):
        assert worker.status(folder)["observed_state"] == "READY"
    with patch.object(worker, "container_info", side_effect=ValueError("unreachable")):
        assert worker.status(folder)["observed_state"] == "UNKNOWN"
        assert worker.stop(folder)["observed_state"] == "UNKNOWN"
    with (
        patch.object(worker, "stop_container"),
        patch.object(worker, "supervisor_alive", return_value=False),
    ):
        assert worker.stop(folder)["observed_state"] == "STOPPED"
    # A startup failure stops only this new deployment, retaining its cache and evidence.
    (folder / "stop.request").unlink(missing_ok=True)
    with (
        patch.object(worker, "inspect_node", return_value=observed),
        patch.object(worker, "stage", return_value=target),
        patch.object(worker, "run", side_effect=ValueError("startup failed")),
        patch.object(worker, "stop_container") as stopped,
    ):
        worker.supervise(folder)
        assert load(folder / "state.json")["observed_state"] == "FAILED"
        assert stopped.call_count == 1 and target.exists()
    stale = load(folder / "state.json")
    stale.update(updated="2000-01-01T00:00:00+00:00", observed_state="READY")
    dump(folder / "state.json", stale)
    with (
        patch.object(worker, "container_info", return_value={"id": "owned", "running": True}),
        patch.object(worker, "supervisor_alive", return_value=True),
    ):
        assert worker.status(folder)["observed_state"] == "UNKNOWN"
    # Wrong-model or merely open endpoints do not satisfy inference/schema readiness.
    with patch.object(
        worker.providers, "post", return_value=(200, '{"model":"wrong","choices":[]}')
    ):
        assert not worker.readiness(plan)
    with patch.object(
        worker.providers,
        "post",
        return_value=(
            200,
            '{"model":"synthetic/model","choices":[{"finish_reason":"stop","message":{"content":"{\\"ok\\":true}"}}]}',
        ),
    ):
        assert worker.readiness(plan)
    # Remote transport quotes arguments and preserves host-key checking; shell content is data.
    ssh = {**config["node"]["worker"], "transport": "ssh", "host": "approved-node"}
    command, options = transport.command(
        ssh, ["/path with spaces/python", "literal;rm -rf /", "$(false)"]
    )
    assert command[-1] == "'/path with spaces/python' 'literal;rm -rf /' '$(false)'"
    assert "-oStrictHostKeyChecking=yes" in command and options == {}
    remote_plan = copy.deepcopy(plan)
    remote_plan["config"]["node"]["worker"] = ssh
    remote_plan = changed(remote_plan)
    ready = {**state, "plan_hash": remote_plan["plan_hash"], "observed_state": "READY"}
    with (
        patch.object(transport, "request", return_value=ready),
        patch.object(deployments, "tunnel", return_value=False),
    ):
        assert (
            deployments.operate(directory, remote_plan, "status")["data"]["observed_state"]
            == "UNHEALTHY"
        )
    with (
        patch.object(
            transport, "request", return_value={**ready, "observed_state": "STOPPED"}
        ) as request,
        patch.object(deployments, "tunnel", side_effect=ValueError("lost socket")),
    ):
        stopped_state = deployments.operate(directory, remote_plan, "stop")
        assert stopped_state["data"]["observed_state"] == "STOPPED"
        assert "not confirmed" in stopped_state["data"]["reason"]
        assert request.call_args.args[2] == "stop"
    with patch.object(transport, "request", side_effect=ConnectionError):
        assert (
            deployments.operate(directory, remote_plan, "status")["data"]["observed_state"]
            == "UNKNOWN"
        )
    print(
        "PASS model inspection, exact plan authorization, budgets, revision/cache, "
        "ownership, restart, SSH/tunnel loss"
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="genesis-llm-") as temporary:
        checks(Path(temporary))
    print("PASS offline lifecycle; real local/SSH/GPU acceptance NOT RUN")


if __name__ == "__main__":
    main()
