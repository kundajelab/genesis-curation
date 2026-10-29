"""Metadata-only application entry point."""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys

from .contracts.records import canonical
from .curation import metadata_cli
from .llm import cli as ai_cli
from .web import cli as web_cli


def main() -> None:
    parser = argparse.ArgumentParser(description="Plant metadata discovery, curation and review")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, module in (("web", web_cli), ("metadata", metadata_cli), ("ai", ai_cli)):
        module.configure(commands.add_parser(name))
    args = parser.parse_args()
    try:
        result, code = args.curation_handler(args)
        print(result if isinstance(result, str) else canonical(result))
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        sqlite3.Error,
        subprocess.SubprocessError,
    ) as error:
        print(canonical({"error": str(error), "operation": args.operation}), file=sys.stderr)
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
