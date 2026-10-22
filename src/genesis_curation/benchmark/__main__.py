"""Benchmark CLI. Planning never executes workflows or fetches inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .spec import load


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    reference = commands.add_parser(
        "reference", help="Inspect sequence identity or prepare a checksummed reference bundle"
    )
    reference_commands = reference.add_subparsers(dest="reference_command", required=True)
    inspect = reference_commands.add_parser(
        "inspect", help="Scan every FASTA base and hash contig sequences"
    )
    inspect.add_argument("fasta", type=Path)
    prepare = reference_commands.add_parser(
        "prepare", help="Verify a versioned reference panel entry"
    )
    prepare.add_argument("panel", type=Path)
    prepare.add_argument("--id", required=True)
    prepare.add_argument("--out", type=Path, required=True)
    sampling = commands.add_parser(
        "sample", help="Validate and count FASTQs; make nested seeded depth samples"
    )
    sampling.add_argument("--read1", type=Path, required=True)
    sampling.add_argument("--read2", type=Path)
    sampling.add_argument("--depth", type=int, action="append", required=True)
    sampling.add_argument("--seed", type=int, required=True)
    sampling.add_argument("--max-seconds", type=int, default=172800)
    sampling.add_argument("--out", type=Path, required=True)
    acquisition = commands.add_parser(
        "acquire", help="Verify publisher files and write a SHA256 asset lock"
    )
    acquisition.add_argument("manifest", type=Path)
    acquisition.add_argument("--out", type=Path, required=True)
    acquisition.add_argument("--connections", type=int, default=1, choices=range(1, 5))
    fixture = commands.add_parser("fixture")
    fixture.add_argument("--out", type=Path, required=True)
    fixture.add_argument(
        "--adapter",
        choices=["postalign", "genesis-atac", "nfcore-atac", "methods"],
        default="postalign",
    )
    fixture.add_argument("--checkout", type=Path)
    fixture.add_argument("--assay", choices=["DAP-seq", "bulk-ATAC"], default="DAP-seq")
    for name in ("plan", "fetch", "run"):
        command = commands.add_parser(name)
        command.add_argument("manifest", type=Path)
        if name == "run":
            command.add_argument("--out", type=Path, required=True)
            command.add_argument("--resume", action="store_true")
    for name in ("compare", "report"):
        command = commands.add_parser(name)
        command.add_argument("results", type=Path)
        if name == "compare":
            command.add_argument("--against", type=Path)
        else:
            command.add_argument(
                "--plot-python",
                type=Path,
                help="Optional isolated Python with Matplotlib; export SVG/PDF/PNG figures",
            )
    args = parser.parse_args()
    try:
        if args.command == "reference":
            from .references import inspect_fasta, prepare_reference

            value = (
                inspect_fasta(args.fasta)
                if args.reference_command == "inspect"
                else prepare_reference(args.panel, args.id, args.out)
            )
            print(json.dumps(value, indent=2))
        elif args.command == "sample":
            from .reads import sample

            print(
                sample(
                    [p for p in (args.read1, args.read2) if p is not None],
                    args.out,
                    depths=args.depth,
                    seed=args.seed,
                    seconds=args.max_seconds,
                )
            )
        elif args.command == "acquire":
            from .acquisition import acquire

            print(acquire(args.manifest, args.out, connections=args.connections))
        elif args.command == "fixture":
            from .fixture import create

            print(create(args.out, assay=args.assay, adapter=args.adapter, checkout=args.checkout))
        elif args.command == "plan":
            plan = load(args.manifest)
            print(json.dumps(plan, indent=2))
            if plan["blockers"]:
                raise SystemExit(2)
        elif args.command == "fetch":
            from .runtime import fetch

            fetch(load(args.manifest))
        elif args.command == "run":
            from .runner import run

            run(load(args.manifest), args.out, resume=args.resume)
        elif args.command == "compare" and args.against:
            from .reporting import compare_runs

            print(compare_runs(args.results, args.against))
        else:
            from .reporting import report

            report(args.results)
            if getattr(args, "plot_python", None):
                from .reporting import figures

                figures(args.results, args.plot_python)
    except (ValueError, OSError, EOFError, KeyError) as error:
        parser.exit(2, f"Benchmark error: {error}\n")


if __name__ == "__main__":
    main()
