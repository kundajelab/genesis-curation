"""Versioned model-loader bundles with explicit chromosome folds and signal semantics."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from ..contracts.records import dump, fingerprint, load
from .common import complete, publication, reference, software, text_file, verify_output

MODELS = {
    "cherimoya": "8ecf7f791ec07c05a701076cc58a0f845bf97609",
    "chrombpnet": "ece97c93ccaa2d9ee5bc5687e62f4dbf8d055367",
}


def folds(config: dict[str, Any], sizes: dict[str, int]) -> dict[str, str]:
    if set(config) != {"train", "validation", "test"}:
        raise ValueError("Explicit train, validation and test chromosomes are required")
    result = {}
    for split, chroms in config.items():
        if not isinstance(chroms, list) or not chroms:
            raise ValueError("Every fold must have an explicit chromosome list")
        for chrom in chroms:
            if chrom not in sizes or chrom in result:
                raise ValueError("Unknown or overlapping chromosome folds")
            result[chrom] = split
    return result


def export(group: Path, config_path: Path, output: Path, *, resume: bool = False) -> dict[str, Any]:
    started = time.monotonic()
    parent = verify_output(group, "scatac-group")
    config = load(config_path)
    target = config["target"]
    if config.get("schema_version") != 1 or target not in MODELS:
        raise ValueError("Select a supported model and schema version 1")
    if config["model_sha"] != MODELS[target]:
        raise ValueError("Adapter requires its audited model commit")
    reference_value = parent["data"]["group"]["reference"]
    fasta, sizes = reference(reference_value, group)
    mapping = folds(config["folds"], sizes)
    window, output_window, jitter = (
        config[k] for k in ("input_window", "output_window", "max_jitter")
    )
    if any(type(n) is not int for n in (window, output_window, jitter)):
        raise ValueError("Window sizes and jitter must be integers")
    if window < output_window or output_window < 2 or window % 2 or output_window % 2 or jitter < 0:
        raise ValueError("Require even positive windows and nonnegative jitter")
    if config.get("controls") is not None:
        raise ValueError("This adapter exports one unstranded ATAC channel without controls")
    if target == "chrombpnet" and not isinstance(config.get("bias_model"), dict):
        raise ValueError("Record a bias-model requirement; no human bias model is selected")
    signature = fingerprint({"parent": parent["version"], "config": config, "software": software()})
    if output.exists() and resume:
        saved = verify_output(output, "scatac-model")
        if saved["data"]["signature"] != signature:
            raise ValueError("Changed model export inputs")
        return {"cached": True, "manifest": saved}
    radius = window // 2 + jitter
    rows: dict[str, list[str]] = {s: [] for s in config["folds"]}
    excluded = {"unassigned_chromosome": 0, "boundary_window": 0}
    occupied: dict[str, list[tuple[int, int]]] = {}
    for line in (group / "peaks_peaks.narrowPeak").read_text().splitlines():
        fields = line.split("\t")
        chrom, start, end, offset = fields[0], int(fields[1]), int(fields[2]), int(fields[9])
        if not 0 <= offset < end - start:
            raise ValueError("Model peaks require a defined in-peak summit")
        center = start + offset
        occupied.setdefault(chrom, []).append((center - radius, center + radius))
        if chrom not in mapping:
            excluded["unassigned_chromosome"] += 1
        elif center - radius < 0 or center + radius > sizes[chrom]:
            excluded["boundary_window"] += 1
        else:
            rows[mapping[chrom]].append(line)
    if any(not records for records in rows.values()):
        raise ValueError("Each fold requires at least one peak with a valid context window")
    # Explicit, deterministic background. No sequence-matched or GC-matched claim.
    if config.get("background_method") != "nonoverlapping-genome-tiles-v1":
        raise ValueError("Select a declared background method")
    backgrounds = {s: [] for s in rows}
    for chrom, split in mapping.items():
        regions = sorted(occupied.get(chrom, []))
        cursor = 0
        for center in range(radius, sizes[chrom] - radius + 1, 2 * radius):
            left, right = center - radius, center + radius
            while cursor < len(regions) and regions[cursor][1] <= left:
                cursor += 1
            if cursor < len(regions) and regions[cursor][0] < right:
                continue
            backgrounds[split].append(
                f"{chrom}\t{center - 1}\t{center + 1}\tbackground\t0\t.\t0\t0\t0\t1"
            )
    if any(not rows for rows in backgrounds.values()):
        raise ValueError("Every fold requires at least one background window")
    with publication(output) as stage:
        with text_file(fasta) as source, (stage / "genome.fa").open("w") as dest:
            shutil.copyfileobj(source, dest)
        for name in ("signal.bw", "chrom.sizes", "metadata.json", "qc.json", "cells.jsonl"):
            shutil.copyfile(group / name, stage / name)
        dump(stage / "parent.json", parent)
        dump(stage / "config.json", config)
        for split in rows:
            (stage / f"{split}.peaks.bed").write_text("\n".join(rows[split]) + "\n")
            (stage / f"{split}.background.bed").write_text("\n".join(backgrounds[split]) + "\n")
        dump(stage / "folds.json", config["folds"])
        loader = {
            "target": target,
            "model_sha": MODELS[target],
            "sequences": "genome.fa",
            "signals": ["signal.bw"],
            "controls": None,
            "signal_groups": [1],
            "input_window": window,
            "output_window": output_window,
            "max_jitter": jitter,
            "summits": True,
            "further_tn5_shift": False,
            "bias_model": config.get("bias_model"),
        }
        dump(stage / "loader.json", loader)
        result = complete(
            stage,
            "scatac-model",
            {
                "signature": signature,
                "parent": parent["version"],
                "config": config,
                "excluded_peaks": excluded,
                "peaks_per_fold": {s: len(v) for s, v in rows.items()},
                "backgrounds_per_fold": {s: len(v) for s, v in backgrounds.items()},
                "reference": reference_value,
                "model_ready": False,
                "status": "REQUIRES_MODEL_LOADER_TEST_AND_REVIEW",
                "scientific_limitations": [
                    "Coordinate folds are disjoint; homology leakage is unassessed",
                    "Genome-tile backgrounds are not GC matched",
                    "Training and plant biological quality are not validated by export",
                ],
            },
            started,
        )
    return {"cached": False, "manifest": result}


def validate(directory: Path) -> dict[str, Any]:
    result = verify_output(directory, "scatac-model")
    sizes = {
        r.split()[0]: int(r.split()[1])
        for r in (directory / "chrom.sizes").read_text().splitlines()
    }
    folds(result["data"]["config"]["folds"], sizes)
    return {
        "checksums": "PASS",
        "folds": "PASS",
        "model_ready": False,
        "loader_test": "NOT_ASSESSED",
        "manifest": result["version"],
    }
