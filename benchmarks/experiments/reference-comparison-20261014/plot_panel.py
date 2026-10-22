"""Draw measured method, depth and reference comparisons with explicit denominators."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

LABELS = {
    "baseline": "BWA-MEM2 / MACS3",
    "bwa": "BWA",
    "bowtie2": "Bowtie2",
    "macs2": "MACS2",
    "genrich": "Genrich (q)",
    "picard": "Picard",
    "cutadapt": "Cutadapt",
    "fastp": "fastp",
}
ORDER = list(LABELS)
COLORS = ["#2166ac", "#b35806", "#1b7837", "#762a83"]


def rows(path: Path) -> list[dict[str, str]]:
    with path.open() as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def plot(summary: Path, concordance: Path | None, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=False)
    quality = rows(summary / "quality-summary.tsv")
    costs = rows(summary / "cost-summary.tsv")
    inputs = {
        "quality-summary.tsv": digest(summary / "quality-summary.tsv"),
        "cost-summary.tsv": digest(summary / "cost-summary.tsv"),
    }
    for name in (
        "quality-summary.tsv",
        "cost-summary.tsv",
        "cost-observations.tsv",
        "index-observations.tsv",
    ):
        shutil.copyfile(summary / name, out / name)
    plt.rcParams.update(
        {
            "font.size": 9,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
        }
    )
    pdf = PdfPages(out / "comparison-figures.pdf")

    def save(fig: plt.Figure, name: str, note: str) -> None:
        fig.text(0.05, 0.015, note, ha="left", va="bottom", fontsize=8)
        fig.tight_layout(rect=(0.02, 0.09, 1, 0.95))
        for suffix in ("svg", "png", "pdf"):
            fig.savefig(
                out / f"{name}.{suffix}",
                dpi=180,
                metadata={"Creator": "Genesis benchmark measurements"},
            )
        pdf.savefig(fig)
        plt.close(fig)

    fig, axes = plt.subplots(3, 3, figsize=(13, 10))
    cohorts = [
        ("dap-se-1m", "SE DAP"),
        ("dap-pe-1m", "PE DAP"),
        ("atac-arabidopsis-1m", "Bulk ATAC"),
    ]
    for row_index, (cohort, title) in enumerate(cohorts):
        observed = [r for r in quality if r["cohort"] == cohort]
        methods = [m for m in ORDER if any(r["method"] == m for r in observed)]
        libraries = sorted(
            {
                r["library"]
                for r in observed
                if r["metric"] == "peak_count" and r["median"] and float(r["median"]) > 0
            }
        )
        for column, (metric, label) in enumerate(
            (
                ("peak_count", "Called peaks"),
                ("fragments_FRiP_own", "Own FRiP"),
                ("fixed_count_spearman", "Fixed-region count Spearman"),
            )
        ):
            axis = axes[row_index, column]
            for index, library in enumerate(libraries):
                subset = {
                    r["method"]: r
                    for r in observed
                    if r["library"] == library and r["metric"] == metric
                }
                for position, method in enumerate(methods):
                    value = subset.get(method)
                    if not value or not value["median"]:
                        continue
                    if value["minimum"] != value["maximum"]:
                        raise ValueError(
                            "Scientific results differ across technical repetitions; inspect "
                            "before plotting"
                        )
                    axis.scatter(
                        position + (index - (len(libraries) - 1) / 2) * 0.12,
                        float(value["median"]),
                        color=COLORS[index],
                        label=library if position == 0 else None,
                        s=28,
                    )
            axis.set_xticks(
                range(len(methods)), [LABELS[m] for m in methods], rotation=40, ha="right"
            )
            axis.set_title(title + " · " + label, loc="left", fontsize=10)
            axis.grid(axis="y", alpha=0.2)
            axis.set_ylim(bottom=-0.02 if metric != "peak_count" else 0)
            if metric != "peak_count":
                axis.set_ylim(-0.02, 1.02)
            if column == 0:
                axis.legend(fontsize=6, frameon=False)
    fig.suptitle(
        "Arabidopsis method comparison · one million input templates per library", x=0.05, ha="left"
    )
    save(
        fig,
        "method-quality",
        (
            "Each point is one treatment or biological ATAC replicate; controls "
            "are not plotted. Quality was identical across three technical "
            "repeats.\nFRiP uses SE read spans or PE outer-fragment spans / "
            "retained spans; peak widths and coverage are in quality-summary.tsv."
            "\nMissing correlation is omitted, never replaced by zero. Baseline "
            "regions are a fixed comparison set, not truth. Genrich uses its "
            "recorded q-value operating point."
        ),
    )

    fig, axes = plt.subplots(2, 3, figsize=(13, 7.7))
    for column, (cohort, title) in enumerate(cohorts):
        observed = [r for r in costs if r["cohort"] == cohort]
        methods = [m for m in ORDER if any(r["method"] == m for r in observed)]
        for row_index, (metric, unit, scale) in enumerate(
            (
                ("processing_seconds", "Processing minutes", 60),
                ("max_sampled_group_rss_gib", "Sampled process-group RSS (GiB)", 1),
            )
        ):
            axis = axes[row_index, column]
            for position, method in enumerate(methods):
                value = next(
                    r for r in observed if r["method"] == method and r["measurement"] == metric
                )
                center, low, high = (
                    float(value[k]) / scale for k in ("median", "minimum", "maximum")
                )
                axis.errorbar(
                    position,
                    center,
                    yerr=[[center - low], [high - center]],
                    fmt="o",
                    color="#2166ac",
                    capsize=3,
                )
            axis.set_xticks(
                range(len(methods)), [LABELS[m] for m in methods], rotation=40, ha="right"
            )
            axis.set_title(title + " · " + unit, loc="left", fontsize=10)
            axis.set_ylim(bottom=0)
            axis.grid(axis="y", alpha=0.2)
    fig.suptitle(
        "Observed component cost · median and range of three technical repeats", x=0.05, ha="left"
    )
    save(
        fig,
        "method-cost",
        (
            "All libraries in each cohort; indexing and common evaluation "
            "excluded. Index costs are reported separately.\nFourteen tool CPUs, "
            "80 GiB per experiment; randomized method order; shared filesystem "
            "and OS caches. Error bars are observed ranges, not confidence "
            "intervals.\nRSS is a sampled sum for the tool process group, not "
            "whole-host peak memory. These are component costs, not complete "
            "workflow timings."
        ),
    )

    fig, axes = plt.subplots(1, 3, figsize=(12, 4.8))
    for axis, metric, label in zip(
        axes,
        ("peak_count", "heldout_fragments_FRiP", "replicate_peak_jaccard"),
        (
            "Called peaks",
            "FRiP in the other replicate's peaks",
            "Biological replicate peak Jaccard",
        ),
        strict=True,
    ):
        for index, library in enumerate(("SRR4000468", "SRR4000469")):
            if metric == "replicate_peak_jaccard" and index == 1:
                continue
            values = [
                next(
                    r
                    for r in quality
                    if r["cohort"] == "atac-arabidopsis-" + depth
                    and r["method"] == "baseline"
                    and r["library"] == library
                    and r["metric"] == metric
                )
                for depth in ("1m", "5m")
            ]
            axis.plot(
                [1, 5],
                [float(r["median"]) for r in values],
                marker="o",
                color=COLORS[index],
                label=library if metric != "replicate_peak_jaccard" else "Replicate pair",
            )
        axis.set_xticks([1, 5], ["1 million", "5 million"])
        axis.set_xlabel("Raw paired templates per replicate")
        axis.set_title(label, loc="left", fontsize=10)
        axis.set_ylim(bottom=0)
        if metric != "peak_count":
            axis.set_ylim(0, 1)
        axis.legend(fontsize=7, frameon=False)
        axis.grid(axis="y", alpha=0.2)
    fig.suptitle("Arabidopsis ATAC depth · unchanged BWA-MEM2 / MACS3 policy", x=0.05, ha="left")
    save(
        fig,
        "atac-depth",
        (
            "Nested, seeded subsets of two biological replicates on TAIR10. The "
            "five-million depth was measured once per method.\nHeld-out FRiP "
            "denominator is retained fragments in the evaluated replicate; "
            "Jaccard uses peak base-pair intersection / union.\nIncreased depth "
            "changes both peak discovery and covered bases; these observations do"
            " not establish a universal plant QC threshold."
        ),
    )

    if concordance:
        mapping = json.loads(concordance.read_text())
        if mapping["status"] != "COMPLETE":
            raise ValueError("Incomplete reference comparison")
        inputs["reference-concordance.json"] = digest(concordance)
        fig, axes = plt.subplots(1, 2, figsize=(9, 4.8))
        libraries = sorted(mapping["libraries"])
        for axis, metric, title in zip(
            axes,
            ("reciprocal_peak_base_jaccard", "common_region_fragment_spearman"),
            ("Peak base-pair Jaccard", "Matched-region count Spearman"),
            strict=True,
        ):
            for index, library in enumerate(libraries):
                value = mapping["libraries"][library]["metrics"][metric]["value"]
                axis.scatter(index, value, color=COLORS[index], s=55)
                axis.annotate(
                    f"{value:.4f}",
                    (index, value),
                    xytext=(0, 9),
                    textcoords="offset points",
                    ha="center",
                )
            axis.set_xticks(range(len(libraries)), libraries)
            axis.set_xlim(-0.5, len(libraries) - 0.5)
            axis.set_ylim(0, 1.05)
            axis.set_title(title, loc="left")
            axis.grid(axis="y", alpha=0.2)
        fig.suptitle("TAIR10 and Col-CEN v1.2 · one million ATAC pairs", x=0.05, ha="left")
        save(
            fig,
            "reference-concordance",
            (
                "Same reads and policy; unique reciprocal assembly mappings only. At "
                "least 95% aligned bases in both directions\nand 95% round-trip span "
                "overlap. See the mapping table for all exclusions. Concordance is "
                "not biological accuracy."
            ),
        )
    pdf.close()
    manifest = {
        "schema_version": 1,
        "script_sha256": digest(Path(__file__)),
        "matplotlib": matplotlib.__version__,
        "inputs": inputs,
        "outputs": {p.name: digest(p) for p in sorted(out.iterdir()) if p.is_file()},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--concordance", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    plot(args.summary, args.concordance, args.out)
