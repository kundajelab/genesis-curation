"""Plot measured ATAC TSS and fragment-length profiles, keeping libraries separate."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
from pathlib import Path
from typing import Any


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def plot(root: Path, out: Path, methods: list[str], repetition: int) -> None:
    result = json.loads((root / "results.json").read_text())
    plan = json.loads((root / "experiment.json").read_text())
    if result["status"] != "COMPLETE" or plan["spec"]["assay"] != "bulk-ATAC":
        raise ValueError("Use a completed bulk-ATAC comparison")
    selected = {
        row["arm"]: row["libraries"]
        for row in result["results"]
        if row["repetition"] == repetition and row["arm"] in methods
    }
    if set(selected) != set(methods) or not methods:
        raise ValueError("A requested method or repetition is absent")
    libraries = sorted(selected[methods[0]])
    if any(sorted(values) != libraries for values in selected.values()):
        raise ValueError("Library populations differ")
    out.mkdir(parents=True, exist_ok=False)
    mpl = importlib.import_module("matplotlib")
    mpl.use("Agg")
    plt = importlib.import_module("matplotlib.pyplot")
    np = importlib.import_module("numpy")
    plt.rcParams.update(
        {
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
        }
    )
    fig, axes = plt.subplots(2, len(libraries), figsize=(6 * len(libraries), 7.8), squeeze=False)
    records: list[dict[str, Any]] = []
    omissions = []
    for column, library in enumerate(libraries):
        for method in methods:
            value = selected[method][library]
            profile = value["tss"]["profile"]
            if len(profile) != 4001:
                raise ValueError("Expected the declared -2000..2000 TSS profile")
            background = sum(profile[:100] + profile[-100:]) / 200
            if background:
                normalized = np.array(profile, dtype=float) / background
                smoothed = np.convolve(normalized, np.ones(25) / 25, mode="valid")
                axes[0, column].plot(range(-1988, 1989), smoothed, label=method, linewidth=1.4)
            else:
                omissions.append(
                    {"method": method, "library": library, "reason": "TSS flank background is zero"}
                )
            for offset, count in enumerate(profile, -2000):
                records.append(
                    {
                        "method": method,
                        "library": library,
                        "repetition": repetition,
                        "measurement": "TSS_cut_count",
                        "position_or_length": offset,
                        "count": count,
                        "denominator": background,
                        "normalized": count / background if background else None,
                    }
                )
            distribution = {int(k): v for k, v in value["fragment_length_distribution"].items()}
            fragments = sum(distribution.values())
            if fragments != value["counts"].get("fragments", 0):
                raise ValueError("Fragment histogram denominator differs from retained pairs")
            if fragments:
                binned = [
                    sum(n for length, n in distribution.items() if left <= length < left + 10)
                    / fragments
                    for left in range(0, 1000, 10)
                ]
                axes[1, column].plot(range(5, 1000, 10), binned, label=method, linewidth=1.4)
            for length, count in sorted(distribution.items()):
                records.append(
                    {
                        "method": method,
                        "library": library,
                        "repetition": repetition,
                        "measurement": "fragment_length",
                        "position_or_length": length,
                        "count": count,
                        "denominator": fragments,
                        "normalized": count / fragments if fragments else None,
                    }
                )
        axes[0, column].set_title(library, loc="left")
        axes[0, column].set_xlabel("Distance from transcript TSS (bp)")
        axes[0, column].set_ylabel("Cut signal / mean flank signal")
        axes[0, column].axhline(1, color="0.5", linestyle=":", linewidth=0.8)
        axes[0, column].set_ylim(bottom=0)
        axes[1, column].set_xlabel("Outer aligned fragment span (bp)")
        axes[1, column].set_ylabel("Fraction of all fragments per 10-bp bin")
        axes[1, column].set_xlim(0, 1000)
        axes[1, column].set_ylim(bottom=0)
        for axis in axes[:, column]:
            axis.grid(alpha=0.15)
            if axis.get_legend_handles_labels()[0]:
                axis.legend(fontsize=8)
            else:
                axis.text(
                    0.5,
                    0.5,
                    "No evaluable profile; see omissions in manifest.json",
                    transform=axis.transAxes,
                    ha="center",
                    fontsize=9,
                )
    fig.suptitle(f"ATAC signal profiles · technical repetition {repetition}", x=0.06, ha="left")
    fig.text(
        0.06,
        0.01,
        "TSS curves use a 25-bp moving mean for display; the reported enrichment "
        "metric uses the unsmoothed center bin.\n"
        "Fragment plots show 0–1,000 bp; their denominator includes longer fragments. "
        "No biological replicates are pooled.",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0.08, 1, 0.95))
    for suffix in ("svg", "png", "pdf"):
        fig.savefig(out / f"profiles.{suffix}", dpi=180, metadata={"Creator": "Genesis benchmark"})
    plt.close(fig)
    with (out / "profiles.tsv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(records)
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "script_sha256": digest(Path(__file__)),
                "source_sha256": digest(root / "results.json"),
                "experiment_id": plan["experiment_id"],
                "methods": methods,
                "repetition": repetition,
                "omissions": omissions,
                "matplotlib": mpl.__version__,
                "numpy": np.__version__,
                "outputs": {p.name: digest(p) for p in sorted(out.iterdir())},
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--method", action="append", required=True)
    parser.add_argument("--repetition", type=int, default=1)
    args = parser.parse_args()
    plot(args.results.resolve(), args.out.resolve(), args.method, args.repetition)
