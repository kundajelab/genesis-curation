"""Render completed paired MACS3 timings without exposing local filesystem paths."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
from pathlib import Path


def public_case(case: dict) -> dict:
    aliases = {
        path: f"input-{index}{Path(path).suffix}" for index, path in enumerate(case["inputs"])
    }
    return {
        "id": case["id"],
        "scope": case["scope"],
        "inputs": {aliases[path]: value for path, value in case["inputs"].items()},
        "argv": [aliases.get(value, value) for value in case["argv"]],
        "source_receipt_sha256": case["source_receipt_sha256"],
    }


def main(args: argparse.Namespace) -> None:
    import matplotlib

    matplotlib.use("Agg")
    matplotlib.rcParams["svg.hashsalt"] = "genesis-macs3"
    import matplotlib.pyplot as plt

    data = json.loads(args.input.read_text())
    if data["status"] not in {"PASS", "FAIL"}:
        raise ValueError("Only a completed comparison can be summarized")
    args.output.mkdir(parents=True, exist_ok=True)
    context = json.loads(args.context.read_text()) if args.context else {}
    rows = []
    for comparison in data["comparisons"]:
        selected = [
            r
            for r in data["runs"]
            if r["case"] == comparison["case"]
            and r["cpus"] == comparison["cpus"]
            and not r["warmup"]
        ]
        row = {
            "case": comparison["case"],
            "cpus": comparison["cpus"],
            "repetitions_per_version": 3,
            "equivalent": comparison["equivalent"],
            "speedup": comparison["speedup"],
        }
        for version in ("baseline", "candidate"):
            measured = [r for r in selected if r["version"] == version]
            row[version + "_wall_median_s"] = statistics.median(r["wall_seconds"] for r in measured)
            row[version + "_wall_min_s"] = min(r["wall_seconds"] for r in measured)
            row[version + "_wall_max_s"] = max(r["wall_seconds"] for r in measured)
            row[version + "_cpu_median_s"] = statistics.median(r["cpu_seconds"] for r in measured)
            row[version + "_sampled_group_rss_max_mib"] = (
                max(r["sampled_group_rss_bytes"] for r in measured) / 1024**2
            )
        rows.append(row)
    with (args.output / "timings.tsv").open("w") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(rows[0]), delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    public = {
        "status": data["status"],
        "source_results_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "limits": data["limits"],
        "memory_scope": data["memory_scope"],
        "cache_policy": data["cache_policy"],
        "launcher_sha256": data["launcher_sha256"],
        "cases": [public_case(c) for c in data["cases"]],
        "input_context": context,
        "runs": [{k: v for k, v in r.items() if k != "argv"} for r in data["runs"]],
        "comparisons": data["comparisons"],
    }
    (args.output / "results.json").write_text(json.dumps(public, indent=2) + "\n")
    cases = [c["id"] for c in data["cases"]]
    fig, axes = plt.subplots(1, 2, figsize=(13, 6), layout="constrained")
    for cpu, marker in [(1, "o"), (2, "s"), (8, "^")]:
        series = [next(r for r in rows if r["case"] == case and r["cpus"] == cpu) for case in cases]
        axes[0].plot(
            [r["speedup"] for r in series],
            range(len(cases)),
            marker=marker,
            linestyle="none",
            label=f"{cpu} CPU" + ("s" if cpu > 1 else ""),
        )
        axes[1].plot(
            [
                r["candidate_sampled_group_rss_max_mib"] / r["baseline_sampled_group_rss_max_mib"]
                for r in series
            ],
            range(len(cases)),
            marker=marker,
            linestyle="none",
            label=f"{cpu} CPU" + ("s" if cpu > 1 else ""),
        )
    for ax in axes:
        ax.axvline(1, color="gray", linestyle="--")
        ax.set_yticks(range(len(cases)), [context.get(c, {}).get("label", c) for c in cases])
        ax.invert_yaxis()
        ax.grid(axis="x", alpha=0.2)
    axes[0].set_xlabel("Speedup: baseline / candidate median wall time")
    axes[0].legend()
    axes[1].set_xlabel("Candidate / baseline sampled process-group RSS")
    axes[1].set_yticklabels([])
    fig.suptitle(
        "MACS3 3.0.5 vs pinned optimized source: plant public subsets\n"
        "Three measured repetitions; peak calling only; RSS can double-count shared pages"
    )
    svg = args.output / "comparison.svg"
    fig.savefig(svg, metadata={"Date": None})
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines()) + "\n")
    fig.savefig(args.output / "comparison.png", dpi=160)
    print(
        f"{len(rows)} comparisons; {len(data['runs'])} calls including warm-ups; {data['status']}"
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--context", type=Path, help="Optional path-neutral input descriptions")
    main(p.parse_args())
