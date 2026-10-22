"""Versioned candidate discovery. Archive identifiers are not biological replicate identities."""

from __future__ import annotations

import csv
import hashlib
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..contracts.records import dump, fingerprint, identity, load, record, validate
from ..registry import store
from . import evidence, workbook

SPECIES = ("Arabidopsis thaliana", "Sorghum bicolor")
ACCESSIONS = {
    "study_accession": r"[SED]RP\d+",
    "bioproject": r"PRJ(?:NA|EB|DB)\d+",
    "geo_series": r"GSE\d+",
    "experiment_accession": r"[SED]RX\d+",
    "run_accessions": r"[SED]RR\d+",
    "biosample": r"SAM[NED][A-Z]?\d+",
    "geo_sample": r"GSM\d+",
}
METADATA = ("species", "tissue", "genotype", "treatment", "dev_stage", "assay", "multiome")


def tokens(value: str) -> list[str]:
    return [s.strip() for s in re.split(r"[;,]", value) if s.strip()]


def import_workbook(
    source: Path,
    directory: Path,
    registry: Path,
    source_id: str,
    sheet: str = "scATAC Fable Curated",
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", source_id):
        raise ValueError("A stable source namespace is required")
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    sheets = workbook.read(source)
    if sheet not in sheets:
        raise ValueError("Requested worksheet does not exist")
    rows = [r for r in workbook.table(sheets[sheet]) if r["values"]["species"].strip() in SPECIES]
    data = {
        "source_id": source_id,
        "source_sha256": digest,
        "sheet": sheet,
        "worksheets": {k: len(v) for k, v in sheets.items()},
        "rows": rows,
    }
    value = record(
        "scatac_inventory", data, identity("scatac-inventory", source_id, sheet), version=4
    )
    directory.mkdir(parents=True, exist_ok=True)
    copied = directory / (digest + ".xlsx")
    if copied.exists():
        with copied.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise ValueError("Retained workbook was modified")
    else:
        shutil.copyfile(source, copied)
    dump(directory / "revisions" / (value["version"] + ".json"), value, immutable=True)
    store.initialize(registry)
    with store.write(registry) as db:
        store.put(db, value)
    dump(directory / "inventory.json", value)
    return {"inventory": value["version"], "candidate_rows": len(rows), "source_sha256": digest}


def audit(directory: Path) -> dict[str, Any]:
    value = validate(load(directory / "inventory.json"), "scatac_inventory")
    data = value["data"]
    issues = []
    accessions = []
    libraries = []
    relationships: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    raw_seen: dict[str, int] = {}
    studies: dict[str, dict[str, Any]] = {}
    samples: dict[str, dict[str, Any]] = {}

    def issue(row: int, code: str, detail: str) -> None:
        issues.append({"row": row, "code": code, "detail": detail, "review_status": "UNREVIEWED"})

    for source_row in data["rows"]:
        row = source_row["row"]
        fields = source_row["values"]
        candidate = identity("scatac-row", data["source_id"], data["sheet"], str(row))
        study = fields.get("bioproject", "").strip() or fields.get("study_accession", "").strip()
        if not study:
            study = "unresolved:" + candidate
            issue(row, "MISSING_STUDY", "No study accession was provided")
        studies.setdefault(study, {"study_key": study, "species": set(), "candidate_rows": 0})
        studies[study]["species"].add(fields["species"])
        studies[study]["candidate_rows"] += 1
        raw_hash = fingerprint(fields)
        if raw_hash in raw_seen:
            issue(row, "DUPLICATE_ROW", f"Same values as row {raw_seen[raw_hash]}")
        raw_seen[raw_hash] = row
        for field, pattern in ACCESSIONS.items():
            for accession in tokens(fields.get(field, "")):
                valid = re.fullmatch(pattern, accession) is not None
                if not valid:
                    issue(row, "INVALID_ACCESSION", field + ": " + accession)
                accessions.append(
                    {
                        "candidate_id": candidate,
                        "row": row,
                        "kind": field,
                        "accession": accession,
                        "syntax_valid": valid,
                        "study_key": study,
                    }
                )
                relationships[field, accession].append(source_row)
        biosample = fields.get("biosample", "").strip()
        if biosample:
            samples.setdefault(
                biosample,
                {
                    "biosample_accession": biosample,
                    "biological_sample_id": "",
                    "biological_replicate_id": "",
                    "identity_status": "UNRESOLVED",
                    "studies": set(),
                },
            )
            samples[biosample]["studies"].add(study)
        issue(
            row,
            "REPLICATE_UNRESOLVED",
            "Accession and sample-name text do not establish a biological replicate",
        )
        issue(row, "REFERENCE_UNRESOLVED", "Exact FASTA identity requires independent evidence")
        if fields.get("multiome") not in {"yes", "no", "unknown", ""}:
            issue(row, "MULTIOME_VALUE", "Preserved nonstandard multiome value")
        for cell in source_row["cells"].values():
            if cell["formula"] is not None:
                issue(row, "CACHED_FORMULA", "Formula retained; cached value was not recalculated")
                break
        libraries.append(
            {
                "candidate_id": candidate,
                "row": row,
                "study_key": study,
                "library_id": "",
                "biological_sample_id": "",
                "biological_replicate_id": "",
                "identity_status": "UNRESOLVED",
                **fields,
            }
        )
    for (kind, accession), members in relationships.items():
        if len(members) < 2:
            continue
        if kind in {"experiment_accession", "run_accessions"}:
            for member in members:
                issue(member["row"], "REPEATED_ACCESSION", kind + ": " + accession)
        if kind in {"biosample", "experiment_accession", "run_accessions"}:
            for field in (*METADATA, "bioproject", "study_accession"):
                distinct = {m["values"].get(field, "").strip() for m in members} - {""}
                if len(distinct) > 1:
                    for member in members:
                        issue(
                            member["row"],
                            "CONFLICTING_RELATIONSHIP",
                            f"{accession}: {field} has conflicting source values",
                        )
    readiness = []
    for key, study in sorted(studies.items()):
        rows = [r for r in libraries if r["study_key"] == key]
        complete = sum(bool(r.get(k, "").strip()) for r in rows for k in METADATA) / (
            len(rows) * len(METADATA)
        )
        readiness.append(
            {
                "study_key": key,
                "fragments": "UNKNOWN",
                "cell_annotations": "UNKNOWN",
                "reference_compatibility": "UNKNOWN",
                "biological_replicates": "UNKNOWN",
                "metadata_completeness": complete,
                "score": round(20 * complete, 4),
                "score_version": "equal-five-v1",
                "status": "NEEDS_EVIDENCE",
                "review_status": "UNREVIEWED",
            }
        )
        study["species"] = ";".join(sorted(study["species"]))
    evidence.apply(directory, readiness)
    for sample in samples.values():
        sample["studies"] = ";".join(sorted(sample["studies"]))
    summary = {}
    for species in SPECIES:
        rows = [r for r in libraries if r["species"].strip() == species]
        summary[species] = {
            "candidate_rows": len(rows),
            **{
                field: len({v for r in rows for v in tokens(r.get(field, ""))})
                for field in ACCESSIONS
            },
            "confirmed_biological_samples": 0,
            "confirmed_biological_replicates": 0,
            "confirmed_libraries": 0,
            "identity_status": "UNRESOLVED",
        }
    return {
        "inventory_version": value["version"],
        "summary": summary,
        "studies": list(studies.values()),
        "libraries": libraries,
        "samples": list(samples.values()),
        "accessions": accessions,
        "readiness": readiness,
        "metadata_issues": issues,
    }


def export(directory: Path, output: Path) -> dict[str, Any]:
    result = audit(directory)
    output.mkdir(parents=True, exist_ok=True)
    for name in ("studies", "libraries", "samples", "accessions", "readiness", "metadata_issues"):
        rows = result[name]
        fields = sorted({k for r in rows for k in r})
        with (output / f"scATAC_{name}.tsv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
    dump(output / "summary.json", result["summary"])
    original = validate(load(directory / "inventory.json"), "scatac_inventory")
    dump(output / "original-values.json", original)
    return {
        "output": str(output),
        "summary": result["summary"],
        "issues": len(result["metadata_issues"]),
    }
