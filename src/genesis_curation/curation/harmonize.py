"""Source snapshots, field-level proposals and separately applied metadata revisions."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from functools import lru_cache
from importlib.resources import files
from typing import Any
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener

from ..contracts.records import (
    canonical,
    identity,
    loads,
    record,
)
from ..llm import providers
from ..llm.security import check, schema

MAX_SOURCE = 2 * 1024 * 1024
FIELDS = {
    "organism",
    "taxon",
    "genotype",
    "cultivar",
    "tissue",
    "developmental_stage",
    "treatment",
    "assay",
    "protocol",
    "library_layout",
    "chemistry",
    "biological_replicate",
    "technical_lane",
    "accession",
    "reference_proposal",
    "tf_identity",
    "tf_source_organism",
    "dna_organism",
    "dna_genotype",
    "control_assignment_proposal",
    "dna_context",
    "notes",
    "cell_type",
    "inferred_cell_type",
    "environment",
    "biosample_accession",
    "experiment_accession",
    "study_accession",
}
ALIASES = {
    "organism_ch1": "organism",
    "taxid_ch1": "taxon",
    "scientific_name": "organism",
    "tax_id": "taxon",
    "sample_accession": "biosample_accession",
    "run_accession": "accession",
    "library_strategy": "assay",
    "dev_stage": "developmental_stage",
}
CLASSIFICATIONS = {"public": 0, "internal": 1, "local-only": 2}


@lru_cache(maxsize=8)
def vocabulary(version: str | None = None) -> dict[str, Any]:
    if version not in (None, "genesis-plant-terms/4"):
        raise ValueError("Unsupported vocabulary version")
    return loads(files(__package__).joinpath("vocabularies/plant-v4.json").read_text())


def prompt_vocabulary(text: str, limit: int = 40) -> dict[str, Any]:
    """Only exact evidence matches enter prompts; full normalization remains server-side."""
    vocab = vocabulary()
    words = " " + " ".join(re.findall(r"\w+", text.casefold())) + " "
    selected: dict[str, dict[str, Any]] = {}
    for field, terms in vocab["terms"].items():
        selected[field] = {}
        for alias, term in sorted(terms.items(), key=lambda pair: (-len(pair[0]), pair[0])):
            phrase = " " + " ".join(re.findall(r"\w+", alias)) + " "
            if phrase.strip() and phrase in words:
                selected[field][alias] = term
                if len(selected[field]) >= limit:
                    break
    return {
        "version": vocab["version"],
        "terms": selected,
        "selection": "bounded exact evidence matches",
    }


def source_record(text: str, location: str, media_type: str) -> dict[str, Any]:
    digest = hashlib.sha256(text.encode()).hexdigest()
    return record(
        "source",
        {
            "location": location,
            "worker": "controller",
            "sha256": digest,
            "snapshot": text,
            "media_type": media_type,
        },
        identity("metadata-source", location, digest),
    )


def retrieve(accession: str) -> tuple[str, str, str]:
    """Fixed ENA/NCBI public APIs; no user or model supplied URLs, redirects or supplements."""
    if re.fullmatch(r"[SED]R[RSXAP]\d{3,12}", accession):
        url = (
            "https://www.ebi.ac.uk/ena/portal/api/filereport?accession="
            + quote(accession)
            + "&result=read_run&format=json&fields=run_accession,sample_accession,scientific_name,"
            "tax_id,library_strategy,library_layout"
        )
        media = "application/json"
    elif re.fullmatch(r"G[SM]E?\d{3,12}", accession) or re.fullmatch(r"GSM\d{3,12}", accession):
        url = (
            "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc="
            + quote(accession)
            + "&targ=self&form=text&view=brief"
        )
        media = "text/plain"
    else:
        raise ValueError("Supported public accessions: ENA run/sample/study or GEO GSE/GSM")
    if urlsplit(url).hostname not in {"www.ebi.ac.uk", "www.ncbi.nlm.nih.gov"}:
        raise ValueError("Source host is not allowlisted")
    with build_opener(providers.NoRedirect()).open(
        Request(url, headers={"User-Agent": "Genesis-curation/1"}), timeout=20
    ) as response:
        content = response.read(MAX_SOURCE + 1)
    if len(content) > MAX_SOURCE:
        raise ValueError("Public source exceeds 2 MiB")
    return content.decode("utf-8"), url, media


def locate(source: dict[str, Any], locator: str) -> str:
    text = source["data"]["snapshot"]
    if locator.startswith("span:"):
        match = re.fullmatch(r"span:(\d+):(\d+)", locator)
        if not match or not 0 <= int(match[1]) < int(match[2]) <= len(text):
            raise ValueError("Evidence span outside source")
        return text[int(match[1]) : int(match[2])]
    if locator.startswith("line:"):
        line = int(locator[5:])
        if not 1 <= line <= len(text.splitlines()):
            raise ValueError("Evidence line outside source")
        return text.splitlines()[line - 1]
    if locator.startswith("/"):
        value = json.loads(text)
        for key in locator[1:].split("/"):
            key = key.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
        return value if isinstance(value, str) else canonical(value)
    raise ValueError("Field evidence needs an exact JSON pointer or line:N")


def extract(bundle: dict[str, Any], metadata: dict[str, Any]) -> list[dict[str, Any]]:
    found: dict[str, list[dict[str, Any]]] = {}
    vocab = vocabulary()
    for source in bundle["data"]["sources"]:
        data = source["data"]
        items: list[tuple[str, Any, str, str]] = []
        if data["media_type"] == "application/json":
            raw = json.loads(data["snapshot"])
            rows = raw if isinstance(raw, list) else [raw]
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                for name, value in row.items():
                    locator = (
                        (f"/{index}" if isinstance(raw, list) else "")
                        + "/"
                        + name.replace("~", "~0").replace("/", "~1")
                    )
                    items.append((name, value, locator, locate(source, locator)))
        elif data["media_type"] in {"text/csv", "text/tab-separated-values"}:
            reader = csv.DictReader(
                io.StringIO(data["snapshot"]),
                delimiter="\t" if data["media_type"].endswith("values") else ",",
            )
            for row in reader:
                # csv.line_num is the ending physical line; multiline values are left for manual
                # review.
                line = reader.line_num
                for name, value in row.items():
                    if name and isinstance(value, str) and "\n" not in value:
                        items.append(
                            (name, value, f"line:{line}", data["snapshot"].splitlines()[line - 1])
                        )
        else:
            for index, line in enumerate(data["snapshot"].splitlines(), 1):
                match = re.match(r"(?:!Sample_)?([A-Za-z_][A-Za-z_0-9]*)\s*[:=]\s*(.+)", line)
                if match:
                    name, value = match[1], match[2]
                    if name == "characteristics_ch1" and ":" in value:
                        name, value = value.split(":", 1)
                        name, value = name.strip().replace(" ", "_"), value.strip()
                    if name == "relation":
                        relation = re.fullmatch(
                            r"(BioSample|SRA):\s+https://(?:www\.)?ncbi\.nlm\.nih\.gov/"
                            r"(?:biosample/|sra\?term=)(SAM[NED]\d+|[SED]RX\d+)",
                            value,
                        )
                        if relation:
                            accession = relation[2]
                            name = (
                                "biosample_accession"
                                if accession.startswith("SAM")
                                else "experiment_accession"
                            )
                            value = accession
                    items.append((name, value, f"line:{index}", line))
        for raw_name, value, locator, quoted in items:
            name = ALIASES.get(raw_name.lower(), raw_name.lower())
            if name not in FIELDS or value in (None, "") or type(value) not in (str, int):
                continue
            if sum(len(candidates) for candidates in found.values()) >= 4096:
                raise ValueError("Too many field observations; select sources for one library")
            term = vocab["terms"].get(name, {}).get(str(value).casefold())
            found.setdefault(name, []).append(
                {
                    "field": name,
                    "original": metadata.get(name),
                    "value": term["label"] if term else value,
                    "status": "known",
                    "vocabulary_id": term["id"] if term else None,
                    "vocabulary_version": vocab["version"],
                    "evidence": [
                        {"source_version": source["version"], "locator": locator, "quote": quoted}
                    ],
                    "rationale": "Explicit source field; biological meaning still requires review",
                    "confidence": None,
                }
            )
    result = []
    for name, candidates in found.items():
        candidate = candidates[0]
        candidate["evidence"] = [e for c in candidates for e in c["evidence"]]
        values = {canonical(c["value"]) for c in candidates}
        if len(values) > 1:
            candidate.update(
                value=None,
                status="conflicting",
                vocabulary_id=None,
                rationale="Source fields conflict; curator resolution required",
            )
        # Library strategy/title alone never assigns bulk versus single-cell or genuine 5-prime
        # protocols.
        if name in {"assay", "protocol"} and str(candidate["value"]).casefold() in {
            "atac-seq",
            "other",
            "unspecified",
            "gro-seq",
            "gro-cap",
            "5-prime",
            "5'",
        }:
            candidate.update(
                value=None,
                status="unknown",
                vocabulary_id=None,
                rationale="Protocol needs explicit bulk/single-cell and library evidence",
            )
        result.append(candidate)
    for name in sorted(FIELDS - set(found)):
        result.append(
            {
                "field": name,
                "original": metadata.get(name),
                "value": None,
                "status": "unknown",
                "vocabulary_id": None,
                "vocabulary_version": vocab["version"],
                "evidence": [],
                "rationale": "No explicit source evidence",
                "confidence": None,
            }
        )
    return result


def verify_fields(bundle: dict[str, Any], fields: list[dict[str, Any]]) -> None:
    check({"fields": fields}, schema("metadata-response-v1"))
    if len({f["field"] for f in fields}) != len(fields) or any(
        f["field"] not in FIELDS for f in fields
    ):
        raise ValueError("Duplicate or unsupported metadata field")
    sources = {s["version"]: s for s in bundle["data"]["sources"]}
    vocab = vocabulary()
    for field in fields:
        if field["status"] == "known" and field["value"] in (None, ""):
            raise ValueError("A known field needs an explicit nonempty value")
        if field["status"] == "not_applicable" and field["value"] is not None:
            raise ValueError("Not-applicable fields must use null and cite explicit evidence")
        if field["status"] in {"unknown", "conflicting"} and field["value"] is not None:
            raise ValueError("Unknown/conflicting fields must abstain with a null value")
        if field["status"] in {"known", "not_applicable"} and not field["evidence"]:
            raise ValueError("A proposed value requires exact source evidence")
        for evidence in field["evidence"]:
            if evidence["source_version"] not in sources or evidence["quote"] not in locate(
                sources[evidence["source_version"]], evidence["locator"]
            ):
                raise ValueError("Fabricated or stale field evidence")
        vocab = vocabulary(field["vocabulary_version"])
        if field["vocabulary_id"] and not any(
            t["id"] == field["vocabulary_id"] and t["label"] == field["value"]
            for t in vocab["terms"].get(field["field"], {}).values()
        ):
            raise ValueError("Unknown ontology ID or mismatched term")
