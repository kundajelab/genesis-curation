"""Explicit opt-in real local/SSH deployment acceptance with owned-service cleanup."""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

from genesis_curation.contracts.records import canonical, dump, load
from genesis_curation.llm import deployments, providers, service_worker
from genesis_curation.registry import store


def accept(
    registry: Path, plan: dict[str, Any], authorization: str, config: dict[str, Any], timeout: float
) -> dict[str, Any]:
    service_worker.verify_plan(plan)
    if not 1 <= timeout <= 3600:
        raise ValueError("Live acceptance timeout must be 1–3600 seconds")
    expected_port = (
        plan["config"]["controller_port"]
        if plan["config"]["node"]["worker"]["transport"] == "ssh"
        else plan["config"]["port"]
    )
    if (
        config["mode"] != "live"
        or config["external"]
        or config["endpoint"] != f"http://127.0.0.1:{expected_port}/v1"
        or config["model"] != plan["model"]["data"]["model_id"]
        or config["model_revision"] != plan["model"]["data"]["revision"]
    ):
        raise ValueError("Provider must describe this exact private loopback deployment/revision")
    if authorization != plan["plan_hash"]:
        raise ValueError("Explicit authorization of the reviewed plan is required")
    store.initialize(registry)
    observations = []
    result: dict[str, Any] = {
        "status": "FAIL",
        "plan_hash": plan["plan_hash"],
        "transport": plan["config"]["node"]["worker"]["transport"],
        "observations": observations,
        "probe": None,
        "cleanup": None,
    }
    started = time.monotonic()
    try:
        observations.append(deployments.apply(registry, plan, authorization))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            observed = deployments.operate(registry, plan, "status")
            observations.append(observed)
            if observed["data"]["observed_state"] == "READY":
                limited = {
                    **config,
                    "max_tokens": min(config["max_tokens"], 32),
                    "timeout_seconds": min(config["timeout_seconds"], 10),
                    "max_retries": 0,
                }
                result["probe"] = providers.probe(registry, limited)
                result["status"] = (
                    "PASS"
                    if all(result["probe"].get(name) != "FAIL" for name in ("tools", "streaming"))
                    else "FAIL"
                )
                break
            if observed["data"]["observed_state"] in {"FAILED", "STOPPED"}:
                break
            time.sleep(2)
    except (ValueError, OSError) as error:
        result["error"] = str(error)
    finally:
        result["cleanup"] = deployments.operate(registry, plan, "stop")
        deadline = time.monotonic() + 60
        while (
            result["cleanup"]["data"]["observed_state"] == "STOPPING"
            and time.monotonic() < deadline
        ):
            time.sleep(2)
            result["cleanup"] = deployments.operate(registry, plan, "status")
        if (
            result["cleanup"]["data"]["observed_state"] != "STOPPED"
            or "tunnel stop was not confirmed" in result["cleanup"]["data"]["reason"]
        ):
            result["status"] = "FAIL"
    result["seconds"] = time.monotonic() - started
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--authorize", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = accept(
        args.registry,
        load(args.plan),
        args.authorize,
        providers.select(args.config, args.provider),
        args.timeout,
    )
    dump(args.output, result, immutable=True)
    print(canonical(result))
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
