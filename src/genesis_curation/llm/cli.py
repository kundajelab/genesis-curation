"""AI commands delegated by Genesis; all operational actions are explicit."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from . import local_models, providers


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
    serve = local.add_parser("serve")
    serve.add_argument("--root", type=Path, required=True, help="Runtime directory")
    serve.set_defaults(curation_handler=execute)


def execute(args: argparse.Namespace) -> tuple[Any, int]:
    if args.ai_family == "local-model":
        if args.operation == "serve":
            import fcntl

            from .model_controller import Controller

            with (args.root / "state/model-controller.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                Controller(args.root).run()
            return {"stopped": True}, 0
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
    raise ValueError("Unknown model command")
