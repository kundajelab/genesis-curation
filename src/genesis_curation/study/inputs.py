"""Register exact prepared inputs and immutable, library-scoped metadata evidence."""

from __future__ import annotations

import csv
import re
import sqlite3
from pathlib import Path
from typing import Any

from ..contracts.adapters import reference, snapshot
from ..contracts.records import (
    ArtifactManifest,
    canonical,
    digest,
    dump,
    fingerprint,
    identifier,
    identity,
    load,
    loads,
    now,
    record,
    validate,
)
from ..curation.harmonize import source_record
from ..registry import store
from ..samples import load_samples


def read_study(directory: Path) -> dict[str, Any]:
    return validate(load(directory / "study.json"), "study")


def catalog(directory: Path) -> Path:
    return Path(read_study(directory)["data"]["registry"])


def input_head(db: sqlite3.Connection, dataset: str) -> dict[str, Any]:
    row = db.execute("SELECT * FROM input_heads WHERE dataset=?", (dataset,)).fetchone()
    if not row:
        raise ValueError("Dataset has no registered study inputs")
    return dict(row)


def asset(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": digest(path)}


def prepare(
    directory: Path,
    study_id: str,
    assay: str,
    sheet: Path,
    references: Path,
    classification: str,
) -> list[dict[str, Any]]:
    """Read-only input validation; HTTP FASTQs retain the assay's existing handling."""
    identifier(study_id)
    if classification not in {"public", "internal", "local-only"}:
        raise ValueError("Explicit source classification is required")
    source_id = study_id + ":" + assay
    sheet_source = snapshot(sheet, "controller")
    with sheet.open(newline="") as stream:
        sheet_rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(sheet_rows) > 10000:
        raise ValueError("Split studies larger than 10,000 sheet rows")
    # Bulk ATAC runs through genesis-preprocessing directly; studies launch DAP-seq only.
    if assay != "DAP-seq":
        raise ValueError("Supported study assay: DAP-seq")
    libraries: list[tuple[str, list[dict[str, str]], dict[str, Any], dict[str, Any]]] = []
    for row in load_samples(sheet, references):
        ref = {
            "reference_id": re.sub(r"\.(fa|fasta|fna)\.gz$", "", row["reference_fasta"]),
            "species": row["species"],
            "fasta": str(references / row["reference_fasta"]),
            "fasta_sha256": digest(references / row["reference_fasta"]),
        }
        libraries.append((row["sample_id"], [row], ref, row))
    result = []
    for name, rows, ref, library in libraries:
        dataset = identity("dataset", source_id, name)
        metadata = {
            "organism": ref["species"],
            "protocol": assay,
            "role": "control" if library["control_sample"] == "-" else "assay",
            "control": library["control_sample"],
            "layout": "SE" if library["read2_url"] == "-" else "PE",
        }
        parameters: dict[str, Any] = {}
        lanes: list[dict[str, Any]] = []
        biosample = None
        evidence = source_record(
            canonical(
                {
                    **metadata,
                    "library_id": name,
                    "source_rows": rows,
                    "sheet_sha256": sheet_source["data"]["sha256"],
                    "reference": ref,
                }
            ),
            "study:" + study_id + "/" + assay + "/" + name,
            "application/json",
        )
        projection = ArtifactManifest(
            source_id,
            dataset,
            name,
            assay,
            ref["species"],
            study_id,
            biosample,
            name,
            lanes,
            reference(ref),
            "controller",
            {
                "campaign_id": None,
                "job_id": None,
                "attempt": None,
                "execution_state": "UNKNOWN",
                "git_sha": None,
                "parameters": parameters,
                "provenance_complete": False,
                "root": str(directory),
            },
            metadata,
            [],
            [evidence, sheet_source],
            [],
        ).export()
        assets = [asset(sheet), asset(Path(ref["fasta"]))]
        result.append(
            record(
                "input_manifest",
                {
                    "manifest": projection,
                    "rows": rows,
                    "assets": assets,
                    "reference_record": ref,
                    "classification": classification,
                },
                dataset,
                version=3,
            )
        )
    return result


def initialize(
    directory: Path,
    study_id: str,
    assay: str,
    sheet: Path,
    references: Path,
    registry: Path | None = None,
    classification: str = "local-only",
) -> dict[str, Any]:
    directory, sheet, references = directory.resolve(), sheet.resolve(), references.resolve()
    registry = registry.resolve() if registry else directory / "registry"
    if (directory / "study.json").exists():
        previous = read_study(directory)["data"]
        if (previous["study_id"], previous["assay"], previous["registry"]) != (
            study_id,
            assay,
            str(registry),
        ):
            raise ValueError("Study identity/assay/registry changed; use a new directory")
    inputs = prepare(directory, study_id, assay, sheet, references, classification)
    store.initialize(registry)
    value = record(
        "study",
        {
            "study_id": study_id,
            "assay": assay,
            "source_id": study_id + ":" + assay,
            "registry": str(registry),
            "sheet": str(sheet),
            "references": str(references),
            "members": {i["id"]: i["version"] for i in inputs},
        },
        identity("study", study_id, assay),
        version=3,
    )
    with store.write(registry, scope="inputs") as db:
        missing = [
            i["data"]["manifest"]
            for i in inputs
            if not db.execute("SELECT 1 FROM datasets WHERE id=?", (i["id"],)).fetchone()
        ]
        if missing:
            data = {"manifests": missing, "validations": []}
            store.import_bundle_db(
                db, record("bundle", data, identity("bundle", fingerprint(data)))
            )
        for item in inputs:
            dataset, data = item["id"], item["data"]
            projection = data["manifest"]
            old = db.execute("SELECT * FROM input_heads WHERE dataset=?", (dataset,)).fetchone()
            if old and old["version"] == item["version"]:
                continue
            metadata = projection["data"]["metadata"]
            if old:
                original = store.get(db, "manifest", old["manifest"])["data"]["metadata"]
                edits = {
                    k: v
                    for k, v in loads(old["metadata"]).items()
                    if k not in original or original[k] != v
                }
                metadata = {**metadata, **edits}
            bundle_data = {
                "dataset_id": dataset,
                "manifest_version": projection["version"],
                "classification": classification,
                "sources": projection["data"]["sources"][:1],
                "retrieved_at": now(),
                "accessions": [],
            }
            source_bundle = record(
                "source_bundle",
                bundle_data,
                identity("source-bundle", dataset, fingerprint(bundle_data)),
                version=2,
            )
            for record_value in [item, projection, source_bundle, *projection["data"]["sources"]]:
                store.put(db, record_value, dataset)
            result_manifest = old["result_manifest"] if old else None
            # Existing standalone results stay independently reviewable.
            current = db.execute("SELECT manifest FROM datasets WHERE id=?", (dataset,)).fetchone()[
                0
            ]
            if not old and current != projection["version"]:
                result_manifest = current
            db.execute(
                "INSERT INTO input_heads VALUES(?,?,?,?,?,NULL,?,NULL) "
                "ON CONFLICT(dataset) DO UPDATE SET version=excluded.version,"
                "manifest=excluded.manifest,"
                "metadata=excluded.metadata,source_bundle=excluded.source_bundle,canonical_revision=NULL",
                (
                    dataset,
                    item["version"],
                    projection["version"],
                    canonical(metadata),
                    source_bundle["version"],
                    result_manifest,
                ),
            )
            if not result_manifest:
                db.execute(
                    "UPDATE datasets SET manifest=? WHERE id=?", (projection["version"], dataset)
                )
            store.update_metadata(db, dataset, metadata)
        store.put(db, value)
    dump(directory / "revisions" / (value["version"] + ".json"), value, immutable=True)
    dump(directory / "study.json", value)
    return {
        "study": value,
        "datasets": [
            {
                "id": i["id"],
                "name": i["data"]["manifest"]["data"]["name"],
                "input_version": i["version"],
            }
            for i in inputs
        ],
    }
