"""Recreate static benchmark figures from exported tables in an isolated environment."""

from __future__ import annotations

import csv
import hashlib
import importlib
import json
import math
import statistics
import sys
from pathlib import Path


def render(root: Path) -> None:
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
    with (root / "metrics.tsv").open() as stream:
        metrics = list(csv.DictReader(stream, delimiter="\t"))
    with (root / "resources.tsv").open() as stream:
        costs = list(csv.DictReader(stream, delimiter="\t"))
    plan = json.loads((root / "experiment.json").read_text())
    target = root / "figures"
    target.mkdir(exist_ok=True)
    paths = []

    def save(name: str) -> None:
        fig = plt.gcf()
        fig.tight_layout(rect=(0, 0.04, 1, 0.97))
        for extension in ("svg", "pdf", "png"):
            path = target / f"{name}.{extension}"
            fig.savefig(path, dpi=180, metadata={"Creator": "Genesis benchmark"})
            paths.append(path)
        plt.close(fig)

    chosen = [
        ("fragment_retention", "Retained fragments / input templates"),
        ("fragments_FRiP_fixed", "Fragment FRiP in fixed baseline regions"),
        ("peak_count", "Called peaks"),
        ("peak_bases", "Bases covered by peaks"),
        ("common_TSS_enrichment", "TSS enrichment (center / flank)"),
        ("fixed_count_spearman", "Fixed-region count rank correlation"),
        ("truth_alignment_recall", "Recovery of exact simulated end coordinates"),
        ("truth_peak_precision", "Called peaks overlapping simulated enrichment"),
        ("truth_region_recall", "Simulated enriched regions recovered"),
        ("replicate_peak_jaccard", "Peak overlap between declared biological replicates"),
        ("heldout_fragments_FRiP", "Fragments overlapping the other replicate's peaks"),
    ]
    present = [(key, label) for key, label in chosen if any(r["metric"] == key for r in metrics)]
    if present:
        columns = 2
        nrows = math.ceil(len(present) / columns)
        fig, axes = plt.subplots(nrows, columns, figsize=(13, 3.1 * nrows), squeeze=False)
        for axis in axes.flat[len(present) :]:
            axis.set_visible(False)
        for (key, label), axis in zip(present, list(axes.flat)[: len(present)], strict=True):
            rows = [r for r in metrics if r["metric"] == key]
            names = sorted({r["arm"] for r in rows})
            libraries = sorted({r["library"] for r in rows})
            for j, library in enumerate(libraries):
                x, y = [], []
                for row in rows:
                    if (
                        row["library"] != library
                        or row["status"] != "OK"
                        or row["value"] in ("", "None")
                    ):
                        continue
                    value = float(row["value"])
                    if math.isfinite(value):
                        x.append(
                            names.index(row["arm"])
                            + (j - (len(libraries) - 1) / 2)
                            * min(0.12, 0.65 / max(1, len(libraries)))
                            + (int(row["repetition"]) - 1) * 0.015
                        )
                        y.append(value)
                axis.scatter(x, y, label=library, s=26, alpha=0.8)
            axis.set_xticks(range(len(names)), names, rotation=25, ha="right")
            axis.set_ylabel(rows[0]["unit"])
            axis.set_title(label, loc="left")
            # Keep proportions on a visible, comparable scale. Automatic offsets
            # near one made tiny differences look large and obscured their units.
            axis.ticklabel_format(axis="y", style="plain", useOffset=False)
            if rows[0]["unit"] == "fraction":
                axis.set_ylim(-0.02, 1.02)
                axis.set_yticks([0, 0.25, 0.5, 0.75, 1])
            elif rows[0]["unit"] == "correlation":
                axis.set_ylim(-1.02, 1.02)
                axis.set_yticks([-1, -0.5, 0, 0.5, 1])
            else:
                axis.set_ylim(bottom=0)
            axis.grid(axis="y", alpha=0.2)
            if any(r["status"] == "OK" for r in rows):
                axis.legend(fontsize=8, loc="best")
            else:
                axis.text(
                    0.5,
                    0.5,
                    "Undefined: no eligible TSS window or background",
                    transform=axis.transAxes,
                    ha="center",
                    fontsize=9,
                )
        fig.suptitle("Recorded results by method and library", x=0.05, ha="left")
        fig.text(
            0.05,
            0.01,
            "Each point is one recorded run. Technical repetitions are not biological replicates.\n"
            "Missing measurements are omitted, never replaced with zero. "
            "Baseline agreement is not accuracy.",
            fontsize=9,
        )
        save("quality")
    measured = [
        r for r in costs if r.get("wall_seconds") and r.get("sampled_process_group_peak_rss_bytes")
    ]
    if measured:
        fig, axis = plt.subplots(figsize=(10, 5))
        for phase in ("indexes", "alignment", "mark", "trim", "peaks"):
            rows = [r for r in measured if phase in r["task"]]
            if rows:
                axis.scatter(
                    [float(r["wall_seconds"]) for r in rows],
                    [float(r["sampled_process_group_peak_rss_bytes"]) / 1024**3 for r in rows],
                    label=phase,
                    s=30,
                    alpha=0.8,
                )
        axis.set_xlabel("Tool invocation wall time (s)")
        axis.set_ylabel("Sampled sum of process RSS (GiB)")
        axis.set_title("Runtime and memory of recorded tool invocations", loc="left")
        axis.legend()
        axis.grid(alpha=0.2)
        fig.text(
            0.05,
            0.01,
            "Includes startup. RSS can double-count shared pages; brief peaks may be missed.\n"
            "Index construction is shown separately. Failed attempts remain in resources.tsv.",
            fontsize=9,
        )
        save("resources")
    if plan["spec"]["adapter"] == "methods":
        arms = [a["id"] for a in plan["arms"]]
        repetitions = plan["spec"]["performance"]["repetitions"]
        fig, axes = plt.subplots(1, 2, figsize=(12, 5))
        summaries = []
        for arm in arms:
            walls, memory = [], []
            for rep in range(1, repetitions + 1):
                selected = [
                    r
                    for r in costs
                    if r["task"].startswith(f"{arm}-r{rep}-")
                    and "independent-counts" not in r["task"]
                ]
                if not selected or any(r["status"] != "COMPLETE" for r in selected):
                    continue
                walls.append(sum(float(r["wall_seconds"]) for r in selected))
                observed = [
                    float(r["sampled_process_group_peak_rss_bytes"]) / 1024**3
                    for r in selected
                    if r["sampled_process_group_peak_rss_bytes"]
                ]
                if observed:
                    memory.append(max(observed))
            summaries.append((arm, walls, memory))
        for axis, index, label in (
            (axes[0], 1, "Sum of tool wall times (s)"),
            (axes[1], 2, "Maximum sampled group RSS (GiB)"),
        ):
            for y, row in enumerate(summaries):
                values = row[index]
                if values:
                    center = statistics.median(values)
                    axis.barh(y, center, color="#297a8c", alpha=0.65)
                    axis.plot([min(values), max(values)], [y, y], color="#172936", linewidth=2)
                    axis.plot(center, y, "o", color="#172936", markersize=4)
            axis.set_yticks(range(len(arms)), arms)
            axis.invert_yaxis()
            axis.set_xlabel(label)
            axis.grid(axis="x", alpha=0.2)
        fig.suptitle(
            "Component costs by method · median and range of technical repetitions",
            x=0.05,
            ha="left",
        )
        fig.text(
            0.05,
            0.01,
            "All dataset libraries combined; successful uncached tool commands. "
            "Index building and common evaluation excluded.\n"
            "Python fragment selection is also excluded: "
            "these are component costs, not complete pipeline timings.",
            fontsize=9,
        )
        save("method-costs")
    index = {
        "schema_version": 1,
        "matplotlib_version": matplotlib.__version__,
        "renderer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "sources": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (root / "metrics.tsv", root / "resources.tsv")
        },
        "figures": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        "interpretation": (
            "Measurements, not an aggregate quality score; "
            "see metric definitions and sample identities in the source tables."
        ),
    }
    (target / "manifest.json").write_text(json.dumps(index, indent=2) + "\n")


if __name__ == "__main__":
    render(Path(sys.argv[1]))
