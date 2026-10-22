"""Small registration/error boundary shared by the delegated curation commands."""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys

from ..contracts import cli as validation_cli
from ..contracts.records import canonical
from ..llm import cli as ai_cli
from ..llm.providers import InferenceFailure
from ..qc_policy import cli as qc_cli
from ..registry import cli as registry_cli
from ..scatac import cli as scatac_cli
from ..study import cli as study_cli
from . import cli as review_cli
from . import metadata_cli


def configure(commands: argparse._SubParsersAction) -> None:
    for name, module in (
        ("validate", validation_cli),
        ("registry", registry_cli),
        ("qc", qc_cli),
        ("review", review_cli),
        ("metadata", metadata_cli),
        ("ai", ai_cli),
        ("study", study_cli),
        ("scatac", scatac_cli),
    ):
        module.configure(commands.add_parser(name))


def execute(args: argparse.Namespace) -> None:
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
        failure = {"error": str(error), "operation": args.operation}
        if isinstance(error, InferenceFailure):
            failure["invocation_version"] = error.invocation["version"]
        print(canonical(failure), file=sys.stderr)
        code = 2
    raise SystemExit(code)
