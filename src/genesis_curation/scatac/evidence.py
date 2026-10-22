"""Retain externally reviewed availability evidence without overwriting source metadata."""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..contracts.records import dump, fingerprint, load

COMPONENTS = ("fragments", "cell_annotations", "reference_compatibility", "biological_replicates")
AVAILABILITY = (*COMPONENTS, "fastq", "cell_barcodes", "processed_matrices")


def validate(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("schema_version") != 1 or not isinstance(value.get("studies"), dict):
        raise ValueError("Expected evidence schema version 1 and studies mapping")
    for key, study in value["studies"].items():
        if not re.fullmatch(r"PRJ(?:NA|EB|DB)\d+|[SED]RP\d+", key):
            raise ValueError("Evidence must identify a study accession")
        for component, finding in study.items():
            if component not in AVAILABILITY:
                raise ValueError("Unknown evidence component")
            if finding["status"] not in {"AVAILABLE", "UNAVAILABLE", "UNKNOWN"}:
                raise ValueError("Unknown availability status")
            if finding["review_status"] not in {"UNREVIEWED", "APPROVED", "REJECTED"}:
                raise ValueError("Unknown evidence review status")
            if not finding.get("detail") or not finding.get("sources"):
                raise ValueError("Evidence needs a description and sources")
            if finding["review_status"] == "APPROVED" and not finding.get("reviewer"):
                raise ValueError("Approved evidence requires a named reviewer")
            for source in finding["sources"]:
                url = urlparse(source["url"])
                if url.scheme != "https" or not url.netloc or url.username or url.password:
                    raise ValueError("Evidence URLs must be public HTTPS URLs without credentials")
                if not re.fullmatch(r"[a-f0-9]{64}", source["sha256"]):
                    raise ValueError("Evidence snapshots require SHA256")
                datetime.date.fromisoformat(source["retrieved_date"])
    return value


def attach(directory: Path, source: Path) -> dict[str, Any]:
    value = validate(load(source))
    inventory = load(directory / "inventory.json")
    known = {
        r["values"].get("bioproject") or r["values"].get("study_accession")
        for r in inventory["data"]["rows"]
    }
    if set(value["studies"]) - known:
        raise ValueError("Evidence references a study absent from this inventory")
    version = fingerprint(value)
    dump(directory / "evidence-revisions" / (version + ".json"), value, immutable=True)
    dump(directory / "evidence.json", value)
    return {"evidence_version": version, "studies": len(value["studies"])}


def apply(directory: Path, readiness: list[dict[str, Any]]) -> None:
    path = directory / "evidence.json"
    if not path.exists():
        return
    evidence = validate(load(path))
    for row in readiness:
        findings = evidence["studies"].get(row["study_key"], {})
        approved = 0
        for component in AVAILABILITY:
            item = findings.get(component)
            row[component] = item["status"] if item else "UNKNOWN"
            row[component + "_review"] = item["review_status"] if item else "UNREVIEWED"
            if component in COMPONENTS and item:
                approved += item["status"] == "AVAILABLE" and item["review_status"] == "APPROVED"
        row["score"] = round(20 * (row["metadata_completeness"] + approved), 4)
        row["status"] = "READY_FOR_REVIEW" if approved == 4 else "NEEDS_EVIDENCE"
        row["evidence_version"] = fingerprint(evidence)
