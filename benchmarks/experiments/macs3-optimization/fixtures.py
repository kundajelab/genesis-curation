"""Bounded real-tool BEDPE and failure regressions for the pinned MACS3 update."""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
from pathlib import Path

from compare import REPO, scientific_outputs


def main(args: argparse.Namespace) -> int:
    args.out.mkdir(parents=True, exist_ok=False)
    rng = random.Random(217)
    enriched = args.out / "enriched.bedpe"
    rows = []
    for chrom in ("chr1", "chr2"):
        for center in (2000, 5000, 8000):
            for _ in range(800):
                start = center + rng.randrange(-80, 81)
                rows.append(f"{chrom}\t{start}\t{start + 100}\n")
        for _ in range(500):
            start = rng.randrange(1, 9900)
            rows.append(f"{chrom}\t{start}\t{start + 100}\n")
    enriched.write_text("".join(rows))
    sparse = args.out / "sparse.bedpe"
    sparse.write_text("chr1\t100\t200\nchr2\t100\t200\n")
    malformed = args.out / "malformed.bedpe"
    malformed.write_text("chr1\tnot-a-coordinate\t200\n")
    empty = args.out / "empty.bedpe"
    empty.write_text("")
    truncated = args.out / "truncated.bedpe"
    truncated.write_text("chr1\t100\n")
    cases = [
        ("enriched-all", enriched, "all"),
        ("enriched-one", enriched, "1"),
        ("sparse", sparse, "all"),
        ("legacy-numeric-coercion", malformed, "all"),
        ("truncated", truncated, "all"),
        ("empty", empty, "all"),
    ]
    results = []
    for name, source, dup in cases:
        observed = []
        for label in ("baseline", "candidate"):
            python = getattr(args, label).absolute()
            folder = args.out / (name + "-" + label)
            folder.mkdir()
            argv = [
                str(python),
                str(REPO / "src/genesis_curation/macs3_launch.py"),
                "--cpus",
                "2",
            ]
            if label == "candidate":
                argv += ["--require-source", "22eb40f7b51f5b20b635614ca0b1759dc5398c5c"]
            argv += [
                "--",
                "callpeak",
                "-t",
                str(source),
                "-f",
                "BEDPE",
                "-g",
                "20000",
                "-q",
                "0.01",
                "--keep-dup",
                dup,
                "-n",
                "result",
            ]
            env = dict(
                os.environ,
                PATH=str(python.parent) + os.pathsep + os.environ["PATH"],
                TMPDIR=str(folder),
            )
            with (folder / "stdout").open("w") as out, (folder / "stderr").open("w") as err:
                process = subprocess.run(
                    argv, cwd=folder, env=env, stdout=out, stderr=err, timeout=60
                )
            observed.append(
                {
                    "version": label,
                    "exit": process.returncode,
                    "outputs": scientific_outputs(folder),
                }
            )
        expected_failure = name in ("truncated", "empty")
        passed = (
            observed[0]["exit"] == observed[1]["exit"]
            and observed[0]["outputs"] == observed[1]["outputs"]
        )
        passed = passed and (
            (observed[0]["exit"] != 0) if expected_failure else (observed[0]["exit"] == 0)
        )
        results.append(
            {
                "case": name,
                "pass": passed,
                "runs": observed,
                "limitation": (
                    "Both upstream versions use atoi: nonnumeric coordinates become zero. "
                    "This is an equivalence test, not input-validation acceptance."
                )
                if name == "legacy-numeric-coercion"
                else None,
            }
        )
    (args.out / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps([{k: v for k, v in row.items() if k != "runs"} for row in results]))
    return int(not all(r["pass"] for r in results))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("baseline", "candidate", "out"):
        parser.add_argument("--" + flag, type=Path, required=True)
    sys.exit(main(parser.parse_args()))
