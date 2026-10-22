"""Machine-readable comparisons and offline HTML; no aggregate biological score."""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any

from .spec import write_json


def table(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def report(out: Path) -> None:
    out = out.resolve()
    plan = json.loads((out / "experiment.json").read_text())
    result = json.loads((out / "results.json").read_text())
    rows, distributions, counts = [], [], []
    library_qc = []
    processing = []
    for arm in result.get("results", []):
        if "processing_wall_seconds" in arm:
            processing.append(
                {
                    "arm": arm["arm"],
                    "repetition": arm["repetition"],
                    "status": arm["status"],
                    "wall_seconds": arm["processing_wall_seconds"],
                    "scope": arm["processing_scope"],
                    "resume": result.get("resume", False),
                }
            )
        for name, qc in arm.get("library_qc", {}).items():
            library_qc.append(
                {
                    "arm": arm["arm"],
                    "repetition": arm["repetition"],
                    "library": name,
                    "qc_json": json.dumps(qc, sort_keys=True, allow_nan=False),
                }
            )
        for sample, value in arm.get("libraries", {}).items():
            prefix = {"arm": arm["arm"], "repetition": arm["repetition"], "library": sample}
            for section in ("metrics", "comparison"):
                for name, measurement in value.get(section, {}).items():
                    rows.append(prefix | {"metric": name} | measurement)
            for unit, measurements in value.get("quantification_comparison", {}).items():
                for name, measurement in measurements.items():
                    rows.append(prefix | {"metric": f"fixed_{unit}_{name}"} | measurement)
            if "tss" in value:
                rows.append(prefix | {"metric": "common_TSS_enrichment"} | value["tss"]["metric"])
            for name, count in value.get("counts", {}).items():
                counts.append(prefix | {"count": name, "value": count})
            for name in ("mapq_distribution", "fragment_length_distribution"):
                for bin_value, count in value.get(name, {}).items():
                    distributions.append(
                        prefix | {"distribution": name, "bin": bin_value, "count": count}
                    )
            for width in value.get("peak_widths", []):
                distributions.append(
                    prefix | {"distribution": "peak_width", "bin": width, "count": 1}
                )
    table(out / "library-qc.tsv", library_qc, ["arm", "repetition", "library", "qc_json"])
    table(
        out / "processing.tsv",
        processing,
        ["arm", "repetition", "status", "wall_seconds", "scope", "resume"],
    )
    fields = [
        "arm",
        "repetition",
        "library",
        "metric",
        "value",
        "status",
        "unit",
        "denominator",
        "definition",
        "version",
        "reason",
    ]
    table(out / "metrics.tsv", rows, fields)
    table(
        out / "distributions.tsv",
        distributions,
        ["arm", "repetition", "library", "distribution", "bin", "count"],
    )
    table(out / "counts.tsv", counts, ["arm", "repetition", "library", "count", "value"])
    resources = []
    for path in sorted((out / "commands").glob("*/*/run.json")):
        record = json.loads(path.read_text())
        memory = path.parent / "container-memory.peak"
        time_file = path.parent / "time.txt"
        timing = {}
        if time_file.is_file():
            for line in time_file.read_text().splitlines():
                key, separator, value = line.strip().rpartition(": ")
                if separator:
                    timing[key] = value
        cpu = None
        if all(k in timing for k in ("User time (seconds)", "System time (seconds)")):
            cpu = float(timing["User time (seconds)"]) + float(timing["System time (seconds)"])
        resources.append(
            {
                "task": path.parent.parent.name,
                "attempt": path.parent.name,
                "status": record["status"],
                "wall_seconds": record["wall_seconds"],
                "cpu_seconds": cpu,
                "sampled_process_group_peak_rss_bytes": record.get(
                    "sampled_process_group_peak_rss_bytes"
                ),
                "max_process_rss_kib": timing.get("Maximum resident set size (kbytes)"),
                "container_memory_peak_bytes": memory.read_text().strip()
                if memory.is_file()
                else None,
                "logs": str(path.parent.relative_to(out)),
                "scope": (
                    "Docker launcher wall/CPU/RSS; CPU/RSS are not container usage"
                    if record.get("image")
                    else "native tool and children; includes startup; "
                    "excludes Python selection/evaluation"
                ),
            }
        )
    table(
        out / "resources.tsv",
        resources,
        [
            "task",
            "attempt",
            "status",
            "wall_seconds",
            "cpu_seconds",
            "sampled_process_group_peak_rss_bytes",
            "max_process_rss_kib",
            "container_memory_peak_bytes",
            "scope",
            "logs",
        ],
    )
    write_json(
        out / "metrics.json",
        {
            "schema_version": 1,
            "metrics": rows,
            "thresholds": "UNSPECIFIED",
            "evidence": "synthetic" if plan["dataset"]["synthetic"] else "real-data sensitivity",
            "experiment_id": plan["experiment_id"],
        },
    )
    title = html.escape(plan["spec"]["id"])
    failures = [a for a in result.get("results", []) if a["status"] != "COMPLETE"]
    encoded = json.dumps(rows, allow_nan=False).replace("<", "\\u003c")
    resources_json = json.dumps(resources, allow_nan=False).replace("<", "\\u003c")
    content = Path(__file__).with_name("report.html").read_text()
    # Replace payload markers last so data cannot become template markup.
    content = content.replace("TITLE", title).replace(
        "EVIDENCE",
        "Synthetic computational evidence"
        if plan["dataset"]["synthetic"]
        else "Real-data sensitivity evidence",
    )
    content = content.replace("IDENTITY", html.escape(plan["experiment_id"])).replace(
        "STATUS", html.escape(result["status"])
    )
    content = content.replace("DATA", encoded).replace("RESOURCES", resources_json)
    (out / "report.html").write_text(content)
    if failures:
        write_json(out / "failures.json", failures)


def figures(out: Path, python: Path) -> None:
    """Render measured tables using a separately installed plotting interpreter."""
    import subprocess

    subprocess.run(
        [
            str(python.resolve()),
            "-I",
            str(Path(__file__).with_name("plots.py")),
            str(out.resolve()),
        ],
        check=True,
        timeout=300,
    )


def compare_runs(out: Path, reference: Path) -> Path:
    """Recompute common metrics on identical baseline regions without changing either run."""
    import tempfile

    from .metrics import assess, compare
    from .spec import sha256, source_identity

    out, reference = out.resolve(), reference.resolve()
    candidate_plan = json.loads((out / "experiment.json").read_text())
    baseline_plan = json.loads((reference / "experiment.json").read_text())
    candidate = json.loads((out / "results.json").read_text())
    baseline = json.loads((reference / "results.json").read_text())
    if candidate["status"] != "COMPLETE" or baseline["status"] != "COMPLETE":
        raise ValueError("Cross-method comparison requires complete runs")
    if (
        candidate_plan["spec"]["assay"] != baseline_plan["spec"]["assay"]
        or candidate_plan["dataset"]["reference"]["fasta_content_sha256"]
        != baseline_plan["dataset"]["reference"]["fasta_content_sha256"]
    ):
        raise ValueError("Cannot compare different assays or reference sequences")
    sampling = [
        (p["spec"]["parameters"].get("template_cap"), p["spec"]["seed"])
        for p in (candidate_plan, baseline_plan)
    ]
    if any(cap is not None for cap, _ in sampling) and sampling[0] != sampling[1]:
        raise ValueError("Different subsampling caps or seeds would confound method comparisons")
    for plan in (candidate_plan, baseline_plan):
        if any(sha256(Path(a["path"])) != a["sha256"] for a in plan["assets"].values()):
            raise ValueError("Original campaign inputs changed")
    candidate_libs = {lib["id"]: lib for lib in candidate_plan["dataset"]["libraries"]}
    baseline_libs = {lib["id"]: lib for lib in baseline_plan["dataset"]["libraries"]}
    if candidate_libs.keys() != baseline_libs.keys():
        raise ValueError("Library sets differ; no implicit sample matching or pooling")
    for name, library in candidate_libs.items():
        other = baseline_libs[name]
        raw = [k for k in ("read1", "read2") if k in library]
        evidence = raw or ["bam"]
        if (
            library["raw_templates"] != other["raw_templates"]
            or library["layout"] != other["layout"]
        ):
            raise ValueError("Different input depths or layouts")
        for key in evidence:
            if (
                key not in other
                or candidate_plan["assets"][library[key]]["sha256"]
                != baseline_plan["assets"][other[key]]["sha256"]
            ):
                raise ValueError(
                    "Different underlying input bytes; comparison would confound data with method"
                )
    reference_arm = next(
        r
        for r in baseline["results"]
        if r["arm"] == baseline_plan["spec"]["baseline"] and r["repetition"] == 1
    )
    target = out / ("comparison-" + baseline_plan["experiment_id"][:12])
    target.mkdir(exist_ok=True)

    def artifacts(value: dict[str, Any]) -> tuple[Path, Path]:
        if any(
            not Path(p).is_file() or sha256(Path(p)) != checksum
            for p, checksum in value["artifacts"].items()
        ):
            raise ValueError("Compared output artifact changed")
        if "artifact_roles" in value:
            return Path(value["artifact_roles"]["bam"]), Path(value["artifact_roles"]["peaks"])
        bams = [Path(p) for p in value["artifacts"] if p.endswith(".bam")]
        peaks = [
            Path(p)
            for p in value["artifacts"]
            if p.endswith((".narrowPeak", ".broadPeak", ".narrowPeak.gz"))
        ]
        if len(bams) != 1 or len(peaks) != 1:
            raise ValueError("Ambiguous legacy artifacts; explicit artifact_roles required")
        return bams[0], peaks[0]

    evaluated = []
    organelles = set(
        baseline_plan["dataset"]["reference"]["mitochondrial"]
        + baseline_plan["dataset"]["reference"]["plastid"]
    )
    for arm in candidate["results"]:
        libraries = {}
        for name, value in arm["libraries"].items():
            reference_bam, fixed = artifacts(reference_arm["libraries"][name])
            bam, peaks = artifacts(value)
            library = candidate_libs[name]
            with tempfile.TemporaryDirectory(dir=target) as tmp:
                current = assess(
                    bam,
                    peaks,
                    fixed,
                    layout=library["layout"],
                    raw_templates=value.get("actual_templates", library["raw_templates"]),
                    organelles=organelles,
                    temporary=Path(tmp),
                )
                original = assess(
                    reference_bam,
                    fixed,
                    fixed,
                    layout=library["layout"],
                    raw_templates=value.get("actual_templates", library["raw_templates"]),
                    organelles=organelles,
                    temporary=Path(tmp),
                )
            current["comparison"] = compare(current, original)
            current["artifacts"] = {str(p): sha256(p) for p in (bam, peaks, reference_bam, fixed)}
            libraries[name] = current
        evaluated.append(
            {
                "arm": arm["arm"],
                "repetition": arm["repetition"],
                "status": "COMPLETE",
                "libraries": libraries,
            }
        )
    provenance = candidate_plan | {
        "comparison_baseline": str(reference),
        "metric_source": source_identity(),
        "comparison_baseline_id": baseline_plan["experiment_id"],
        "policy_differences": {
            "candidate": candidate_plan["spec"],
            "baseline": baseline_plan["spec"],
        },
    }
    write_json(target / "experiment.json", provenance)
    write_json(
        target / "results.json",
        {
            "status": "COMPLETE",
            "results": evaluated,
            "interpretation": (
                "Common definitions and fixed baseline regions; "
                "method/policy differences remain explicit"
            ),
        },
    )
    report(target)
    return target
