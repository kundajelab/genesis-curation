"""Private, relocatable installation setup and explicitly scoped housekeeping."""

from __future__ import annotations

import argparse
import fcntl
import getpass
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from .contracts.records import load
from .llm import providers
from .llm.security import credential
from .web import runtime, storage


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, required=True, help="Runtime directory")
    parser.set_defaults(curation_handler=execute)
    commands = parser.add_subparsers(dest="operation", required=True)
    init = commands.add_parser("init", help="Create the runtime and its curator account")
    init.add_argument("--username", default="curator")
    init.add_argument("--password-env")
    commands.add_parser("doctor", help="Check the runtime and its configuration")
    clean = commands.add_parser(
        "clean", help="Preview (or --apply) removal of cache and temporary files"
    )
    clean.add_argument("--apply", action="store_true")
    provider = commands.add_parser(
        "provider", help="Add or update a model provider (the key is prompted for)"
    )
    provider.add_argument("id")
    provider.add_argument("--model", required=True)
    provider.add_argument(
        "--protocol",
        choices=("anthropic-messages", "openai-chat", "openai-responses"),
        required=True,
    )
    provider.add_argument("--endpoint")
    provider.add_argument("--local", action="store_true")
    provider.add_argument("--key-env")
    provider.add_argument("--default", action="store_true")
    provider.add_argument("--context", type=int, default=32768)
    provider.add_argument("--max-output", type=int, default=4096)
    provider.add_argument("--input-price", type=float)
    provider.add_argument("--output-price", type=float)
    budget = commands.add_parser("budget", help="Set the model spending limit")
    budget.add_argument("--limit-usd", type=float, required=True)


def private_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(".new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(value, out, indent=2)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)


def initialize(root: Path, username: str, password: str) -> None:
    if len(password) < 14 or not username.strip():
        raise ValueError("Use a curator name and password of at least 14 characters")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    if (root / "state/app.sqlite").exists():
        raise ValueError("Workspace already initialized")
    for name in (
        "config/secrets",
        "state",
        "evidence",
        "exports",
        "skills",
        "models",
        "cache",
        "logs",
        "tmp",
    ):
        (root / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, target in (
        ("source-snapshots", "evidence"),
        ("exports", "exports"),
        ("skills", "skills"),
    ):
        (root / "state" / name).symlink_to(Path("..") / target, target_is_directory=True)
    runtime.initialize(root / "state", username, password)
    private_json(
        root / "config/providers.json",
        {"schema_version": 1, "providers": [], "default_provider": None},
    )


def execute(args: argparse.Namespace) -> tuple[Any, int]:
    root = args.root.resolve()
    if root == Path("/") or root == Path.home():
        raise ValueError("Choose a dedicated runtime subdirectory")
    if args.operation == "init":
        password = (
            os.environ.get(args.password_env, "")
            if args.password_env
            else getpass.getpass("Curator password (14+ characters): ")
        )
        initialize(root, args.username, password)
        return {"root": str(root), "initialized": True}, 0
    if not (root / "state/app.sqlite").is_file():
        raise ValueError("Initialize this runtime first")
    config = root / "config/providers.json"
    if args.operation == "doctor":
        configured = providers.configurations(config)
        checks = []
        for item in configured:
            ready = True
            if item.get("credential"):
                try:
                    credential(item["credential"])
                except OSError, ValueError:
                    ready = False
            checks.append(
                {
                    "id": item["id"],
                    "model": item["model"],
                    "credential_ready": ready,
                    "live_inference": "NOT_RUN",
                }
            )
        return {
            "root": str(root),
            "storage": storage.status(root / "state"),
            "providers": checks,
            "default_provider": providers.default_id(config),
        }, 0 if all(c["credential_ready"] for c in checks) else 3
    if args.operation == "budget":
        import math

        if not math.isfinite(args.limit_usd) or args.limit_usd <= 0:
            raise ValueError("Use a positive finite budget")
        path = root / "state/spending.json"
        with (root / "state/spending.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            value = load(path) if path.exists() else {"reserved_usd": 0, "reservations": []}
            if args.limit_usd < value["reserved_usd"]:
                raise ValueError("Limit is below existing reservations")
            value["limit_usd"] = args.limit_usd
            private_json(path, value)
        return {"limit_usd": args.limit_usd, "reserved_usd": value["reserved_usd"]}, 0
    if args.operation == "clean":
        with (root / "state/worker.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Stop the worker before cleaning") from None
            paths = []
            for name in ("cache", "tmp"):
                directory = root / name
                if directory.is_symlink():
                    raise ValueError("Refusing to clean a symlink")
                paths.extend(directory.iterdir())
            size = sum(
                p.stat().st_size
                for parent in paths
                for p in ([parent] if parent.is_file() else parent.rglob("*"))
                if p.is_file() and not p.is_symlink()
            )
            if args.apply:
                for p in paths:
                    if p.is_symlink() or p.is_file():
                        p.unlink()
                    else:
                        shutil.rmtree(p)
            return {"applied": args.apply, "paths": [str(p) for p in paths], "bytes": size}, 0
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", args.id):
        raise ValueError("Use a simple provider ID")
    endpoint = args.endpoint or (
        "https://api.anthropic.com/v1" if args.protocol == "anthropic-messages" else None
    )
    if endpoint is None:
        raise ValueError("Specify --endpoint")
    key = None
    secret = ""
    path = root / "config/secrets" / (args.id + ".key")
    if args.key_env or not args.local:
        secret = os.environ.get(args.key_env, "") if args.key_env else getpass.getpass("API key: ")
        if not secret or any(c in secret for c in "\r\n\x00"):
            raise ValueError("Invalid key")
        path = root / "config/secrets" / (args.id + ".key")
        key = {"file": str(path)}
    item = {
        "id": args.id,
        "mode": "live",
        "endpoint": endpoint,
        "model": args.model,
        "protocol": args.protocol,
        "model_revision": args.model,
        "serving_revision": None,
        "external": not args.local,
        "credential": key,
        "allowed_tasks": ["probe", "metadata", "diagnose", "ask"],
        "allowed_classifications": ["public"]
        if not args.local
        else ["public", "internal", "local-only"],
        "context_chars": args.context * 4,
        "context_window_tokens": args.context,
        "max_input_tokens": args.context,
        "max_tokens": args.max_output,
        "timeout_seconds": 180,
        "max_retries": 1,
        "concurrency": 1,
        "format_repair": False,
        "capabilities": {"json_schema": True, "tools": False, "streaming": False},
        "mock_response": None,
        "token_counter": args.protocol
        if args.protocol in {"anthropic-messages", "openai-responses"}
        else "conservative",
    }
    if args.input_price is not None and args.output_price is not None:
        item["pricing"] = {
            "input_per_million": args.input_price,
            "output_per_million": args.output_price,
        }
    value = load(config)
    value["providers"] = [p for p in value["providers"] if p["id"] != args.id] + [item]
    if args.default:
        value["default_provider"] = args.id
    from .llm.security import check, schema
    from .llm.security import endpoint as validate_endpoint

    check(value, schema("providers-v1"))
    validate_endpoint(endpoint, external=not args.local)
    if key is not None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(secret + "\n")
        path.chmod(0o600)
    private_json(config, value)
    return {
        "provider": args.id,
        "model": args.model,
        "default": value.get("default_provider") == args.id,
    }, 0
