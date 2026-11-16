"""Lossless external workbook inventory and indexed curation views.

Reference claims remain separate from archive evidence and accepted metadata.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from ..contracts.records import canonical, fingerprint, loads, now
from ..registry import store
from . import catalog, workbook
from .discovery_catalog import MODALITIES

ALIASES = {
    "organism": "species",
    "scientific_name": "species",
    "dev_stage": "developmental_stage",
    "sra_study": "study_accession",
    "geo_series": "geo_series",
    "sra_experiment": "experiment_accession",
    "sra_runs": "run_accessions",
    "sra_link": "experiment_link",
    "#": "sample_name",
    "sample path": "sample_name",
    "method": "assay",
    "fasta": "reference_path",
    "chrom_sizes": "chrom_sizes",
}
ACCESSION = re.compile(
    r"\b(?:GSE|GSM|[SED]R[PRXS]|CRA|CRX|CRR|PRJ[EDNC][AB]|GCA_|GCF_)\d+(?:\.\d+)?\b"
)


def event(
    db: sqlite3.Connection, campaign: str, entity: str, action: str, **details: object
) -> int:
    cursor = db.execute(
        "INSERT INTO workspace_events(campaign,entity,created,body) VALUES(?,?,?,?)",
        (campaign, entity, now(), canonical({"action": action, **details})),
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def normalized(values: dict[str, Any], modality: str) -> dict[str, Any]:
    result = {
        ALIASES.get(k.strip().lower(), k.strip().lower().replace(" ", "_")): v
        for k, v in values.items()
    }
    species = str(result.get("species", "")).replace("_", " ")
    # A path-derived label is a search hint, not verified taxonomy.
    result["species"] = re.split(r"-(?:PRJ|[SED]RP|GSE)", species)[0]
    text = " ".join(str(v) for v in values.values())
    accessions = sorted(set(ACCESSION.findall(text)))
    for field, pattern in (
        ("experiment_accession", r"(?:[SED]RX|CRX)\d+"),
        ("study_accession", r"(?:GSE|[SED]RP|CRA|PRJ[EDNC][AB])\d+"),
    ):
        if not result.get(field):
            result[field] = next((a for a in accessions if re.fullmatch(pattern, a)), "")
    result.update(modality=modality, accessions=accessions)
    result["sample_name"] = str(
        result.get("sample_name")
        or result.get("experiment_accession")
        or result.get("title")
        or result.get("entry")
        or result.get("study_accession")
        or "Reference"
    )
    return result


def add_row(
    db: sqlite3.Connection,
    batch: str,
    tab: str,
    number: int,
    values: dict[str, Any],
    cells: dict[str, Any],
    modality: str,
    location: str,
    origin: str = "discovery",
) -> None:
    row = normalized(values, modality)
    key = fingerprint([batch, tab, number])
    # Only exact experiment identifiers share a curation subject. Paths/titles never do.
    experiment = str(row.get("experiment_accession", ""))
    entity = experiment if re.fullmatch(r"(?:[SED]RX|CRX)\d+", experiment) else key
    body = {
        "original": values,
        "cells": cells,
        "values": row,
        "location": location,
        "kind": "genome"
        if modality == "Genomes"
        else "paper"
        if modality == "MPRA" and not experiment
        else "metadata",
        "origin": origin,
    }
    inserted = db.execute(
        (
            "INSERT OR IGNORE INTO workspace_rows(id,batch,tab,row_number,moda"
            "lity,species,study,entity,status,body) VALUES(?,?,?,?,?,?,?,?,?,?"
            ")"
        ),
        (
            key,
            batch,
            tab,
            number,
            modality,
            row["species"],
            str(row.get("study_accession", "")),
            entity,
            "Discovered" if origin == "discovery" else "Imported",
            canonical(body),
        ),
    )

    if inserted.rowcount and row.get("study_accession"):
        db.execute(
            "UPDATE curation_queue_owners SET dirty=1 WHERE kind='study' "
            "AND json_extract(body,'$.accession')=?",
            (row["study_accession"],),
        )


def register_rna(db: sqlite3.Connection, state: dict[str, Any], accession: str | None) -> None:
    """Make native RNA imports visible in Curation without inventing discovery receipts."""
    if db.execute("SELECT 1 FROM workspace_rows WHERE subject=?", (state["id"],)).fetchone():
        return
    projection = db.execute(
        "SELECT body,modality FROM curation_result_rows WHERE subject=?", (state["id"],)
    ).fetchone()
    if not projection or projection["modality"] != "RNA-seq":
        return
    values = {**state["original"], **loads(projection["body"])}
    values["study_accession"] = accession or values.get("study_accession", "")
    location = next(
        (
            s["data"]["location"]
            for s in state["sources"]
            if s["data"]["location"].startswith("https:")
        ),
        "upload:" + state["namespace"],
    )
    batch, tab = "native:" + state["id"], "RNA-seq"
    add_row(
        db,
        batch,
        tab,
        1,
        values,
        {},
        "RNA-seq",
        location,
        "archive" if location.startswith("https:") else "upload",
    )
    db.execute(
        "UPDATE workspace_rows SET subject=? WHERE id=?",
        (state["id"], fingerprint([batch, tab, 1])),
    )
    event(db, batch, state["id"], "native-metadata-import", subject=state["id"], location=location)
    sync(db, state)


def import_workbook(root: Path, path: Path) -> dict[str, Any]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    registry = root / "catalog"
    store.initialize(registry)
    with contextlib.closing(store.connect(registry)) as db:
        old = db.execute("SELECT body FROM workspace_imports WHERE id=?", (digest,)).fetchone()
        if old:
            return loads(old[0])
    destination = root / "reference-snapshots" / (digest + ".xlsx")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copyfile(path, destination)
    manifest: dict[str, Any] = {
        "id": digest,
        "source": "snapshot:" + digest,
        "retrieved_at": now(),
        "origin": "user-import",
        "tabs": {},
        "rows": 0,
    }
    for name, rows in workbook.read(destination).items():
        modality = name if name in MODALITIES else "Other"
        headers = (
            {}
            if not rows
            else {re.sub(r"\d+$", "", a): c["value"] for a, c in rows[0]["cells"].items()}
        )
        counts: dict[str, int] = {}
        for h in headers.values():
            counts[str(h)] = counts.get(str(h), 0) + 1
        entries = rows[1:]
        for start in range(0, len(entries), 100):
            with store.write(registry) as db:
                for row in entries[start : start + 100]:
                    values = {}
                    for address, cell in row["cells"].items():
                        column = re.sub(r"\d+$", "", address)
                        heading = str(headers.get(column) or column)
                        if counts.get(heading, 0) > 1:
                            heading += " [" + column + "]"
                        if cell["value"] != "" or cell["formula"] is not None:
                            values[heading] = cell["value"]
                    add_row(
                        db,
                        digest,
                        name,
                        row["row"],
                        values,
                        row["cells"],
                        modality,
                        "snapshot:" + digest + ":" + name + ":" + str(row["row"]),
                        origin="user-import",
                    )
        manifest["tabs"][name] = {"rows": len(entries), "modality": modality}
        manifest["rows"] += len(entries)
    with store.write(registry) as db:
        db.execute("INSERT INTO workspace_imports VALUES(?,?)", (digest, canonical(manifest)))
        event(db, digest, "", "workbook-import", manifest=manifest)
    return manifest


def summary(root: Path, archived: bool = False) -> dict[str, Any]:
    with contextlib.closing(store.connect(root / "catalog")) as db:
        counts = [
            dict(r)
            for r in db.execute(
                "SELECT modality,status,count(*) AS count FROM workspace_rows GROU"
                "P BY modality,status"
            )
        ]
        species = [
            r[0]
            for r in db.execute(
                "SELECT DISTINCT species FROM workspace_rows WHERE species<>'' ORDER BY species"
            )
        ]
        imports = [loads(r[0]) for r in db.execute("SELECT body FROM workspace_imports")]
        campaigns = [
            loads(r[0]) | {"status": r[1]}
            for r in db.execute(
                "SELECT body,status FROM workspace_campaigns WHERE "
                "coalesce(json_extract(body,'$.archived'),0)=? ORDER BY rowid DESC LIMIT 20",
                (int(archived),),
            )
        ]
        for c in campaigns:
            c["query_count"] = len(c.pop("queries", []))
            for bulky in ("group", "ledger", "replayed_studies"):
                c.pop(bulky, None)
            c["counts"] = {
                r[0]: r[1]
                for r in db.execute(
                    "SELECT status,count(*) FROM workspace_items WHERE campaign=? GROUP BY status",
                    (c["id"],),
                )
            }
        revision = db.execute("SELECT coalesce(max(sequence),0) FROM workspace_events").fetchone()[
            0
        ]
    return {
        "counts": counts,
        "species": species,
        "imports": imports,
        "campaigns": campaigns,
        "revision": revision,
        "modalities": MODALITIES,
    }


def page(
    root: Path,
    modality: str = "Bulk ATAC",
    species: str = "",
    status: str = "",
    query: str = "",
    cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    if modality not in MODALITIES or not 1 <= limit <= 200 or len(query) > 200:
        raise ValueError("Invalid table filters")
    where, args = ["r.modality=?"], [modality]
    if species:
        where.append("r.species=?")
        args.append(species)
    if status:
        where.append("r.status=?")
        args.append(status)
    if query:
        where.append("(r.study LIKE ? OR r.body LIKE ?)")
        args.extend(["%" + query + "%"] * 2)
    clause = " AND ".join(where)
    with contextlib.closing(store.connect(root / "catalog")) as db:
        db.execute("BEGIN")
        total = db.execute(
            "SELECT count(*) FROM workspace_rows r WHERE " + clause, args
        ).fetchone()[0]
        selected = db.execute(
            (
                "SELECT r.*,p.body AS proposal FROM workspace_rows r LEFT JOIN cur"
                "ation_result_rows p ON p.subject=r.subject WHERE "
            )
            + clause
            + " AND r.id>? ORDER BY r.id LIMIT ?",
            (*args, cursor, limit + 1),
        ).fetchall()
        items = []
        for record in selected[:limit]:
            raw = loads(record["body"])
            original = raw["values"]
            current = loads(record["proposal"]) if record["proposal"] else {}
            values = dict(original)
            if record["status"] in {"Ready", "Reviewed"}:
                values.update({k: v for k, v in current.items() if v not in (None, "", [])})
            values.update(
                {
                    k: current.get(k, "")
                    for k in ("subject", "metadata_supported", "paper_coverage", "taxonomy_id")
                }
            )
            values["confidence"] = {
                "Ready": "Source-supported",
                "Reviewed": "Human-reviewed",
                "Imported": "Unassessed",
                "Discovered": "Unassessed",
                "Checking": "Checking",
                "Needs review": "Uncertain",
                "Excluded": "Excluded",
            }.get(record["status"], "Unassessed")
            items.append(
                {
                    "id": record["id"],
                    "entity": record["entity"],
                    "subject": record["subject"],
                    "status": record["status"],
                    "values": values,
                    "proposed": current,
                    "origin": raw["origin"],
                    "source_tab": record["tab"],
                    "source_row": record["row_number"],
                    "location": raw["location"],
                }
            )
    return {
        "items": items,
        "total": total,
        "next_cursor": selected[limit - 1]["id"] if len(selected) > limit else None,
    }


def history(root: Path, key: str) -> dict[str, Any]:
    with contextlib.closing(store.connect(root / "catalog")) as db:
        record = db.execute("SELECT * FROM workspace_rows WHERE id=?", (key,)).fetchone()
        if not record:
            raise ValueError("Unknown workspace record")
        raw = loads(record["body"])
        events = [
            dict(r) | {"body": loads(r["body"])}
            for r in db.execute(
                "SELECT sequence,created,body FROM workspace_events "
                "WHERE entity=? OR sequence=? OR (campaign=? AND (json_extract(body,'$.action')="
                "'workbook-import' OR json_extract(body,'$.action')='demo-import' "
                "OR (json_extract(body,'$.action')='search-completed' "
                "AND json_extract(body,'$.result.query')=?))) ORDER BY sequence",
                (
                    record["entity"],
                    raw["values"].get("search_event", -1),
                    record["batch"],
                    raw["values"].get("search_query", ""),
                ),
            )
        ]
        siblings = [
            dict(r)
            for r in db.execute(
                "SELECT id,tab,row_number FROM workspace_rows WHERE entity=?", (record["entity"],)
            )
        ]
    state = catalog.get(root / "catalog", record["subject"]) if record["subject"] else None
    changes = []
    if state:
        for f in state["fields"]:
            original_key = "species" if f["field"] == "organism" else f["field"]
            old = raw["values"].get(original_key)
            if old != f["value"]:
                changes.append(
                    {
                        "field": f["field"],
                        "before": old,
                        "after": f["value"],
                        "status": f["status"],
                        "evidence": f["evidence"],
                        "decision": state["reviews"].get(f["field"]),
                    }
                )
    return {
        "record": raw,
        "status": record["status"],
        "subject": record["subject"],
        "events": events,
        "changes": changes,
        "related_rows": siblings,
        "state": state,
        "curation_events": catalog.history(root / "catalog", record["subject"]) if state else [],
    }


def events(root: Path, campaign: str = "", after: int = 0) -> dict[str, Any]:
    with contextlib.closing(store.connect(root / "catalog")) as db:
        rows = [
            dict(r) | {"body": loads(r["body"])}
            for r in db.execute(
                (
                    "SELECT * FROM workspace_events WHERE sequence>? AND (?='' OR camp"
                    "aign=?) ORDER BY sequence LIMIT 100"
                ),
                (after, campaign, campaign),
            )
        ]
    return {"items": rows, "cursor": rows[-1]["sequence"] if rows else after}


def sync(db: sqlite3.Connection, state: dict[str, Any]) -> None:
    """Update only the indexed status; originals and human decisions remain immutable."""
    rows = db.execute(
        "SELECT entity FROM workspace_rows WHERE subject=? LIMIT 1", (state["id"],)
    ).fetchone()
    if not rows:
        return
    from . import assessments, eligibility

    fields = {f["field"]: f for f in state["fields"]}
    checks = assessments.assess(state, state["fields"])
    required = {
        "organism",
        "assay",
        "tissue",
        "genotype",
        "developmental_stage",
        "treatment",
        "library_layout",
    }
    approved = {k for k, v in state["reviews"].items() if v["action"] == "approve"}
    supported = set(approved)
    source_locations = {s["version"]: s["data"]["location"] for s in state["sources"]}
    for name, field in fields.items():
        check = checks.get(name, {})
        if (
            field["status"] == "known"
            and check.get("support") == "quoted_value"
            and not check.get("issues")
            and field["evidence"]
            and all(
                source_locations.get(c["source_version"], "").startswith(
                    (
                        "https://www.ebi.ac.uk/",
                        "https://eutils.ncbi.nlm.nih.gov/",
                        "https://www.ncbi.nlm.nih.gov/",
                    )
                )
                for c in field["evidence"]
            )
        ):
            supported.add(name)
    check = eligibility.classify(
        state.get("original", {}),
        {k: f["value"] for k, f in fields.items() if f["status"] == "known"},
    )
    status = (
        "Excluded"
        if check["eligibility"] == "excluded"
        else "Reviewed"
        if required.issubset(approved)
        else "Ready"
        if required.issubset(supported) and check["eligibility"] == "candidate"
        else "Needs review"
        if state.get("preparation_provenance")
        else "Checking"
    )
    db.execute("UPDATE workspace_rows SET status=? WHERE subject=?", (status, state["id"]))


def export_bundle(root: Path, key: str, clean: bool = False) -> dict[str, Any]:
    """Build a consistent Excel/JSON bundle outside the request thread."""
    import io
    import zipfile

    from . import results, studies

    tables: dict[str, list[dict[str, Any]]] = {m: [] for m in MODALITIES}
    with contextlib.closing(store.connect(root / "catalog")) as db:
        db.execute("BEGIN")
        signed: dict[str, dict[str, Any]] = {}
        releases: dict[str, str] = {}
        if clean:
            for entry in db.execute("SELECT body FROM curation_studies"):
                study = loads(entry[0])
                if not study.get("approval"):
                    continue
                release = db.execute(
                    "SELECT body FROM curation_releases WHERE version=? AND study=?",
                    (study["approval"], study["id"]),
                ).fetchone()
                if not release:
                    continue
                body = loads(release[0])
                if body["study_revision"] != studies.revision(
                    study, studies.members(db, study["id"])
                ):
                    continue
                releases[study["id"]] = study["approval"]
                signed.update({record["id"]: record["metadata"] for record in body["records"]})
        rows = db.execute(
            "SELECT r.*,p.body AS proposal FROM workspace_rows r LEFT JOIN cur"
            "ation_result_rows p ON r.subject=p.subject ORDER BY r.modality,r."
            "id"
        )
        for r in rows:
            if clean and (r["subject"] not in signed or r["status"] == "Excluded"):
                continue
            raw = loads(r["body"])
            values = dict(signed[r["subject"]] if clean else raw["values"])
            if not clean and r["proposal"] and r["status"] in {"Ready", "Reviewed"}:
                values.update(
                    {k: v for k, v in loads(r["proposal"]).items() if v not in (None, "", [])}
                )
            values.update(
                record_id=r["id"],
                status="Reviewed" if clean else r["status"],
                origin=raw["origin"],
                source_tab=r["tab"],
                source_row=r["row_number"],
                source_url=raw["location"],
            )
            tables[r["modality"]].append(values)
        raw_rows = [
            dict(r) | {"body": loads(r["body"])} for r in db.execute("SELECT * FROM workspace_rows")
        ]
        log = [
            dict(r) | {"body": loads(r["body"])}
            for r in db.execute("SELECT * FROM workspace_events ORDER BY sequence")
        ]
        history_rows = [
            dict(r)
            for r in db.execute(
                "SELECT e.subject,e.version,r.body FROM curation_events e JOIN rec"
                "ords r ON r.version=e.version AND r.kind='curation_event' WHERE e"
                ".subject IN (SELECT subject FROM workspace_rows) ORDER BY e.seque"
                "nce"
            )
        ]
        manifest = {
            "kind": "clean-metadata" if clean else "metadata-working-copy",
            "created": now(),
            "source_data_modified": False,
            "study_releases": releases,
            "rows": sum(map(len, tables.values())),
            "scientific_qc": "External QC columns retained; not rerun",
            "source_snapshots": [
                loads(r[0]) for r in db.execute("SELECT body FROM workspace_imports")
            ],
        }
    folder = root / "exports"
    folder.mkdir(exist_ok=True)
    truncated: list[dict[str, Any]] = []

    def spreadsheet_rows(
        name: str, rows: list[dict[str, Any]], full_file: str
    ) -> list[dict[str, Any]]:
        output = []
        for number, row in enumerate(rows, 2):
            shown = dict(row)
            for field, value in row.items():
                text = canonical(value) if isinstance(value, (list, dict)) else str(value or "")
                if len(text.encode("utf-16-le")) // 2 > 32767:
                    shown[field] = text[:16000] + f"\n[Full value in {full_file}]"
                    truncated.append({"sheet": name, "row": number, "column": field})
            output.append(shown)
        return output

    shown_tables = {
        name: spreadsheet_rows(name, rows, "metadata.json") for name, rows in tables.items()
    }
    shown_manifest = spreadsheet_rows(
        "Manifest", [{"property": k, "value": v} for k, v in manifest.items()], "manifest.json"
    )
    manifest["xlsx_truncated_cells"] = len(truncated)
    shown_manifest.append({"property": "xlsx_truncated_cells", "value": len(truncated)})
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("metadata.xlsx", results.xlsx(shown_tables, shown_manifest))
        archive.writestr("metadata.json", canonical(tables))
        archive.writestr("xlsx-truncations.json", canonical(truncated))
        archive.writestr("manifest.json", canonical(manifest))
        archive.writestr("originals.json", canonical(raw_rows))
        archive.writestr("discovery-history.json", canonical(log))
        archive.writestr("changes.json", canonical(history_rows))
    destination = folder / (key + ".zip")
    destination.write_bytes(stream.getvalue())
    return {
        "filename": key + ".zip",
        "rows": manifest["rows"],
        "url": "/api/workspace/exports/" + key,
    }


def import_demo(root: Path, path: Path) -> dict[str, Any]:
    """Load the current neutral JSONL demo format; no external source is contacted."""
    import json

    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    registry = root / "catalog"
    store.initialize(registry)
    with contextlib.closing(store.connect(registry)) as db:
        old = db.execute("SELECT body FROM workspace_imports WHERE id=?", (digest,)).fetchone()
        if old:
            return loads(old[0])
    with path.open() as stream:
        header = loads(stream.readline())
        if header.get("format") != "genesis-demo/1":
            raise ValueError("Expected genesis-demo/1 JSONL")
        manifest = {
            "id": digest,
            "source": "snapshot:" + digest,
            "origin": "demo-reference",
            "retrieved_at": now(),
            "rows": 0,
            "tabs": {},
            "source_snapshot": header["source_snapshot"],
        }
        with store.write(registry) as db:
            for line in stream:
                item = loads(line)
                if item["modality"] not in MODALITIES:
                    raise ValueError("Unknown demo modality")
                if any(
                    host in json.dumps(item) for host in ("docs.google.com", "sheets.google.com")
                ):
                    raise ValueError("Demo records must use snapshot identities, not workbook URLs")
                add_row(
                    db,
                    digest,
                    item["table"],
                    item["row"],
                    item["original"],
                    item.get("cells", {}),
                    item["modality"],
                    f"snapshot:{digest}:{item['table']}:{item['row']}",
                    origin="demo-reference",
                )
                counts = manifest["tabs"].setdefault(
                    item["table"], {"rows": 0, "modality": item["modality"]}
                )
                counts["rows"] += 1
                manifest["rows"] += 1
            if manifest["rows"] != header["rows"]:
                raise ValueError("Demo row count differs from its manifest")
            db.execute("INSERT INTO workspace_imports VALUES(?,?)", (digest, canonical(manifest)))
            event(db, digest, "", "demo-import", manifest=manifest)
    destination = root / "reference-snapshots" / (digest + ".jsonl")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, destination)
    return manifest


def activity_page(
    root: Path, campaign: str = "", after: int = 0, before: int = 0, archived: bool = False
) -> dict[str, Any]:
    if after < 0 or before < 0 or after and before:
        raise ValueError("Invalid activity cursor")
    where, args = ["1=1"], []
    if campaign:
        where.append("e.campaign=?")
        args.append(campaign)
    else:
        with contextlib.closing(store.connect(root / "catalog")) as db:
            archived_ids = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM workspace_campaigns WHERE json_extract(body,'$.archived')=1"
                )
            ]
        if archived_ids:
            where.append(
                "e.campaign "
                + ("IN" if archived else "NOT IN")
                + " ("
                + ",".join("?" for _ in archived_ids)
                + ")"
            )
            args.extend(archived_ids)
        elif archived:
            where.append("0=1")
    if after:
        where.append("e.sequence>?")
        args.append(after)
    if before:
        where.append("e.sequence<?")
        args.append(before)
    with contextlib.closing(store.connect(root / "catalog")) as db:
        rows = db.execute(
            "SELECT e.* FROM workspace_events e WHERE "
            + " AND ".join(where)
            + " ORDER BY e.sequence "
            + ("ASC" if after else "DESC")
            + " LIMIT 50",
            args,
        ).fetchall()
    items = []
    for row in rows:
        body = loads(row["body"])
        query = body.get("query", {})
        result = body.get("result", {})
        items.append(
            {
                "sequence": row["sequence"],
                "created": row["created"],
                "campaign": row["campaign"],
                "action": body["action"],
                "source": query.get("source", "") if isinstance(query, dict) else "",
                "query": (query.get("query", "") if isinstance(query, dict) else str(query))[:300],
                "accession": str(body.get("accession", ""))[:80],
                "hits": len(result.get("rows", [])) if isinstance(result, dict) else 0,
                "error": str(body.get("error", ""))[:300],
            }
        )
    items.sort(key=lambda item: item["sequence"])
    return {
        "items": items,
        "cursor": items[-1]["sequence"] if items else after,
        "before": items[0]["sequence"] if len(items) == 50 else None,
    }


def activity_detail(root: Path, sequence: int) -> dict[str, Any]:
    with contextlib.closing(store.connect(root / "catalog")) as db:
        row = db.execute("SELECT * FROM workspace_events WHERE sequence=?", (sequence,)).fetchone()
    if not row:
        raise ValueError("Unknown activity")
    return dict(row) | {"body": loads(row["body"])}


def selection(root: Path, body: dict[str, Any], *, internal: bool = False) -> dict[str, Any]:
    """Persist a previewed selection; large cohorts never travel through the browser."""
    ids = body.get("ids", [])
    if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids) or len(ids) > 200:
        raise ValueError("Select at most 200 explicit rows, or use filtered results")
    filters = body.get("filters")
    with contextlib.closing(store.connect(root / "catalog")) as db:
        if body.get("selection_id"):
            if ids or filters:
                raise ValueError("Use a saved selection or new filters, not both")
            saved = db.execute(
                "SELECT body FROM workspace_selections WHERE id=?", (body["selection_id"],)
            ).fetchone()
            if not saved:
                raise ValueError("Unknown selection; preview the cohort again")
            ids = loads(saved[0])["ids"]
        elif filters is not None:
            if ids or not isinstance(filters, dict) or filters.get("modality") not in MODALITIES:
                raise ValueError("Choose one data type and explicit filters")
            where, args = ["modality=?"], [filters["modality"]]
            for field in ("species", "status"):
                if value := filters.get(field):
                    if not isinstance(value, str) or len(value) > 200:
                        raise ValueError("Invalid filter")
                    where.append(field + "=?")
                    args.append(value)
            if query := filters.get("query"):
                if not isinstance(query, str) or len(query) > 200:
                    raise ValueError("Invalid search")
                where.append("(study LIKE ? OR body LIKE ?)")
                args.extend(["%" + query + "%"] * 2)
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM workspace_rows WHERE "
                    + " AND ".join(where)
                    + " ORDER BY id LIMIT 50001",
                    args,
                )
            ]
        if not ids:
            raise ValueError("Select records or narrow the table filters first")
        if len(ids) > 50000:
            raise ValueError("Preview at most 50,000 initial rows; narrow the filters")
        packed = canonical(sorted(set(ids)))
        found = db.execute(
            "SELECT count(*) FROM workspace_rows WHERE id IN (SELECT value FROM json_each(?))",
            (packed,),
        ).fetchone()[0]
        if found != len(set(ids)):
            raise ValueError("Selected records no longer exist")
        rows = db.execute(
            "SELECT entity,min(id) AS representative,study FROM workspace_rows WHERE "
            "id IN (SELECT value FROM json_each(?)) OR (study!='' AND study IN "
            "(SELECT study FROM workspace_rows WHERE id IN (SELECT value FROM json_each(?)))) "
            "GROUP BY entity ORDER BY entity",
            (packed, packed),
        ).fetchall()
    if len(rows) > 50000:
        raise ValueError("Expanded study selection exceeds 50,000 records; narrow the filters")
    token = fingerprint([r["entity"] for r in rows])
    key = fingerprint([packed, token])
    result = {
        "ids": ids,
        "selection_id": key,
        "records": len(rows),
        "studies": len({r["study"] or r["entity"] for r in rows}),
        "scope_token": token,
    }
    with store.write(root / "catalog") as db:
        db.execute(
            "INSERT OR IGNORE INTO workspace_selections VALUES(?,?)", (key, canonical(result))
        )
    return (
        result if internal or len(ids) <= 200 else {k: v for k, v in result.items() if k != "ids"}
    )
