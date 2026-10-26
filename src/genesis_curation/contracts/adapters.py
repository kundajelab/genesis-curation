"""Read existing published run records without changing scientific inputs or outputs."""

from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path
from typing import Any

from .records import (
    ArtifactManifest,
    QCMeasurement,
    SourceEvidence,
    identity,
    load,
    record,
)


def snapshot(path: Path, worker: str) -> dict[str, Any]:
    """Store a source document under its resolved location.

    Metric evidence must cite source["data"]["location"], not the caller's path: validation
    matches them as strings, and a path through a symlink would never match.
    """
    with path.open("rb") as stream:
        raw = stream.read(32 * 1024 * 1024 + 1)
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError("Source snapshot exceeds document limit")
    checksum = hashlib.sha256(raw).hexdigest()
    return record(
        "source",
        {
            "location": str(path.resolve()),
            "worker": worker,
            "sha256": checksum,
            "snapshot": raw.decode(),
            "media_type": "application/json"
            if path.suffix == ".json"
            else "text/tab-separated-values",
        },
        identity("source", worker, str(path.resolve())),
    )


def reference(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": value.get("reference_id"),
        "version": value.get("fasta_sha256"),
        "contigs": value.get("contigs", {}),
        "fasta": value.get("fasta"),
        "fasta_sha256": value.get("fasta_sha256"),
        "tss": value.get("tss"),
        "tss_sha256": value.get("tss_sha256"),
        "assembly": value.get("assembly"),
        "annotation_release": value.get("annotation_release"),
        "genome_size": value.get("genome_size"),
        "mitochondrial_contigs": value.get("mitochondrial_contigs", []),
        "plastid_contigs": value.get("plastid_contigs", []),
    }


def artifact(
    root: Path,
    relative: str,
    worker: str,
    dataset: str,
    role: str,
    fmt: str,
    ref: str | None,
    *,
    required: bool = True,
    checksum: str | None = None,
    signal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = root / relative
    return {
        "id": identity("artifact", dataset, relative),
        "path": str(path.absolute()),
        "worker": worker,
        "format": fmt,
        "role": role,
        "required": required,
        "availability": "available" if path.is_file() else "unavailable",
        "retention": "published",
        "sha256": checksum,
        "size": path.stat().st_size if path.is_file() else None,
        "reference_id": ref,
        "signal": signal,
    }


def signal(role: str, strand: str = "both") -> dict[str, Any]:
    return {
        "kind": "normalized_coverage" if role == "track_cpm" else "raw_counts",
        "unit": {"track_cpm": "base_coverage", "cuts_counts": "cut", "counts_5p": "read_end"}[role],
        "normalization": "CPM" if role == "track_cpm" else "none",
        "strand": strand,
        "derivation": "Genesis supported workflow v1: " + role,
    }


# The Nextflow run records its own provenance (trace, report, git-status.txt, dap-images.lock).
# Carry that record into each manifest as-is; assessing it is not this adapter's job.
DAP_PROVENANCE_NOTE = (
    "DAP provenance is recorded by the Nextflow run (trace, report, git-status.txt, "
    "dap-images.lock) and is not assessed by this adapter"
)


def dap_run_record(output: Path) -> tuple[str | None, dict[str, Any]]:
    """Copy a DAP run's recorded commit, container engine, image pins and dirty files."""
    status_path, lock_path = output / "git-status.txt", output / "dap-images.lock"
    fields: dict[str, str] = {}
    dirty: list[str] = []
    if status_path.is_file():
        lines = status_path.read_text().splitlines()
        for index, line in enumerate(lines):
            key, separator, value = line.partition(": ")
            if line == "dirty files:":
                dirty = [entry for entry in lines[index + 1 :] if entry.strip()]
                break
            if separator:
                fields[key] = value
    commit = fields.get("commit")
    if commit is not None and not re.fullmatch(r"[0-9a-f]{40}", commit):
        commit = None
    images: dict[str, str] = {}
    if lock_path.is_file():
        for line in lock_path.read_text().splitlines():
            name, separator, image = line.strip().partition("=")
            if separator and not name.startswith("#"):
                images[name] = image
    evidence = {
        "container_engine": fields.get("container engine"),
        "images": images,
        "dirty_files": dirty,
    }
    return commit, evidence


def from_dap(
    root: Path,
    source_id: str,
    worker: str,
    study: str | None,
    sheet: Path | None,
    references: Path | None,
) -> list[dict[str, Any]]:
    path = root / "output/multiqc/multiqc_data/genesis_provenance.json"
    provenance = load(path)
    sources = [snapshot(path, worker)]
    # DAP runs record image pins and Git status beside their outputs, not per-task evidence.
    commit, run_evidence = dap_run_record(root / "output")
    limitations = [DAP_PROVENANCE_NOTE]
    rows: dict[str, dict[str, str]] = {}
    if sheet:
        with sheet.open() as stream:
            rows = {row["sample_id"]: row for row in csv.DictReader(stream, delimiter="\t")}
        sources.append(snapshot(sheet, worker))
    result = []
    names = [s["id"] for s in provenance["samples"]]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate DAP sample identity")
    for sample in provenance["samples"]:
        name, species = sample["id"], sample["species"]
        from ..samples import IDENTIFIER

        if not all(IDENTIFIER.fullmatch(x) for x in (name, species, sample["reference_fasta"])):
            raise ValueError("Unsafe DAP identity")
        dataset = identity("dataset", source_id, name)
        if sample["layout"] not in {"SE", "PE"}:
            raise ValueError("Unsupported DAP library layout")
        if sample.get("control", "-") != "-" and sample["control"] not in names:
            raise ValueError("DAP control is absent from published sample provenance")
        ref = reference(
            {
                "reference_id": sample["ref_id"],
                "fasta": str((references / sample["reference_fasta"]).resolve())
                if references
                else None,
            }
        )
        folder = root / "output" / species / name
        artifacts = []

        def add(
            relative: str,
            role: str,
            fmt: str,
            required: bool = True,
            semantics: dict[str, Any] | None = None,
            output: list[dict[str, Any]] = artifacts,
            base: Path = folder,
            dataset_id: str = dataset,
            reference_id: str | None = ref["id"],
        ) -> None:
            output.append(
                artifact(
                    base,
                    relative,
                    worker,
                    dataset_id,
                    role,
                    fmt,
                    reference_id,
                    required=required,
                    signal=semantics,
                )
            )

        add(name + ".metadata.tsv", "metadata", "tsv")
        for stem in [name + ".tracks"] + (
            [name + ".tracks.read1"] if sample["layout"] == "PE" else []
        ):
            for suffix, role, strand in (
                ("CPM", "track_cpm", "both"),
                ("plus.CPM", "track_cpm", "plus"),
                ("minus.CPM", "track_cpm", "minus"),
                ("plus.5p.counts", "counts_5p", "plus"),
                ("minus.5p.counts", "counts_5p", "minus"),
            ):
                add(stem + "." + suffix + ".bw", role, "bigwig", semantics=signal(role, strand))
        control = sample.get("control", "-")
        if control != "-":
            add(name + ".macs3_peaks.narrowPeak.gz", "peaks", "narrowPeak")
            for suffix in ("RPM", "mean_RPKM"):
                add(name + ".peaks." + suffix + ".tsv", "quantification", "tsv")
        for mate in ("read1", "read2") if sample["layout"] == "PE" else ("read1",):
            add(f"qc/fastqc/{name}.{mate}_fastqc.zip", "fastqc", "zip")
        for suffix in ("main.stats.txt", "read1.stats.txt", "flagstat.txt", "idxstats.tsv"):
            add(name + ".qc.report." + suffix, "alignment_qc", "tsv")
        for item in artifacts:
            expected = [
                s["sha256"]
                for s in provenance.get("sources", [])
                if s.get("sample") == name and s.get("filename") == Path(item["path"]).name
            ]
            if len(expected) == 1:
                item["sha256"] = expected[0]
        # Preserve native QC definitions and their raw sources. FastQC flags are
        # descriptive evidence, never dataset rejection criteria.
        qc_sources, measured = [], []
        raw_path = root / "output/multiqc/multiqc_data/genesis_flagstat.json"
        if raw_path.is_file():
            qc_source = snapshot(raw_path, worker)
            qc_sources.append(qc_source)
            raw = load(raw_path)
            key = name + ".qc.report.flagstat.txt"
            for metric, value in raw.get(key, {}).items():
                if type(value) not in (int, float) and value is not None:
                    continue
                # MultiQC names do not specify every metric's denominator. Keep
                # those fields in the source snapshot without inventing units.
                if metric not in {"flagstat_total", "mapped_passed", "duplicates_passed"}:
                    continue
                measured.append(
                    QCMeasurement(
                        dataset,
                        metric,
                        "samtools-flagstat/" + metric + "/v1",
                        "records",
                        "none",
                        value,
                        SourceEvidence(
                            qc_source["data"]["location"],
                            qc_source["data"]["sha256"],
                            "/" + key + "/" + metric,
                            worker,
                        ),
                    ).export()
                )
        artifacts.append(
            artifact(
                root,
                "output/multiqc/multiqc_report.html",
                worker,
                dataset,
                "report",
                "html",
                ref["id"],
            )
        )
        result.append(
            ArtifactManifest(
                source_id,
                dataset,
                name,
                "DAP-seq",
                species,
                study,
                None,
                name,
                [],
                ref,
                worker,
                {
                    "campaign_id": None,
                    "job_id": None,
                    "attempt": None,
                    "execution_state": "UNKNOWN",
                    "git_sha": commit or provenance.get("run", {}).get("git_sha"),
                    "parameters": run_evidence,
                    "provenance_complete": False,
                    "root": str(root),
                },
                {
                    "layout": sample["layout"],
                    "control": control,
                    "role": "control" if control == "-" else "assay",
                    "original_sample": rows.get(name),
                    "output_contract": "genesis-published-v1",
                    "provenance_limitations": limitations,
                },
                artifacts,
                sources + qc_sources,
                measured,
            ).export()
        )
    return result


def run_manifests(
    root: Path,
    assay: str,
    source_id: str,
    worker: str,
    study: str | None = None,
    sheet: Path | None = None,
    references: Path | None = None,
) -> list[dict[str, Any]]:
    if not source_id.strip() or not worker.strip():
        raise ValueError("Explicit source and worker identities are required")
    # absolute(), not resolve(), as in artifact(): run roots and artifact paths must stay spelled as
    # planned (e.g. not /var rewritten to /private/var), or collection cannot match them.
    root = root.absolute()
    if assay == "DAP-seq":
        return from_dap(root, source_id, worker, study, sheet, references)
    raise ValueError("Unsupported assay")
