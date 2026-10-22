#!/usr/bin/env python3
"""Run MACS3 within a CPU allocation; usable without installing Genesis.

Linux applies CPU affinity. MACS3 builds with a CPU budget (MACS3_NUM_THREADS / --threads,
added upstream in macs3-project/MACS#787) also bound their own pools, on any OS.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import sys
import sysconfig
from pathlib import Path

SOURCE_REVISION = "22eb40f7b51f5b20b635614ca0b1759dc5398c5c"
SOURCE_SHA256 = "791cc59bd1daa290ccaa53f2552301b293db8cff44ddaa208ef6b8a1a7fb7857"
THREAD_VARIABLES = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "BLIS_NUM_THREADS",
)
# Present in MACS3 builds whose pools honor one CPU budget per invocation.
BUDGET_MODULE = "MACS3.Utilities.Execution"


def constrain(cpus: int) -> list[int]:
    if cpus < 1:
        raise ValueError("CPU allocation must be positive")
    if not hasattr(os, "sched_getaffinity"):
        raise ValueError("Bounded MACS3 execution requires Linux CPU affinity")
    allowed = sorted(os.sched_getaffinity(0))
    # Spread independent local tasks within the inherited allocation. This is
    # not a reservation; a scheduler-provided cpuset remains the outer boundary.
    offset = os.getpid() % len(allowed) if allowed else 0
    selected = sorted((allowed[offset:] + allowed[:offset])[:cpus])
    if not selected:
        raise ValueError("Empty CPU affinity mask")
    os.sched_setaffinity(0, selected)
    for name in THREAD_VARIABLES:
        os.environ[name] = "1"
    return selected


def thread_budget() -> bool:
    """Whether the installed MACS3 enforces a CPU budget itself."""
    try:
        return importlib.util.find_spec(BUDGET_MODULE) is not None
    except ModuleNotFoundError:
        return False


def explicit_threads(arguments: list[str]) -> int | None:
    """Return an explicit CPU budget from MACS3 arguments, if any.

    callvar also spells it -m/--multiple-processing; elsewhere -m means something else
    (e.g. callpeak --mfold). MACS3's argparse accepts abbreviated long options and keeps the
    last of repeated ones, so every spelling counts, and more than one is refused rather than
    validating a value MACS3 would not use.
    """
    options = ["--threads"]
    if arguments[:1] == ["callvar"]:
        options.append("--multiple-processing")
    values = []
    for index, argument in enumerate(arguments):
        name, equals, value = argument.partition("=")
        if name.startswith("--") and len(name) > 2 and any(o.startswith(name) for o in options):
            # An abbreviation is either the budget option or ambiguous, which MACS3 rejects.
            if not equals:
                value = arguments[index + 1] if index + 1 < len(arguments) else ""
            values.append(value)
        elif options[1:] and argument.startswith("-m") and not argument.startswith("--"):
            values.append(
                argument[2:] or (arguments[index + 1] if index + 1 < len(arguments) else "")
            )
    if len(values) > 1:
        raise ValueError("MACS3 CPU budget (--threads) may be given only once")
    if not values:
        return None
    if not values[0].isdecimal():
        raise ValueError("MACS3 --threads must be a positive integer")
    return int(values[0])


def executable() -> str:
    path = Path(sysconfig.get_path("scripts")) / "macs3"
    if not path.is_file():
        raise ValueError("macs3 is not installed alongside this Python environment")
    return str(path)


def identity() -> dict[str, object]:
    distribution = importlib.metadata.distribution("MACS3")
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    digest = direct.get("archive_info", {}).get("hashes", {}).get("sha256")
    revision = direct.get("vcs_info", {}).get("commit_id")
    return {
        "version": distribution.version,
        "source_revision": SOURCE_REVISION if digest == SOURCE_SHA256 else revision,
        "source_archive_sha256": digest,
        "launcher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpus", type=int, required=True)
    parser.add_argument("--receipt", type=Path, default=Path("macs3.runtime.json"))
    parser.add_argument("--require-source", help="Require this exact upstream Git revision")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.arguments
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    if not arguments:
        parser.error("MACS3 arguments are required after --")
    try:
        program = executable()
        observed = identity()
        if args.require_source and observed["source_revision"] != args.require_source:
            raise ValueError("Installed MACS3 source revision does not match the required revision")
        budget = thread_budget()
        if hasattr(os, "sched_getaffinity"):
            selected = constrain(args.cpus)
        elif budget or observed["source_revision"] is None:
            # Without affinity, a budget-aware MACS3 bounds its own pools. Released packages
            # without one keep running unbounded, as before, and are recorded as unassessed.
            if args.cpus < 1:
                raise ValueError("CPU allocation must be positive")
            selected = []
            if budget:
                for name in THREAD_VARIABLES:
                    os.environ[name] = "1"
        else:
            raise ValueError(
                "This MACS3 source build has no CPU budget; bounded execution requires "
                "Linux CPU affinity"
            )
        if budget:
            requested = explicit_threads(arguments)
            if requested is not None and not 1 <= requested <= args.cpus:
                raise ValueError("MACS3 --threads must be between 1 and the CPU allocation")
            # An explicit --threads takes precedence over this; otherwise it is MACS3's budget.
            os.environ["MACS3_NUM_THREADS"] = str(args.cpus)
        with args.receipt.open("x") as stream:
            json.dump(
                observed
                | {
                    "requested_cpus": args.cpus,
                    "affinity": selected,
                    "macs3_thread_budget": args.cpus if budget else None,
                    "native_library_threads": 1 if selected or budget else None,
                    "resource_enforcement": "PASS" if selected or budget else "NOT_ASSESSED",
                },
                stream,
                indent=2,
            )
            stream.write("\n")
        os.execvpe(program, [program, *arguments], os.environ)
    except (OSError, ValueError, importlib.metadata.PackageNotFoundError) as error:
        print(f"MACS3 launch failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
