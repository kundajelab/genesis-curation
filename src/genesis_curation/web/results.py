"""Indexed, paginated draft metadata views, separate from human-signed releases."""

from __future__ import annotations

import contextlib
import csv
import io
import sqlite3
from pathlib import Path
from typing import Any

from ..contracts.records import canonical, loads
from ..registry import store
from . import eligibility

COLUMNS = (
    "species",
    "assay",
    "sample_name",
    "tissue",
    "genotype",
    "treatment",
    "dev_stage",
    "paper",
    "paper_link",
    "study_accession",
    "bioproject",
    "geo_series",
    "experiment_accession",
    "run_accessions",
    "biosample",
    "geo_sample",
    "library_layout",
    "sample_attributes",
    "modality",
    "eligibility",
    "reason",
    "review_state",
    "model",
    "model_revision",
    "subject",
    "version",
    "source_versions",
)


def project(db: sqlite3.Connection, state: dict[str, Any]) -> None:
    original = state.get("original", {})
    fields = {f["field"]: f["value"] for f in state.get("fields", []) if f["status"] == "known"}
    for name, decision in state.get("reviews", {}).items():
        if decision["action"] == "approve":
            fields[name] = decision["field"]["value"]
        else:
            fields.pop(name, None)
    aliases = {
        "species": "organism",
        "dev_stage": "developmental_stage",
        "biosample": "biosample_accession",
        "run_accessions": "run_accession",
    }
    row = {key: fields.get(aliases.get(key, key), original.get(key, "")) for key in COLUMNS}
    provenance = state.get("preparation_provenance", {})
    row.update(
        eligibility.classify(original, fields),
        sample_name=state["name"],
        subject=state["id"],
        version=state["version"],
        review_state="draft",
        model=provenance.get("model", ""),
        model_revision=provenance.get("model_revision", ""),
        source_versions=[s["version"] for s in state.get("sources", [])],
    )
    # This is deliberately a draft projection. Only curation_releases proves study sign-off.
    db.execute(
        "INSERT INTO curation_result_rows VALUES(?,?,?,?,?,?) ON CONFLICT(subject) DO UPDATE SET "
        "modality=excluded.modality,species=excluded.species,eligibility=excluded.eligibility,"
        "study=excluded.study,body=excluded.body",
        (
            state["id"],
            row["modality"],
            str(row["species"]),
            row["eligibility"],
            str(row["study_accession"]),
            canonical(row),
        ),
    )


def backfill(root: Path) -> None:
    with store.write(root) as db:
        for (body,) in db.execute(
            "SELECT s.body FROM curation_subjects s LEFT JOIN curation_result_rows r "
            "ON r.subject=s.id WHERE r.subject IS NULL"
        ).fetchall():
            project(db, loads(body))


def page(
    root: Path,
    modality: str = "Bulk ATAC",
    species: str = "",
    eligibility_status: str = "",
    study: str = "",
    cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    if modality not in {"Bulk ATAC", "scATAC", "TSS", "Other"} or not 1 <= limit <= 200:
        raise ValueError("Invalid results page")
    where, args = ["modality=?"], [modality]
    for column, value in (
        ("species", species),
        ("eligibility", eligibility_status),
        ("study", study),
    ):
        if value:
            where.append(column + "=?")
            args.append(value)
    clause = " AND ".join(where)
    with contextlib.closing(store.connect(root)) as db:
        db.execute("BEGIN")
        total = db.execute(
            "SELECT count(*) FROM curation_result_rows WHERE " + clause, args
        ).fetchone()[0]
        rows = [
            loads(r[0])
            for r in db.execute(
                "SELECT body FROM curation_result_rows WHERE "
                + clause
                + " AND subject>? ORDER BY subject LIMIT ?",
                (*args, cursor, limit + 1),
            )
        ]
    return {
        "items": rows[:limit],
        "total": total,
        "next_cursor": rows[limit - 1]["subject"] if len(rows) > limit else None,
        "kind": "draft-metadata",
        "biological_accuracy": "NOT_INDEPENDENTLY_VALIDATED",
    }


def csv_page(rows: list[dict[str, Any]]) -> str:
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        # Spreadsheet-safe text; do not allow archive values to become executable formulas.
        output = {}
        for key in COLUMNS:
            value = row.get(key, "")
            text = canonical(value) if isinstance(value, (list, dict)) else str(value or "")
            output[key] = "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text
        writer.writerow(output)
    return stream.getvalue()


def workbook(root: Path) -> bytes:
    """Export one consistent draft snapshot; no formulas, source-sheet writes or sign-off."""
    import xml.etree.ElementTree as ET
    import zipfile

    spreadsheet = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    relationships = "http://schemas.openxmlformats.org/package/2006/relationships"
    office = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    content_types = "http://schemas.openxmlformats.org/package/2006/content-types"
    sheets: dict[str, list[dict[str, Any]]] = {
        name: [] for name in ("TSS", "Bulk ATAC", "scATAC", "Unresolved", "Excluded")
    }
    with contextlib.closing(store.connect(root)) as db:
        db.execute("BEGIN")
        count = db.execute("SELECT count(*) FROM curation_result_rows").fetchone()[0]
        if count > 10000:
            raise ValueError("Workbook exceeds 10,000 rows; use filtered page exports")
        for (body,) in db.execute("SELECT body FROM curation_result_rows ORDER BY subject"):
            row = loads(body)
            name = (
                "Excluded"
                if row["eligibility"] == "excluded"
                else (row["modality"] if row["eligibility"] == "candidate" else "Unresolved")
            )
            sheets[name].append(row)
    manifest = [
        {"property": "kind", "value": "draft-metadata; not a signed training-data release"},
        {
            "property": "provenance",
            "value": "Pipeline registry; original reference Sheet unchanged",
        },
        {"property": "scientific_qc", "value": "NOT_EVALUATED"},
        {"property": "biological_accuracy", "value": "NOT_INDEPENDENTLY_VALIDATED"},
        {"property": "rows", "value": str(count)},
    ]
    root_book = ET.Element("workbook", xmlns=spreadsheet)
    sheet_list = ET.SubElement(root_book, "sheets")
    rels = ET.Element("Relationships", xmlns=relationships)
    types = ET.Element("Types", xmlns=content_types)
    ET.SubElement(
        types,
        "Default",
        Extension="rels",
        ContentType="application/vnd.openxmlformats-package.relationships+xml",
    )
    ET.SubElement(types, "Default", Extension="xml", ContentType="application/xml")
    ET.SubElement(
        types,
        "Override",
        PartName="/xl/workbook.xml",
        ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, (name, rows) in enumerate([*sheets.items(), ("Manifest", manifest)], 1):
            fields = ("property", "value") if name == "Manifest" else COLUMNS
            sheet = ET.Element("worksheet", xmlns=spreadsheet)
            data = ET.SubElement(sheet, "sheetData")
            for number, row in enumerate([dict(zip(fields, fields, strict=True)), *rows], 1):
                element = ET.SubElement(data, "row", r=str(number))
                for column, field in enumerate(fields, 1):
                    letters, n = "", column
                    while n:
                        n, remainder = divmod(n - 1, 26)
                        letters = chr(65 + remainder) + letters
                    cell = ET.SubElement(element, "c", r=f"{letters}{number}", t="inlineStr")
                    value = row.get(field, "")
                    text = canonical(value) if isinstance(value, (list, dict)) else str(value or "")
                    if len(text) > 32767:
                        raise ValueError(
                            "A cell exceeds Excel's text limit; use CSV or JSON export"
                        )
                    ET.SubElement(ET.SubElement(cell, "is"), "t").text = "".join(
                        c for c in text if c in "\t\r\n" or ord(c) >= 32
                    )
            archive.writestr(
                f"xl/worksheets/sheet{index}.xml", ET.tostring(sheet, encoding="utf-8")
            )
            ET.SubElement(
                sheet_list,
                "sheet",
                name=name,
                sheetId=str(index),
                attrib={f"{{{office}}}id": f"rId{index}"},
            )
            ET.SubElement(
                rels,
                "Relationship",
                Id=f"rId{index}",
                Type=office + "/worksheet",
                Target=f"worksheets/sheet{index}.xml",
            )
            ET.SubElement(
                types,
                "Override",
                PartName=f"/xl/worksheets/sheet{index}.xml",
                ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
            )
        package = ET.Element("Relationships", xmlns=relationships)
        ET.SubElement(
            package,
            "Relationship",
            Id="rId1",
            Type=office + "/officeDocument",
            Target="xl/workbook.xml",
        )
        for path, element in (
            ("xl/workbook.xml", root_book),
            ("xl/_rels/workbook.xml.rels", rels),
            ("_rels/.rels", package),
            ("[Content_Types].xml", types),
        ):
            archive.writestr(path, ET.tostring(element, encoding="utf-8"))
    return stream.getvalue()
