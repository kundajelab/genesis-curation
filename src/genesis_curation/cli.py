"""Metadata-only application entry point."""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys

from . import installation
from .contracts.records import canonical
from .curation import cli as curation_cli
from .curation import metadata_cli
from .llm import cli as ai_cli
from .llm import evaluation
from .sheets import cli as sheets_cli
from .web import cli as web_cli


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plant metadata discovery, curation and review",
        epilog="Run 'genesis COMMAND --help' for the options of each command.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    groups = (
        ("web", web_cli, "Run the web server and the job worker"),
        ("metadata", metadata_cli, "Import or export metadata tables directly"),
        ("ai", ai_cli, "List and check model providers; manage local models"),
        ("curation", curation_cli, "Discover, curate, review and export (the GUI's operations)"),
        ("workspace", installation, "Create and maintain a runtime: account, providers, budget"),
        ("sheets", sheets_cli, "Build and score genesis-preprocessing sample sheets"),
        ("eval", evaluation, "Score the LLM steps against the curated gold sheets"),
    )
    for name, module, summary in groups:
        module.configure(commands.add_parser(name, help=summary, description=summary))
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
