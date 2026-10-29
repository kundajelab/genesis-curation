"""Bounded, cached public metadata retrieval; never fetch sequencing files."""

from __future__ import annotations

import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

from ..contracts.records import canonical, dump, fingerprint, load, now
from ..curation import harmonize
from ..llm import providers
from . import catalog, studies

HOSTS = {"www.ncbi.nlm.nih.gov", "eutils.ncbi.nlm.nih.gov", "www.ebi.ac.uk"}
ACCESSION = re.compile(r"(?:GSE|GSM|[SED]RP|[SED]RS|[SED]RR|[SED]RX|PRJ[EDN][AB])\d{3,12}")


def fetch(root: Path, url: str, cancellation: threading.Event | None = None) -> dict[str, Any]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in HOSTS or parsed.username or parsed.port:
        raise ValueError("Only fixed public metadata services are supported")
    cache = root / "source-cache" / (fingerprint(url) + ".json")
    if cache.exists():
        cached = load(cache)
        if (
            datetime.now(UTC) - datetime.fromisoformat(cached["retrieved_at"])
        ).total_seconds() < 86400:
            return retain(root, cached)
    # Stay below the unauthenticated NCBI request-rate limit in this serial worker.
    if parsed.hostname.endswith("ncbi.nlm.nih.gov"):
        if cancellation:
            cancellation.wait(0.35)
        else:
            time.sleep(0.35)
    error: Exception | None = None
    for attempt in range(3):
        if cancellation and cancellation.is_set():
            raise ValueError("Retrieval cancelled")
        try:
            with build_opener(providers.NoRedirect()).open(
                Request(url, headers={"User-Agent": "Genesis-curation/1"}), timeout=25
            ) as response:
                raw = response.read(harmonize.MAX_SOURCE + 1)
            if len(raw) > harmonize.MAX_SOURCE:
                raise ValueError(
                    "Metadata source exceeds 2 MiB; provide a selected text/table excerpt"
                )
            result = {"url": url, "text": raw.decode("utf-8"), "retrieved_at": now()}
            dump(cache, result)
            return retain(root, result)
        except (OSError, UnicodeDecodeError) as exc:
            if isinstance(exc, HTTPError) and 400 <= exc.code < 500 and exc.code != 429:
                raise ValueError(f"Metadata retrieval failed: HTTP {exc.code}") from exc
            error = exc
            delay = min(attempt + 1, 3)
            if isinstance(exc, HTTPError) and exc.code in {429, 503}:
                retry = exc.headers.get("Retry-After", "")
                delay = min(30, int(retry)) if retry.isdigit() else 3 * (attempt + 1)
            if cancellation:
                cancellation.wait(delay)
            else:
                time.sleep(delay)
    raise ValueError(f"Metadata retrieval failed: {error}")


def retain(root: Path, source: dict[str, Any]) -> dict[str, Any]:
    """URL caches may refresh; cited content snapshots never disappear on refresh."""
    dump(
        root / "source-snapshots" / (fingerprint(source["text"]) + ".json"),
        {"text": source["text"]},
        immutable=True,
    )
    return source


def geo_url(accession: str) -> str:
    return "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?" + urlencode(
        {"acc": accession, "targ": "self", "form": "text", "view": "full"}
    )


def geo_row(text: str, accession: str) -> dict[str, str]:
    row = {"accession": accession}
    names = {
        "organism_ch1": "organism",
        "source_name_ch1": "source_description",
        "library_strategy": "assay",
        "relation": "relations",
    }
    for line in text.splitlines():
        match = re.match(r"!Sample_([a-zA-Z_0-9]+)\s*=\s*(.*)", line)
        if not match:
            continue
        key, value = match.groups()
        if key == "characteristics_ch1" and ":" in value:
            key, value = value.split(":", 1)
            key = key.strip().lower().replace(" ", "_")
        key, value = names.get(key, key), value.strip()
        # Preserve duplicate attributes instead of overwriting contradictory observations.
        if key in row and row[key] != value:
            row["additional_" + key] = value
        else:
            row[key] = value
    return row


def attach(root: Path, subject: str, text: str, url: str, scope: str, actor: str) -> None:
    state = catalog.get(root, subject)
    if any(
        s["data"]["location"] == url and s["data"]["snapshot"] == text for s in state["sources"]
    ):
        return
    if state["reviews"]:
        raise ValueError("Existing human review preserved; attach changed evidence explicitly")
    catalog.attach(root, subject, state["version"], text, url, "public", actor, scope=scope)


def articles(
    root: Path, study_text: str, cancellation: threading.Event | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    documents, ledger = [], []
    pmids = sorted(set(re.findall(r"!Series_pubmed_id\s*=\s*(\d+)", study_text)))[:5]
    for pmid in pmids:
        urls = [
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?"
            + urlencode({"db": "pubmed", "id": pmid, "retmode": "xml"})
        ]
        for url in urls:
            try:
                source = fetch(root, url, cancellation)
                tree = ET.fromstring(source["text"])
                for node in (
                    tree.findall(".//PubmedData/ArticleIdList/ArticleId") if url == urls[0] else []
                ):
                    if node.attrib.get("IdType") == "pmc" and re.fullmatch(
                        r"PMC\d+", node.text or ""
                    ):
                        full = (
                            "https://www.ebi.ac.uk/europepmc/webservices/rest/"
                            + str(node.text)
                            + "/fullTextXML"
                        )
                        if full not in urls:
                            urls.append(full)
                paragraphs = [
                    " ".join(n.itertext()).strip()
                    for n in tree.iter()
                    if n.tag in {"ArticleTitle", "AbstractText", "article-title", "p", "table-wrap"}
                ]
                if not paragraphs:
                    raise ValueError("No article text available")
                documents.append({**source, "text": "\n".join(paragraphs)})
                ledger.append({"url": url, "status": "retrieved"})
            except (ValueError, OSError, ET.ParseError) as exc:
                ledger.append({"url": url, "status": "unavailable", "reason": str(exc)[:500]})
    return documents, ledger


def import_accession(
    root: Path,
    accession: str,
    actor: str,
    cancellation: threading.Event | None = None,
    sample_limit: int | None = None,
    sample_offset: int = 0,
) -> str:
    if sample_offset < 0:
        raise ValueError("Sample offset must be nonnegative")
    accession = accession.strip().upper()
    if not ACCESSION.fullmatch(accession):
        raise ValueError("Use a GEO, ENA/SRA or BioProject accession")
    registry = root / "catalog"
    ids: list[str] = []
    failures: dict[str, str] = {}
    ledger: list[dict[str, str]] = []
    available_records = 0
    if accession.startswith(("GSE", "GSM")):
        source = fetch(root, geo_url(accession), cancellation)
        samples = (
            [accession]
            if accession.startswith("GSM")
            else list(
                dict.fromkeys(re.findall(r"!Series_sample_id\s*=\s*(GSM\d+)", source["text"]))
            )
        )
        if not samples:
            raise ValueError("GEO study returned no sample identifiers")
        available_records = len(samples)
        if sample_limit is not None:
            if not 1 <= sample_limit <= 1000:
                raise ValueError("Pilot sample limit must be 1–1000")
            samples = samples[sample_offset : sample_offset + sample_limit]
        docs, ledger = articles(root, source["text"], cancellation)
        ledger.insert(0, {"url": source["url"], "status": "retrieved"})
        for sample in samples:
            if cancellation and cancellation.is_set():
                raise ValueError(
                    "Import interrupted; retry uses cached sources and existing records"
                )
            sid = catalog.import_rows(
                registry,
                canonical([{"accession": sample, "study_accession": accession}]),
                sample + ".json",
                accession,
                "accession",
                "public",
                actor,
            )[0]
            ids.append(sid)
            try:
                raw = source if sample == accession else fetch(root, geo_url(sample), cancellation)
                attach(registry, sid, raw["text"], raw["url"], "sample", actor)
                ledger.append({"url": raw["url"], "status": "retrieved"})
                for doc in docs:
                    attach(registry, sid, doc["text"], doc["url"], "study_context", actor)
            except (ValueError, OSError) as exc:
                failures[sid] = str(exc)[:1000]
                ledger.append(
                    {"url": geo_url(sample), "status": "unavailable", "reason": str(exc)[:500]}
                )
    else:
        url = "https://www.ebi.ac.uk/ena/portal/api/filereport?" + urlencode(
            {
                "accession": accession,
                "result": "read_run",
                "format": "json",
                "fields": "run_accession,sample_accession,study_accession,experiment_accession,"
                "scientific_name,tax_id,library_strategy,library_layout,sample_title",
            }
        )
        source = fetch(root, url, cancellation)
        rows = json.loads(source["text"])
        if not isinstance(rows, list) or not rows:
            raise ValueError("ENA accession returned no run metadata")
        available_records = len(rows)
        if sample_limit is not None:
            if not 1 <= sample_limit <= 1000:
                raise ValueError("Pilot sample limit must be 1–1000")
            rows = rows[sample_offset : sample_offset + sample_limit]
        # Import in bounded batches; the study itself has no 1000-record ceiling.
        for start in range(0, len(rows), 500):
            ids.extend(
                catalog.import_rows(
                    registry,
                    canonical(rows[start : start + 500]),
                    accession + ".json",
                    accession,
                    "run_accession",
                    "public",
                    actor,
                )
            )
        ledger.append({"url": url, "status": "retrieved"})
        for sid in ids:
            if cancellation and cancellation.is_set():
                raise ValueError("Import interrupted; retry reuses cached metadata")
            state = catalog.get(registry, sid)
            sample = str(state["original"].get("sample_accession", ""))
            if not re.fullmatch(r"(?:[SED]RS|SAM[END][A-Z]?)\d+", sample):
                failures[sid] = (
                    "Sample accession is unavailable; sample attributes were not retrieved"
                )
                continue
            sample_url = "https://www.ebi.ac.uk/ena/browser/api/xml/" + sample
            try:
                raw = fetch(root, sample_url, cancellation)
                tree = ET.fromstring(raw["text"])
                attributes = [
                    n.findtext("TAG", "").replace(" ", "_") + ": " + n.findtext("VALUE", "")
                    for n in tree.findall(".//SAMPLE_ATTRIBUTE")
                ]
                if attributes:
                    attach(registry, sid, "\n".join(attributes), sample_url, "sample", actor)
                ledger.append({"url": sample_url, "status": "retrieved"})
            except (ValueError, OSError, ET.ParseError) as exc:
                failures[sid] = "Sample attributes unavailable: " + str(exc)[:500]
                ledger.append(
                    {"url": sample_url, "status": "unavailable", "reason": str(exc)[:500]}
                )
    key = studies.link(registry, list(dict.fromkeys(ids)), accession, actor, accession)
    studies.preparation(
        registry,
        key,
        {
            "retrieval": ledger,
            "failures": failures,
            "retrieval_failures": failures,
            "selection": {
                "available_records": available_records,
                "selected_records": len(studies.get(registry, key)["records"]),
                "sample_offset": sample_offset,
                "pilot_limit": sample_limit,
            },
            "status": "NOT_STARTED",
        },
        actor,
    )
    return key


def archive_xml(root: Path, accession: str) -> dict[str, Any]:
    """Read exact experiment/BioSample evidence with a bounded NCBI fallback."""
    if not re.fullmatch(r"(?:[SED]RX|SAM[END][A-Z]?)\d+", accession):
        raise ValueError("Expected an experiment or BioSample accession")
    try:
        return fetch(root, "https://www.ebi.ac.uk/ena/browser/api/xml/" + accession)
    except ValueError as ena_error:
        database = "biosample" if accession.startswith("SAM") else "sra"
        base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
        found = fetch(
            root,
            base
            + "esearch.fcgi?"
            + urlencode(
                {
                    "db": database,
                    "term": accession + "[Accession]",
                    "retmode": "json",
                    "retmax": 2,
                }
            ),
        )
        ids = json.loads(found["text"])["esearchresult"]["idlist"]
        if len(ids) != 1:
            raise ValueError("Archive identity unresolved: " + accession) from ena_error
        source = fetch(
            root,
            base
            + "efetch.fcgi?"
            + urlencode(
                {
                    "db": database,
                    "id": ids[0],
                    "retmode": "xml",
                }
            ),
        )
        tree = ET.fromstring(source["text"])
        tag = "BioSample" if database == "biosample" else "EXPERIMENT"
        if not any(item.get("accession") == accession for item in tree.iter(tag)):
            raise ValueError("Fallback archive evidence has a different accession") from ena_error
        return source
