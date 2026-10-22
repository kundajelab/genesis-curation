"""Explicit opt-in live API/private-endpoint probe; never part of pixi run checks."""

from __future__ import annotations

import argparse
from pathlib import Path

from genesis_curation.contracts.records import canonical, dump
from genesis_curation.llm.providers import InferenceFailure, probe, select
from genesis_curation.registry import store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--authorize-egress", choices=("public",), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = select(args.config, args.provider)
    if config["mode"] != "live":
        raise ValueError("Live acceptance cannot be established by a mock/disabled provider")
    store.initialize(args.registry)
    # Acceptance only sends the fixed public probe and tightens, never increases, budgets.
    config = {
        **config,
        "max_tokens": min(config["max_tokens"], 32),
        "timeout_seconds": min(config["timeout_seconds"], 10),
        "max_retries": 0,
        "format_repair": False,
    }
    try:
        evidence = probe(args.registry, config)
        status = (
            "FAIL"
            if any(evidence.get(name) == "FAIL" for name in ("tools", "streaming"))
            else "PASS"
        )
    except InferenceFailure as error:
        evidence, status = error.invocation, "FAIL"
    result = {"status": status, "mode": "live", "public_probe_only": True, "evidence": evidence}
    dump(args.output, result, immutable=True)
    print(canonical(result))
    raise SystemExit(0 if status == "PASS" else 1)


if __name__ == "__main__":
    main()
