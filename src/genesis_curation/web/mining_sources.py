"""Fixed public archive tools for discovery, cross-links and file inventories."""

from __future__ import annotations

import json
import re
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from ..contracts.records import fingerprint
from . import intake
from .discovery_catalog import ASSAYS as RECIPES

SPECIES = {"Arabidopsis thaliana": 3702, "Sorghum bicolor": 4558}

ASSAYS = {k: v["strategies"][0] for k, v in RECIPES.items()}
GEO_ASSAYS = {
    k: "(" + " OR ".join('"' + t + '"' for t in v["terms"]) + ")" for k, v in RECIPES.items()
}


def valid_species_name(name: object) -> bool:
    return isinstance(name, str) and bool(re.fullmatch(r"[A-Z][A-Za-z0-9 .×_-]{1,120}", name))


def taxon(root: Path, name: str, cancel: threading.Event | None) -> int:
    """Require exact scientific name and plant ancestry; ambiguous names fail closed."""
    if name in SPECIES:
        return SPECIES[name]
    if not valid_species_name(name):
        raise ValueError("Use an exact plant scientific name")
    found, _ = json_source(
        root,
        EUTILS
        + "esearch.fcgi?"
        + urlencode(
            {
                "db": "taxonomy",
                "term": f'"{name}"[Scientific Name]',
                "retmode": "json",
            }
        ),
        cancel,
    )
    ids = found["esearchresult"]["idlist"]
    if len(ids) != 1:
        raise ValueError("Plant taxonomy name is unresolved or ambiguous: " + name)
    source = intake.fetch(
        root,
        EUTILS
        + "efetch.fcgi?"
        + urlencode(
            {
                "db": "taxonomy",
                "id": ids[0],
                "retmode": "xml",
            }
        ),
        cancel,
    )
    document = ET.fromstring(source["text"])
    item = document.find("Taxon")
    if (
        item is None
        or item.findtext("ScientificName") != name
        or not any(
            ancestor.findtext("TaxId") == "33090" for ancestor in item.findall("LineageEx/Taxon")
        )
    ):
        raise ValueError("Taxonomy did not verify the requested plant species: " + name)
    return int(ids[0])


EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
ENA = "https://www.ebi.ac.uk/ena/portal/api/"
ACCESSIONS = re.compile(r"\b(?:GSE|GSM|[SED]R[PSRX]|PRJ[EDN][AB]|SAM[END][A-Z]?)\d+\b")


def json_source(root: Path, url: str, cancel: threading.Event | None) -> tuple[Any, dict[str, Any]]:
    source = intake.fetch(root, url, cancel)
    return json.loads(source["text"]), source


def queries(species: list[str], assays: list[str], terms: list[str]) -> list[dict[str, Any]]:
    return [
        {"source": source, "species": organism, "assay": assay, "terms": terms, "round": 0}
        for organism in species
        for assay in assays
        for source in ["geo", "ena"]
    ]


def search(
    root: Path, query: dict[str, Any], cancel: threading.Event | None = None
) -> dict[str, Any]:
    "One bounded page per call. Counts/cursors are retained, never claimed exhaustive."
    species, assay = query["species"], query["assay"]
    if not valid_species_name(species) or assay not in ASSAYS:
        raise ValueError("Unsupported plant species or assay")
    tax_id = taxon(root, species, cancel)
    terms = [str(t).replace('"', " ")[:100] for t in query.get("terms", [])][:8]
    offset = int(query.get("offset", 0))
    if query["source"] == "geo":
        assay_term = GEO_ASSAYS[assay]
        term = f'"{species}"[Organism] AND {assay_term} AND gse[Entry Type]'
        if terms:
            term += " AND (" + " OR ".join('"' + t + '"' for t in terms) + ")"
        found, source = json_source(
            root,
            EUTILS
            + "esearch.fcgi?"
            + urlencode(
                {"db": "gds", "term": term, "retmode": "json", "retmax": 25, "retstart": offset}
            ),
            cancel,
        )
        search_result = found["esearchresult"]
        if "error" in search_result:
            raise ValueError(str(search_result["error"]))
        ids = search_result["idlist"]
        rows = []
        sources = [source]
        if ids:
            response, detail = json_source(
                root,
                EUTILS
                + "esummary.fcgi?"
                + urlencode({"db": "gds", "id": ",".join(ids), "retmode": "json"}),
                cancel,
            )
            sources.append(detail)
            for uid in ids:
                item = response["result"][uid]
                accession = item.get("accession", "")
                if re.fullmatch(r"GSE\d+", accession):
                    rows.append(
                        {
                            "accession": accession,
                            "title": item.get("title", accession),
                            "summary": item.get("summary", ""),
                            "species": item.get("taxon", ""),
                            "url": intake.geo_url(accession),
                            "search_assay": assay,
                        }
                    )
        total = int(search_result["count"])
        return {
            "candidates": rows,
            "sources": sources,
            "total": total,
            "next_offset": offset + len(ids) if ids and offset + len(ids) < total else None,
            "query": term,
        }
    expression = (
        f"tax_tree({tax_id}) AND ("
        + " OR ".join('library_strategy="' + v + '"' for v in RECIPES[assay]["strategies"])
        + ")"
    )
    if assay == "TSS":
        expression = (
            f'tax_eq({tax_id}) AND (library_strategy="OTHER" OR library_strategy="RNA-Seq")'
        )
    # ENA free-text fields vary by result type. Tissue/condition are evaluated from
    # retrieved evidence rather than silently turning a failed search into no matches.
    url = (
        ENA
        + "search?"
        + urlencode(
            {
                "result": "read_run",
                "query": expression,
                "format": "json",
                "limit": 100,
                "offset": offset,
                "fields": "study_accession,secondary_study_accession,study_title,"
                "scientific_name,library_strategy",
            }
        )
    )
    rows, source = json_source(root, url, cancel)
    if not isinstance(rows, list):
        raise ValueError("ENA search did not return a record list")
    candidates = {}
    for row in rows:
        accession = row.get("study_accession", "")
        if intake.ACCESSION.fullmatch(accession):
            candidates[accession] = {
                "accession": accession,
                "title": row.get("study_title", accession),
                "summary": json.dumps(row),
                "species": row.get("scientific_name", ""),
                "url": url,
                "search_assay": assay,
                "aliases": [v for v in [row.get("secondary_study_accession")] if v],
            }
    return {
        "candidates": list(candidates.values()),
        "sources": [source],
        "total": None,
        "next_offset": offset + len(rows) if len(rows) == 100 else None,
        "query": expression,
    }


def inspect(root: Path, accession: str, cancel: threading.Event | None = None) -> dict[str, Any]:
    if not intake.ACCESSION.fullmatch(accession):
        raise ValueError("Invalid archive accession")
    if accession.startswith("GSE"):
        source = intake.fetch(root, intake.geo_url(accession), cancel)
        # Relation lines assert archive links; arbitrary mentions in a paper do not.
        aliases = []
        for line in source["text"].splitlines():
            if line.startswith("!Series_relation"):
                aliases.extend(ACCESSIONS.findall(line))
        return {"sources": [source], "aliases": [], "relations": aliases, "text": source["text"]}
    source = intake.fetch(root, "https://www.ebi.ac.uk/ena/browser/api/xml/" + accession, cancel)
    try:
        tree = ET.fromstring(source["text"])
    except ET.ParseError as error:
        raise ValueError("Archive returned malformed study XML") from error
    aliases = [
        n.text
        for n in tree.findall(".//IDENTIFIERS/*")
        if n.text and intake.ACCESSION.fullmatch(n.text)
    ]
    return {"sources": [source], "aliases": aliases, "text": " ".join(tree.itertext())}


def inventory(root: Path, accession: str, cancel: threading.Event | None = None) -> dict[str, Any]:
    if not intake.ACCESSION.fullmatch(accession):
        raise ValueError("Invalid archive accession")
    fields = [
        "run_accession",
        "sample_accession",
        "experiment_accession",
        "study_accession",
        "secondary_sample_accession",
        "scientific_name",
        "library_strategy",
        "library_layout",
        "fastq_ftp",
        "fastq_md5",
        "fastq_bytes",
        "submitted_ftp",
        "submitted_md5",
        "submitted_bytes",
    ]
    rows, source = json_source(
        root,
        ENA
        + "filereport?"
        + urlencode(
            {
                "accession": accession,
                "result": "read_run",
                "fields": ",".join(fields),
                "format": "json",
            }
        ),
        cancel,
    )
    if not isinstance(rows, list):
        raise ValueError("ENA file report must be a list")
    files = []
    for row in rows:
        for prefix in ["fastq", "submitted"]:
            locations = row.get(prefix + "_ftp", "").split(";")
            sums = row.get(prefix + "_md5", "").split(";")
            sizes = row.get(prefix + "_bytes", "").split(";")
            for i, location in enumerate(locations):
                if not location:
                    continue
                files.append(
                    {
                        "location": location,
                        "format": prefix,
                        "run": row.get("run_accession"),
                        "sample": row.get("sample_accession"),
                        "experiment": row.get("experiment_accession"),
                        "md5": sums[i] if i < len(sums) and sums[i] else None,
                        "bytes": int(sizes[i]) if i < len(sizes) and sizes[i].isdigit() else None,
                        "availability": "archive-reported",
                        "download_verified": False,
                        "source_hash": fingerprint(source["text"]),
                        "retrieved_at": source["retrieved_at"],
                    }
                )
    return {"rows": rows, "files": files, "source": source}


def evidence_links(text: str) -> list[str]:
    """Only accession-bearing archive relations, not arbitrary URLs or article claims."""
    result = []
    for line in text.splitlines():
        if "relation" in line.lower():
            result.extend(ACCESSIONS.findall(line))
    return list(dict.fromkeys(result))


def investigate(
    root: Path, accession: str, inspected: dict[str, Any], cancel: threading.Event | None = None
) -> dict[str, Any]:
    """Fetch a bounded protocol sample before escalating missing modality evidence."""
    urls = []
    if accession.startswith("GSE"):
        samples = list(
            dict.fromkeys(re.findall(r"!Series_sample_id\s*=\s*(GSM\d+)", inspected["text"]))
        )[:2]
        urls = [intake.geo_url(sample) for sample in samples]
    else:
        report = inventory(root, accession, cancel)
        experiments = list(
            dict.fromkeys(row.get("experiment_accession", "") for row in report["rows"])
        )[:2]
        urls = [
            "https://www.ebi.ac.uk/ena/browser/api/xml/" + key
            for key in experiments
            if re.fullmatch(r"[SED]RX\d+", key)
        ]
    texts, ledger = [], []
    for url in urls:
        try:
            source = intake.fetch(root, url, cancel)
            if "/xml/" in url:
                tree = ET.fromstring(source["text"])
                text = " ".join(tree.itertext())
            else:
                text = "\n".join(
                    line for line in source["text"].splitlines() if line.startswith("!Sample_")
                )
            texts.append(text[:8000])
            ledger.append(
                {
                    "url": url,
                    "hash": fingerprint(source["text"]),
                    "retrieved_at": source["retrieved_at"],
                    "status": "retrieved",
                    "sampled_protocol": True,
                }
            )
        except (ValueError, OSError, ET.ParseError) as error:
            ledger.append({"url": url, "status": "unavailable", "reason": str(error)[:500]})
    return {"text": "\n".join(texts), "ledger": ledger}
