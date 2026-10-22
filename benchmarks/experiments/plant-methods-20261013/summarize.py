"""Make panel tables and figures from completed, explicitly named experiments.

Run with the locked benchmark Python. Biological libraries remain separate;
median/range summaries describe technical repetitions of the same input.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def rows(path: Path) -> list[dict[str, str]]:
    with path.open() as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def table(path: Path, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(records)


def describe(values: list[float]) -> dict[str, Any]:
    if any(not math.isfinite(v) for v in values):
        raise ValueError("Nonfinite measurement")
    return {
        "n": len(values),
        "median": statistics.median(values) if values else None,
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
    }


def summarize(
    runs: dict[str, Path], out: Path, workflows: Path | None, motifs: Path | None
) -> None:
    out.mkdir(parents=True, exist_ok=False)
    sources: dict[str, str] = {}
    identities = {}
    quality, costs, indexes = [], [], []
    for label, root in runs.items():
        plan = json.loads((root / "experiment.json").read_text())
        result = json.loads((root / "results.json").read_text())
        if result["status"] != "COMPLETE" or result.get("resume", False):
            raise ValueError("Use complete, fresh experiments; keep failures in their own record")
        identities[label] = plan["experiment_id"]
        for name in (
            "experiment.json",
            "results.json",
            "metrics.tsv",
            "resources.tsv",
            "processing.tsv",
        ):
            sources[str(root / name)] = sha256(root / name)
        grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
        for row in rows(root / "metrics.tsv"):
            grouped[row["arm"], row["library"], row["metric"]].append(row)
        for (method, library, metric), group in sorted(grouped.items()):
            values = [float(r["value"]) for r in group if r["status"] == "OK"]
            if len({(r["definition"], r["unit"], r["version"]) for r in group}) != 1:
                raise ValueError("Cannot combine incompatible metric definitions")
            quality.append(
                {
                    "cohort": label,
                    "method": method,
                    "library": library,
                    "metric": metric,
                    **describe(values),
                    "missing": len(group) - len(values),
                    "denominators": json.dumps(sorted({r["denominator"] for r in group})),
                    "unit": group[0]["unit"],
                    "definition": group[0]["definition"],
                    "version": group[0]["version"],
                }
            )
        resources = rows(root / "resources.tsv")
        processing = rows(root / "processing.tsv")
        for method in (arm["id"] for arm in plan["arms"]):
            for row in (r for r in processing if r["arm"] == method):
                commands = [
                    r
                    for r in resources
                    if r["task"].startswith(f"{method}-r{row['repetition']}-")
                    and "independent-counts" not in r["task"]
                ]
                if row["status"] != "COMPLETE" or any(r["status"] != "COMPLETE" for r in commands):
                    raise ValueError("Failed processing cannot enter successful cost summaries")
                memory = [
                    float(r["sampled_process_group_peak_rss_bytes"]) / 1024**3
                    for r in commands
                    if r["sampled_process_group_peak_rss_bytes"]
                ]
                costs.append(
                    {
                        "cohort": label,
                        "method": method,
                        "repetition": row["repetition"],
                        "processing_seconds": float(row["wall_seconds"]),
                        "tool_seconds": sum(float(r["wall_seconds"]) for r in commands),
                        "max_sampled_group_rss_gib": max(memory) if memory else None,
                        "scope": "all libraries; indexing and common evaluation excluded",
                    }
                )
        for row in resources:
            if row["task"].startswith("indexes-"):
                indexes.append({"cohort": label} | row)
    table(out / "quality-summary.tsv", quality)
    table(out / "cost-observations.tsv", costs)
    table(out / "index-observations.tsv", indexes)
    cost_groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for row in costs:
        for metric in ("processing_seconds", "tool_seconds", "max_sampled_group_rss_gib"):
            if row[metric] is not None:
                cost_groups[row["cohort"], row["method"], metric].append(row[metric])
    table(
        out / "cost-summary.tsv",
        [
            {"cohort": cohort, "method": method, "measurement": metric, **describe(values)}
            for (cohort, method, metric), values in sorted(cost_groups.items())
        ],
    )

    matplotlib = importlib.import_module("matplotlib")
    matplotlib.use("Agg")
    plt = importlib.import_module("matplotlib.pyplot")
    plt.rcParams.update(
        {
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
        }
    )
    figures = []

    def save(name: str) -> None:
        fig = plt.gcf()
        fig.tight_layout(rect=(0, 0.12, 1, 0.95))
        for suffix in ("svg", "png", "pdf"):
            path = out / f"{name}.{suffix}"
            fig.savefig(path, dpi=180, metadata={"Creator": "Genesis measured comparison"})
            figures.append(path)
        plt.close(fig)

    if workflows:
        source = workflows / "metrics.tsv"
        sources[str(source)] = sha256(source)
        observed = rows(source)
        table(out / "workflow-metrics.tsv", observed)
        libraries = sorted({r["library"] for r in observed})
        methods = sorted({r["workflow"] for r in observed})
        fig, axes = plt.subplots(1, 3, figsize=(12, 4.5))
        for axis, metric, title in zip(
            axes,
            ("fragment_retention", "common_TSS_enrichment", "fragments_FRiP_fixed"),
            (
                "Retained fragments / input pairs",
                "TSS center / flank signal",
                "Fragment FRiP in Genesis peaks",
            ),
            strict=True,
        ):
            for j, library in enumerate(libraries):
                points = [
                    next(
                        r
                        for r in observed
                        if r["workflow"] == method
                        and r["library"] == library
                        and r["metric"] == metric
                    )
                    for method in methods
                ]
                axis.scatter(
                    [i + (j - 0.5) * 0.12 for i in range(len(methods))],
                    [float(r["value"]) for r in points],
                    s=55,
                    label=library,
                )
            axis.set_xticks(range(len(methods)), methods)
            axis.set_title(title, loc="left", fontsize=11)
            axis.set_ylim(bottom=0)
            if metric != "common_TSS_enrichment":
                axis.set_ylim(-0.02, 1.02)
            axis.grid(axis="y", alpha=0.2)
        axes[0].legend(fontsize=8)
        fig.suptitle(
            "Configured ATAC workflows · one million input pairs per library", x=0.05, ha="left"
        )
        fig.text(
            0.05,
            0.01,
            "Same reads and reference; filtering and caller policies differ. "
            "Each point is one library, measured once.\n"
            "Genesis peaks are fixed comparison regions, not truth. "
            "See the table for organellar retention and peak coverage.",
            fontsize=9,
        )
        save("workflow-quality")

    if motifs:
        source = motifs / "motif-metrics.tsv"
        sources[str(source)] = sha256(source)
        observed = [r for r in rows(source) if r["selection"] == "top1000"]
        table(out / "motif-top1000.tsv", observed)
        libraries = sorted({r["library"] for r in observed})
        fig, axes = plt.subplots(1, len(libraries), figsize=(12, 4.5), squeeze=False)
        for axis, library in zip(axes.flat, libraries, strict=True):
            methods = sorted({r["arm"] for r in observed if r["library"] == library})
            for j, (field, label, color) in enumerate(
                (
                    ("peak_hit_fraction", "Summit windows", "#247a91"),
                    ("control_hit_fraction", "GC-matched controls", "#c26c34"),
                )
            ):
                for i, method in enumerate(methods):
                    group = [r for r in observed if r["library"] == library and r["arm"] == method]
                    values = [float(r[field]) for r in group if r[field]]
                    if values:
                        x = i + (j - 0.5) * 0.20
                        axis.plot([x, x], [min(values), max(values)], color=color)
                        axis.scatter(
                            x,
                            statistics.median(values),
                            color=color,
                            s=45,
                            label=label if i == 0 else None,
                        )
            matrix = next(r["matrix"] for r in observed if r["library"] == library)
            axis.set_title(f"{library} · {matrix}", loc="left")
            axis.set_xticks(range(len(methods)), methods, rotation=20, ha="right")
            axis.set_ylim(-0.02, 1.02)
            axis.set_ylabel("Fraction of paired windows with a motif hit")
            axis.grid(axis="y", alpha=0.2)
            axis.legend(fontsize=8)
        fig.suptitle(
            "Independent PBM motifs · top 1,000 eligible DAP summit windows", x=0.05, ha="left"
        )
        fig.text(
            0.05,
            0.01,
            "200-bp windows, both strands; median/range over technical repeats. "
            "Incomplete matched sets are retained.\n"
            "Consult motif-top1000.tsv for exact peak/control hit counts, paired "
            "denominators and missing controls.",
            fontsize=9,
        )
        save("dap-motif-support")

    manifest = {
        "schema_version": 1,
        "script_sha256": sha256(Path(__file__)),
        "matplotlib_version": matplotlib.__version__,
        "experiment_ids": identities,
        "source_sha256": sources,
        "outputs": {p.name: sha256(p) for p in sorted(out.iterdir()) if p.is_file()},
        "scope": "Technical repetitions summarized within each method/library; no quality score",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", default=[], metavar="NAME=RESULT_DIRECTORY")
    parser.add_argument("--workflows", type=Path)
    parser.add_argument("--motifs", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    entries = [value.split("=", 1) for value in args.run]
    if any(len(entry) != 2 for entry in entries) or len({e[0] for e in entries}) != len(entries):
        parser.error("Use unique NAME=RESULT_DIRECTORY entries")
    summarize(
        {name: Path(path).resolve() for name, path in entries},
        args.out.resolve(),
        args.workflows,
        args.motifs,
    )
