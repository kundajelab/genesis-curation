"""AI commands delegated by Genesis; all operational actions are explicit."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..contracts.records import dump, load
from . import assistant, deployments, diagnostics, models, providers


def inference_options(parser: argparse.ArgumentParser, *, required: bool = False) -> None:
    parser.add_argument("--config", type=Path, required=required)
    parser.add_argument("--provider", required=required)


def configure(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="ai_family", required=True)
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
    for name in ("ask", "diagnose"):
        child = commands.add_parser(name)
        child.set_defaults(curation_handler=execute, operation=name)
        child.add_argument("--json", action="store_true")
        child.add_argument("directory", type=Path)
        inference_options(child, required=name == "ask")
        child.add_argument(
            "--classification", choices=("public", "internal", "local-only"), default="local-only"
        )
        if name == "ask":
            child.add_argument("question")
            child.add_argument("--dataset", required=True, action="append")
        else:
            child.add_argument("--root", type=Path, required=True)
            child.add_argument("--log", required=True, action="append")
            child.add_argument("--facts", type=Path)
            child.add_argument("--dataset")
            child.add_argument("--worker-config", type=Path)
            child.add_argument("--worker-id", default="local")
            child.add_argument("--output", type=Path)


def execute(args: argparse.Namespace) -> tuple[Any, int]:
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
    if bool(args.config) != bool(args.provider):
        raise ValueError("Use both --config and --provider for optional inference")
    config = providers.select(args.config, args.provider) if args.config else None
    if args.ai_family == "ask":
        if config is None:
            raise ValueError("An explicit provider is required")
        return assistant.ask(
            args.directory, config, args.question, args.dataset, args.classification
        ), 0
    bundle = (
        diagnostics.remote(load(args.worker_config), args.root, args.log, args.worker_id)
        if args.worker_config
        else diagnostics.collect(args.root, args.log, args.worker_id)
    )
    result = diagnostics.diagnose(
        args.directory,
        bundle,
        facts=load(args.facts) if args.facts else {},
        config=config,
        dataset=args.dataset,
        classification=args.classification,
    )
    if args.output:
        dump(args.output, result, immutable=True)
    return result, 0
