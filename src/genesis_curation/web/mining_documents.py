"""Bounded metadata attachments; never fetch read files or execute workbook formulas."""

from __future__ import annotations

import csv
import hashlib
import io
import re
import threading
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from urllib.request import Request, build_opener

from ..contracts.records import canonical, fingerprint, now
from ..llm import providers
from ..scatac import workbook
from . import intake, mining_sources

LIMIT = 8 * 1024 * 1024
EXTENSIONS = {".csv", ".tsv", ".xlsx", ".txt"}


def location(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme == "ftp" and parsed.hostname == "ftp.ncbi.nlm.nih.gov":
        url = "https://" + url[6:]
        parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname
        not in {
            "ftp.ncbi.nlm.nih.gov",
            "www.ncbi.nlm.nih.gov",
            "pmc.ncbi.nlm.nih.gov",
            "www.ebi.ac.uk",
        }
        or parsed.port
        or parsed.username
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Metadata attachment is outside supported archive locations")
    name = Path(unquote(parsed.path)).name
    if Path(name).suffix.lower() not in EXTENSIONS or not re.search(
        r"metadata|sample|table|suppl|sra|run", name, re.I
    ):
        raise ValueError("Only named metadata/sample tables are retrieved automatically")
    if re.search(r"fastq|\.bam|\.cram|matrix|counts|expression|fragments", name, re.I):
        raise ValueError("Scientific data files are inventoried, not downloaded")
    return url


def fetch(root: Path, url: str, cancel: threading.Event | None = None) -> tuple[Path, bytes]:
    url = location(url)
    folder = root / "metadata-attachments"
    folder.mkdir(exist_ok=True)
    path = folder / (fingerprint(url) + Path(urlsplit(url).path).suffix.lower())
    if path.exists():
        return path, path.read_bytes()
    content = bytearray()
    with build_opener(providers.NoRedirect()).open(
        Request(url, headers={"User-Agent": "Genesis-curation/1"}), timeout=25
    ) as response:
        while chunk := response.read(65536):
            if cancel and cancel.is_set():
                raise ValueError("Attachment retrieval cancelled")
            content.extend(chunk)
            if len(content) > LIMIT:
                raise ValueError("Metadata attachment exceeds 8 MiB")
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_bytes(content)
    temporary.replace(path)
    return path, bytes(content)


def rows(path: Path, content: bytes) -> list[tuple[str, str]]:
    if path.suffix == ".xlsx":
        result = []
        for name, sheet in workbook.read(path).items():
            if not sheet:
                continue
            header = canonical(sheet[0]["cells"])
            for row in sheet[1:]:
                result.append((f"{name}:row:{row['row']}", header + "\n" + canonical(row["cells"])))
        return result
    text = content.decode("utf-8-sig")
    delimiter = "," if path.suffix == ".csv" else "\t"
    values = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if not values:
        return []
    header = canonical(values[0])
    return [(f"row:{i}", header + "\n" + canonical(row)) for i, row in enumerate(values[1:], 2)]


def gather(
    root: Path,
    candidate: dict[str, Any],
    states: list[dict[str, Any]],
    actor: str,
    cancel: threading.Event | None,
) -> list[dict[str, Any]]:
    """Attach only rows with explicit subject accession matches; leave ambiguous rows unbound."""
    ledger = []
    urls = []
    if candidate["accession"].startswith("GSE"):
        source = intake.fetch(root, intake.geo_url(candidate["accession"]), cancel)
        urls.extend(re.findall(r"!Series_supplementary_file[^=]*=\s*(\S+)", source["text"]))
    for state in states:
        for source in state["sources"]:
            urls.extend(
                re.findall(r"!Sample_supplementary_file[^=]*=\s*(\S+)", source["data"]["snapshot"])
            )
    allowed = []
    for url in dict.fromkeys(urls):
        candidate["files"].append(
            {
                "location": url,
                "format": Path(urlsplit(url).path).suffix,
                "availability": "archive-reported",
                "download_verified": False,
                "kind": "supplementary",
            }
        )
        try:
            allowed.append(location(url))
        except ValueError as error:
            ledger.append({"url": url, "status": "not-fetched", "reason": str(error)})
    for url in allowed[:3]:
        try:
            path, content = fetch(root, url, cancel)
            extracted = rows(path, content)
            matched = 0
            for state in states:
                own = set(mining_sources.ACCESSIONS.findall(canonical(state["original"])))
                own.update(
                    a
                    for source in state["sources"]
                    if state.get("source_scopes", {}).get(source["version"]) != "study_context"
                    for a in mining_sources.evidence_links(source["data"]["snapshot"])
                )
                own = {a for a in own if not a.startswith(("GSE", "PRJ", "SRP", "ERP", "DRP"))}
                selected = []
                for locator, text in extracted:
                    # Header/column accessions and study IDs cannot attribute a data row.
                    row_ids = {
                        a
                        for a in mining_sources.ACCESSIONS.findall(text.split("\n", 1)[-1])
                        if not a.startswith(("GSE", "PRJ", "SRP", "ERP", "DRP"))
                    }
                    if row_ids and row_ids.issubset(own):
                        selected.append((locator, text))
                if selected:
                    matched += len(selected)
                    text = "\n".join(locator + ": " + value for locator, value in selected)
                    if len(text.encode()) > 2 * 1024 * 1024:
                        raise ValueError(
                            "Matched metadata exceeds sample evidence attachment limit"
                        )
                    intake.attach(root / "catalog", state["id"], text, url, "sample", actor)
            ledger.append(
                {
                    "url": url,
                    "status": "retrieved",
                    "retrieved_at": now(),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "rows": len(extracted),
                    "matched_rows": matched,
                    "binding": "explicit accession only",
                }
            )
        except (
            ValueError,
            OSError,
            KeyError,
            IndexError,
            zipfile.BadZipFile,
            ET.ParseError,
        ) as error:
            ledger.append({"url": url, "status": "unavailable", "reason": str(error)[:500]})
    for url in allowed[3:]:
        ledger.append(
            {"url": url, "status": "not-fetched", "reason": "Three-attachment study budget reached"}
        )
    return ledger
