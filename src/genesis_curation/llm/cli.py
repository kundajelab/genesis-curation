"""AI commands delegated by Genesis; all operational actions are explicit."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..contracts.records import dump, load
from . import deployments, local_models, models, providers


def inference_options(parser: argparse.ArgumentParser, *, required: bool = False) -> None:
    parser.add_argument("--config", type=Path, required=required)
    parser.add_argument("--provider", required=required)


def configure(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="ai_family", required=True)
    local = commands.add_parser("local-model").add_subparsers(dest="operation", required=True)
    for name in ("list", "download"):
        child = local.add_parser(name)
        child.set_defaults(curation_handler=execute)
        child.add_argument("--json", action="store_true")
        if name == "download":
            child.add_argument("preset", choices=[p["id"] for p in local_models.PRESETS])
            child.add_argument("--directory", type=Path, required=True)
    provider = commands.add_parser("providers").add_subparsers(dest="operation", required=True)
    for name in ("list", "check"):
        child = provider.add_parser(name)
        child.set_defaults(curation_handler=execute)
        child.add_argument("--json", action="store_true")
        child.add_argument("--config", type=Path, required=True)
        if name == "check":
            child.add_argument("directory", type=Path)
            child.add_argument("--provider", required=True)
    model = (
        commands.add_parser("model")
        .add_subparsers(dest="operation", required=True)
        .add_parser("inspect")
    )
    model.set_defaults(curation_handler=execute)
    model.add_argument("--json", action="store_true")
    model.add_argument("model_id")
    model.add_argument("--revision", required=True)
    model.add_argument("--token-env", help="Existing controller-side HF credential variable")
    model.add_argument("--snapshot", type=Path, help="Offline captured Hub metadata; no network")
    model.add_argument("--output", type=Path, required=True)
    deployment = commands.add_parser("deployment").add_subparsers(dest="operation", required=True)
    for name in ("plan", "apply", "status", "logs", "stop"):
        child = deployment.add_parser(name)
        child.set_defaults(curation_handler=execute)
        child.add_argument("--json", action="store_true")
        if name == "plan":
            child.add_argument("--config", type=Path, required=True)
            child.add_argument("--model", type=Path, required=True)
            child.add_argument(
                "--observed",
                type=Path,
                help="Offline inspection snapshot; apply always rechecks the node",
            )
            child.add_argument("--output", required=True, type=Path)
        else:
            child.add_argument("directory", type=Path)
            child.add_argument("--plan", required=True, type=Path)
            if name == "apply":
                child.add_argument("--authorize", required=True, help="Exact reviewed plan_hash")


def execute(args: argparse.Namespace) -> tuple[Any, int]:
    if args.ai_family == "local-model":
        if args.operation == "list":
            return {"models": local_models.catalog()}, 0
        return local_models.download(args.preset, args.directory), 0
    if args.ai_family == "providers":
        if args.operation == "list":
            return {
                "providers": [
                    {
                        key: value
                        for key, value in item.items()
                        if key not in {"credential", "mock_response"}
                    }
                    for item in providers.configurations(args.config)
                ]
            }, 0
        result = providers.probe(args.directory, providers.select(args.config, args.provider))
        return result, 3 if any(
            result.get(name) == "FAIL" for name in ("tools", "streaming")
        ) else 0
    if args.ai_family == "model":
        result = models.inspect(
            args.model_id,
            args.revision,
            load(args.snapshot) if args.snapshot else None,
            args.token_env,
        )
        dump(args.output, result, immutable=True)
        return result, 0
    if args.ai_family == "deployment":
        if args.operation == "plan":
            result = deployments.plan(
                load(args.config), load(args.model), load(args.observed) if args.observed else None
            )
            dump(args.output, result, immutable=True)
            return result, 0
        result = (
            deployments.apply(args.directory, load(args.plan), args.authorize)
            if args.operation == "apply"
            else deployments.operate(args.directory, load(args.plan), args.operation)
        )
        return result, 0 if result.get("data", {}).get("observed_state") not in {
            "UNKNOWN",
            "FAILED",
            "UNHEALTHY",
        } else 3
    raise ValueError("Unknown model command")
