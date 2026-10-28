"""Expose scientific launchers without changing their arguments or runtime."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

COMMANDS = {
    "benchmark": "Plan, run and compare benchmark experiments",
}
TOOLS = ("nextflow", "genesis-tools", "uv", "micromamba", "docker", "apptainer", "sbatch", "ssh")


def checkout() -> Path:
    """The genesis-preprocessing checkout that holds the Nextflow workflows (GENESIS_REPO)."""
    configured = os.environ.get("GENESIS_REPO")
    if not configured:
        raise ValueError(
            "Set GENESIS_REPO to a genesis-preprocessing checkout; the pipelines live there, "
            "not in genesis-curation."
        )
    root = Path(configured).expanduser().resolve()
    required = (
        "pixi.toml",
        "pixi.lock",
        "workflows/dap-seq.nf",
        "genesis_tools/pyproject.toml",
    )
    if not all((root / name).is_file() for name in required):
        raise ValueError(
            f"Genesis workflow checkout not found at {root}. Clone genesis-preprocessing, run "
            "`pixi run install-all` there, and set GENESIS_REPO to that checkout."
        )
    manifest = tomllib.loads((root / "pixi.toml").read_text())
    if manifest.get("workspace", {}).get("name") != "genesis":
        raise ValueError(f"Not a Genesis Pixi workspace: {root}")
    return root


def python(root: Path) -> Path:
    executable = root / "genesis_tools/.venv/bin/python"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError(
            f"Missing Genesis Python environment. Run `pixi run install-all` in {root}."
        )
    return executable


def managed_command(root: Path, command: list[str]) -> list[str]:
    pixi = shutil.which("pixi")
    if pixi is None:
        raise ValueError(
            "Pixi is required for scientific execution. Install Pixi and put it on PATH; "
            "then run `pixi run install-all` in the Genesis checkout."
        )
    # Pixi --as-is can execute host binaries even when its prefix is missing.
    # Discover the configured prefix (including detached environments) and fail
    # before any scientific command rather than falling back to host tools.
    info = subprocess.run(
        [pixi, "info", "--manifest-path", str(root / "pixi.toml"), "--json"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if info.returncode:
        raise ValueError(f"Cannot inspect Pixi runtime: {info.stderr.strip()[-2000:]}")
    details = json.loads(info.stdout)
    environments = details.get("environments_info", []) if isinstance(details, dict) else []
    prefix = None
    if isinstance(environments, list):
        for item in environments:
            if isinstance(item, dict) and item.get("name") == "default":
                prefix = item.get("prefix")
    if not isinstance(prefix, str) or not Path(prefix).is_absolute():
        raise ValueError("Pixi did not report an absolute prefix for the default environment.")
    installed = Path(prefix)
    if not (installed / "conda-meta").is_dir() or any(
        not (installed / "bin" / name).is_file() or not os.access(installed / "bin" / name, os.X_OK)
        for name in ("nextflow", "uv", "micromamba")
    ):
        raise ValueError(
            f"Missing or incomplete Pixi environment at {installed}. "
            f"Run `pixi run install-all` in {root}."
        )
    # --as-is never updates locks or installs environments. --executable bypasses task
    # lookup (and its checkout-relative cwd); argv and the caller's cwd stay intact.
    return [
        pixi,
        "run",
        "--as-is",
        "--manifest-path",
        str(root / "pixi.toml"),
        "--executable",
        *command,
    ]


def environment(root: Path) -> dict[str, str]:
    # Local Nextflow tasks also invoke the installed genesis-tools helpers. An
    # entry-point symlink selects Python but does not activate its bin directory.
    return {**os.environ, "PATH": f"{python(root).parent}{os.pathsep}{os.environ.get('PATH', '')}"}


def command(action: str, arguments: list[str]) -> list[str]:
    if action not in COMMANDS:
        raise ValueError(f"Unknown scientific command: {action}")
    return managed_command(
        checkout(), [sys.executable, "-m", "genesis_curation.benchmark", *arguments]
    )


def configure(commands: argparse._SubParsersAction) -> None:
    for name, help_text in COMMANDS.items():
        child = commands.add_parser(name, help=help_text, add_help=False)
        child.add_argument("arguments", nargs=argparse.REMAINDER)


def execute(action: str, arguments: list[str]) -> None:
    env = dict(os.environ)
    # Help does not need a scientific environment or start Pixi; the benchmark parser
    # ships in standalone wheels.
    if "--help" in arguments or "-h" in arguments:
        argv = [sys.executable, "-m", "genesis_curation.benchmark", *arguments]
    elif action == "benchmark" and arguments and arguments[0] == "reference":
        # FASTA/annotation validation is pure Python and ships in the wheel.
        # It does not launch scientific tools or require Nextflow resources.
        argv = [sys.executable, "-m", "genesis_curation.benchmark", *arguments]
    else:
        argv = command(action, arguments)
        env = environment(checkout())
    # Replacing the CLI process preserves normal signal delivery and exit status.
    os.execve(argv[0], argv, env)


def inspect_runtime() -> dict[str, object]:
    """Bounded, read-only diagnosis, including tools inside the managed environment."""
    result: dict[str, object] = {"python": sys.executable, "pixi": shutil.which("pixi")}
    try:
        root = checkout()
        result["checkout"] = str(root)
        git = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        result["git_sha"] = git.stdout.strip() if git.returncode == 0 else None
        probe = managed_command(
            root,
            [
                str(python(root)),
                "-c",
                "import json,shutil; print(json.dumps({n:shutil.which(n) for n in "
                f"{TOOLS!r}" + "}))",
            ],
        )
        completed = subprocess.run(
            probe, env=environment(root), capture_output=True, text=True, check=False, timeout=20
        )
        if completed.returncode:
            raise ValueError(
                f"Managed runtime unavailable; run `pixi run install-all` in {root}. "
                + completed.stderr.strip()[-2000:]
            )
        result["tools"] = json.loads(completed.stdout)
        result["status"] = "available"
        if completed.stderr.strip():
            result["diagnostics"] = completed.stderr.strip()[-2000:]
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        result["status"] = "unavailable"
        result["error"] = str(error)
    return result
