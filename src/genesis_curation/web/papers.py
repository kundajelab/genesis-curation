"""Bounded originating-paper lookup and explicit sample-to-passage bindings."""

from __future__ import annotations

import re
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from ..contracts.records import canonical, fingerprint
from . import catalog, intake, mining_documents, mining_sources


def aliases(rows: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Only unique, informative archive labels can attribute a paper paragraph."""
    owners: dict[str, set[str]] = {}
    for row in rows:
        raw = row["original"]
        values = set(mining_sources.ACCESSIONS.findall(canonical(raw)))
        for key in ("sample_name", "sample_title", "library_name", "alias"):
            label = str(raw.get(key, "")).strip()
            if 5 <= len(label) <= 120 and re.search(r"\d|[_-]", label):
                values.add(label)
        for source in row["sources"]:
            if row.get("source_scopes", {}).get(source["version"]) != "study_context":
                values.update(mining_sources.ACCESSIONS.findall(source["data"]["snapshot"]))
        for value in values:
            if not value.startswith(("GSE", "SRP", "ERP", "DRP", "PRJ")):
                owners.setdefault(value, set()).add(row["id"])
    return {value: subjects for value, subjects in owners.items() if len(subjects) == 1}


def bind(rows: list[dict[str, Any]], text: str) -> dict[str, list[dict[str, str]]]:
    known = aliases(rows)
    matched: dict[str, list[dict[str, str]]] = {}
    for number, paragraph in enumerate(text.splitlines(), 1):
        hits = {
            a: ids
            for a, ids in known.items()
            if re.search(r"(?<![\w])" + re.escape(a) + r"(?![\w])", paragraph)
        }
        # Multiple sample labels in one paragraph do not attribute individual values.
        subjects = set().union(*hits.values()) if hits else set()
        if len(subjects) == 1:
            sid = next(iter(subjects))
            matched.setdefault(sid, []).append(
                {
                    "locator": f"line:{number}",
                    "quote": paragraph,
                    "alias": ", ".join(sorted(hits)),
                    "binding": "unique exact archive identity/label",
                }
            )
    return matched


def enrich(
    root: Path, study: dict[str, Any], actor: str, cancel: threading.Event | None = None
) -> dict[str, Any]:
    rows = study["records"]
    needs = [
        r
        for r in rows
        if any(
            f["status"] in {"unknown", "conflicting"}
            and f["field"]
            in {
                "tissue",
                "genotype",
                "treatment",
                "developmental_stage",
                "library_selection",
                "library_source",
                "library_preparation",
                "strandedness",
                "age",
                "age_unit",
            }
            and f["field"] not in r["reviews"]
            for f in r["fields"]
        )
    ]
    if not needs:
        return {"status": "not-needed", "documents": [], "changed": []}
    accession = study.get("accession") or ""
    texts = [s["data"]["snapshot"] for r in rows for s in r["sources"]]
    ledger: list[dict[str, Any]] = []
    if re.fullmatch(r"(?:GSE|[SED]RP|PRJ[EDN][AB])\d+", accession):
        url = (
            intake.geo_url(accession)
            if accession.startswith("GSE")
            else ("https://www.ebi.ac.uk/ena/browser/api/xml/" + accession)
        )
        try:
            texts.append(intake.fetch(root, url, cancel)["text"])
        except (ValueError, OSError) as error:
            ledger.append({"url": url, "status": "unavailable", "reason": str(error)[:500]})
    pmids = set()
    for text in texts:
        pmids.update(re.findall(r"!Series_pubmed_id\s*=\s*(\d+)", text))
        pmids.update(re.findall(r"pubmed(?:\.ncbi\.nlm\.nih\.gov)?[/=: ]+(\d{4,})", text, re.I))
        try:
            tree = ET.fromstring(text)
        except ET.ParseError:
            continue
        for node in tree.findall(".//XREF_LINK"):
            if node.findtext("DB", "").lower() == "pubmed":
                value = node.findtext("ID", "")
                if value.isdigit():
                    pmids.add(value)
    docs: list[dict[str, Any]] = []
    for pmid in sorted(pmids)[:3]:
        found, attempts = intake.articles(root, "!Series_pubmed_id = " + pmid, cancel)
        ledger.extend({**a, "relationship": "archive-linked", "pmid": pmid} for a in attempts)
        docs.extend(found)
    # Literature search finds leads, not an originating-paper assertion.
    if not pmids and re.fullmatch(r"(?:GSE|[SED]RP|PRJ[EDN][AB])\d+", accession):
        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urlencode(
            {
                "query": '"' + accession + '"',
                "format": "json",
                "resultType": "core",
                "pageSize": 5,
            }
        )
        try:
            import json

            found = json.loads(intake.fetch(root, url, cancel)["text"])
            for candidate in found.get("resultList", {}).get("result", []):
                pmc = candidate.get("pmcid", "")
                entry = {
                    "pmid": candidate.get("pmid"),
                    "pmcid": pmc,
                    "title": candidate.get("title"),
                    "relationship": "unverified-citation",
                }
                ledger.append(entry)
                if not re.fullmatch(r"PMC\d+", pmc):
                    continue
                full_url = (
                    "https://www.ebi.ac.uk/europepmc/webservices/rest/" + pmc + "/fullTextXML"
                )
                try:
                    raw = intake.fetch(root, full_url, cancel)
                    tree = ET.fromstring(raw["text"])
                    declared = [
                        s
                        for s in tree.iter("sec")
                        if re.search(
                            r"data availab|data access|data deposit", s.findtext("title", ""), re.I
                        )
                        and accession in " ".join(s.itertext())
                        and re.search(
                            r"deposit|generat|available|accession", " ".join(s.itertext()), re.I
                        )
                    ]
                    if not declared:
                        continue
                    text = "\n".join(" ".join(p.itertext()).strip() for p in tree.iter("p"))
                    entry.update(
                        relationship="explicit-data-availability",
                        url=full_url,
                        declaration=" ".join(declared[0].itertext()),
                    )
                    docs.append({**raw, "text": text})
                except (ValueError, OSError, ET.ParseError) as error:
                    entry.update(status="unavailable", reason=str(error)[:500])
        except (ValueError, OSError) as error:
            ledger.append({"url": url, "status": "unavailable", "reason": str(error)[:500]})
    changed = set()
    for doc in docs:
        bindings = bind(rows, doc["text"])
        entry = {
            "url": doc["url"],
            "sha256": fingerprint(doc["text"]),
            "bindings": bindings,
            "unbound_subjects": len(rows) - len(bindings),
        }
        ledger.append(entry)
        for state in needs:
            if state["reviews"]:
                continue  # New evidence must not silently overwrite reviewed records.
            passages = bindings.get(state["id"], [])
            if not passages:
                continue
            before = catalog.get(root / "catalog", state["id"])["version"]
            intake.attach(
                root / "catalog",
                state["id"],
                "\n".join(p["quote"] for p in passages),
                doc["url"],
                "sample",
                actor,
            )
            if catalog.get(root / "catalog", state["id"])["version"] != before:
                changed.add(state["id"])
    if accession.startswith("GSE"):
        candidate = {"accession": accession, "files": []}
        try:
            ledger.extend(
                mining_documents.gather(
                    root, candidate, [r for r in needs if not r["reviews"]], actor, cancel
                )
            )
        except (ValueError, OSError) as error:
            ledger.append(
                {
                    "status": "unavailable",
                    "source": "GEO supplementary metadata",
                    "reason": str(error)[:500],
                }
            )
        for state in needs:
            if catalog.get(root / "catalog", state["id"])["version"] != state["version"]:
                changed.add(state["id"])
    return {
        "status": "retrieved" if docs else "unavailable",
        "documents": ledger,
        "changed": sorted(changed),
        "unresolved": len(needs),
    }
