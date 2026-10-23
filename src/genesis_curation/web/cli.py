"""Explicit local account, worker and web service lifecycle."""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
from typing import Any

from . import runtime, worker


def configure(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("init", "reset-password", "serve", "worker"):
        child = commands.add_parser(name)
        child.add_argument("--root", type=Path, required=True)
        child.set_defaults(curation_handler=execute)
        if name in {"init", "reset-password"}:
            child.add_argument("--username", default="curator")
            child.add_argument(
                "--password-env", help="Provisioned environment variable; otherwise prompt"
            )
        else:
            child.add_argument("--providers", type=Path, required=True)
        if name == "serve":
            child.add_argument("--origin", required=True)
            child.add_argument("--port", type=int, default=18765)
            child.add_argument(
                "--allow-insecure-http",
                action="store_true",
                help="Explicit public HTTP testing; traffic is unencrypted",
            )
            child.add_argument("--dev", action="store_true", help="Loopback HTTP development only")


def execute(args: argparse.Namespace) -> tuple[Any, int]:
    if args.operation in {"init", "reset-password"}:
        password = (
            os.environ.get(args.password_env, "")
            if args.password_env
            else getpass.getpass("Curator password: ")
        )
        action = runtime.initialize if args.operation == "init" else runtime.reset
        action(args.root, args.username, password)
        return {"account": args.username, "root": str(args.root)}, 0
    if args.operation == "worker":
        worker.run(args.root, args.providers)
    else:
        import uvicorn

        from .api import create_app

        if args.dev and not args.origin.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise ValueError("Development mode requires a loopback origin")
        uvicorn.run(
            create_app(
                args.root,
                args.providers,
                args.origin,
                dev=args.dev,
                allow_insecure_http=args.allow_insecure_http,
            ),
            host="127.0.0.1",
            port=args.port,
            proxy_headers=True,
            forwarded_allow_ips="127.0.0.1",
        )
    return {"stopped": True}, 0
