"""Cached, exact NCBI plant taxonomy resolution, separate from ontology assertions."""

from __future__ import annotations

import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from ..contracts.records import fingerprint
from ..curation import harmonize
from ..registry import store
from . import catalog, intake, mining_sources


def resolve(
    root: Path, rows: list[dict[str, Any]], actor: str, cancel: threading.Event | None = None
) -> dict[str, Any]:
    names = {
        str(f["value"])
        for r in rows
        for f in r["fields"]
        if f["field"] == "organism" and f["status"] == "known"
    }
    resolved: dict[str, Any] = {}
    for name in sorted(names):
        if cancel and cancel.is_set():
            raise ValueError("Taxonomy resolution cancelled")
        try:
            taxid = mining_sources.taxon(root, name, cancel)
            url = (
                mining_sources.EUTILS
                + "efetch.fcgi?"
                + urlencode({"db": "taxonomy", "id": taxid, "retmode": "xml"})
            )
            source = intake.fetch(root, url, cancel)
            tree = ET.fromstring(source["text"])
            item = tree.find("Taxon")
            if (
                item is None
                or item.findtext("ScientificName") != name
                or not any(t.findtext("TaxId") == "33090" for t in item.findall("LineageEx/Taxon"))
            ):
                raise ValueError("Exact plant taxonomy identity not verified")
            resolved[name] = {
                "status": "verified",
                "name": name,
                "id": "NCBITaxon:" + str(taxid),
                "url": url,
                "retrieved_at": source["retrieved_at"],
                "source_hash": fingerprint(source["text"]),
                "source": harmonize.source_record(source["text"], url, "application/xml"),
            }
        except (ValueError, OSError, ET.ParseError) as error:
            resolved[name] = {"status": "unresolved", "name": name, "reason": str(error)[:500]}
    with store.write(root / "catalog") as db:
        for row in rows:
            name = next(
                (
                    str(f["value"])
                    for f in row["fields"]
                    if f["field"] == "organism" and f["status"] == "known"
                ),
                "",
            )
            if name not in resolved:
                continue
            state = catalog.read(db, row["id"])
            if state["version"] != row["version"]:
                raise ValueError("Metadata changed during taxonomy resolution; retry")
            resolution = resolved[name]
            if state.get("taxonomy") != resolution:
                # intake.fetch retains the snapshot; this event binds its hash to the subject.
                state["taxonomy"] = resolution
                catalog.save(db, state, "taxonomy", actor, resolution)
    return resolved
