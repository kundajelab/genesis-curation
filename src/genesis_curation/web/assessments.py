"""Versioned evidence assessments; confidence cannot waive a concrete issue."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from ..contracts.records import fingerprint

VERSION = "plant-evidence/3"


def sample_attributes(state: dict[str, Any]) -> dict[str, set[str]]:
    """Read explicit attributes from single-sample XML, never study-context sources."""
    result: dict[str, set[str]] = {}
    for source in state.get("sources", []):
        if state.get("source_scopes", {}).get(source["version"]) == "study_context":
            continue
        text = source["data"]["snapshot"]
        if not text.lstrip().startswith("<"):
            continue
        try:
            document = ET.fromstring(text)
        except ET.ParseError:
            continue
        samples = [
            e for e in document.iter() if e.tag.rsplit("}", 1)[-1] in {"SAMPLE", "BioSample"}
        ]
        if len(samples) != 1:
            continue
        for element in samples[0].iter():
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "SAMPLE_ATTRIBUTE":
                children = {e.tag.rsplit("}", 1)[-1]: e.text or "" for e in element}
                name, value = children.get("TAG", ""), children.get("VALUE", "")
            elif tag == "Attribute":
                name = element.get("harmonized_name") or element.get("attribute_name", "")
                value = element.text or ""
            else:
                continue
            if name.strip() and value.strip():
                result.setdefault(name.strip().casefold(), set()).add(value.strip())
    return result


def assess(state: dict[str, Any], fields: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    explicit = sample_attributes(state)
    for field in fields:
        name, value = field["field"], str(field["value"] or "")
        citations = field["evidence"]
        text = " ".join(c["quote"] for c in citations).casefold()
        issues = []
        if field["status"] == "known":
            if name == "chemistry" and re.search(
                r"space ranger|cell ranger|\bstar\b|hisat|bowtie|\bseurat\b", value, re.I
            ):
                issues.append("Processing software is not library chemistry")
            if name in {
                "dna_organism",
                "tf_source_organism",
                "organism",
                "dna_context",
            } and re.fullmatch(
                r"(?:GRCh\d+|hg\d+|TAIR\d+|IRGSP[-\d.]+|GCA_[\d.]+|GCF_[\d.]+)", value, re.I
            ):
                issues.append("A reference assembly does not identify the biological source")
            if name == "biological_replicate" and re.search(r"lane|technical replicate", text):
                if not re.search(r"biological replicate|biological rep", text):
                    issues.append(
                        "Technical sequencing information does not establish a biological replicate"
                    )
            if name in {"dna_organism", "tf_source_organism"}:
                expected = (
                    r"dna|genomic|substrate"
                    if name == "dna_organism"
                    else r"transcription factor|\btf\b|tf_source|protein|donor"
                )
                if not re.search(expected, text + " " + " ".join(c["locator"] for c in citations)):
                    issues.append("Evidence does not distinguish TF donor from assayed DNA species")
            if name == "library_layout" and value.casefold() not in {
                "paired",
                "single",
                "paired-end",
                "single-end",
                "paired end",
                "single end",
            }:
                issues.append(
                    "Library layout must distinguish paired-end from single-end sequencing"
                )
        if field["status"] == "known" and name in {"tissue", "genotype"}:
            original = state.get("original", {})
            attributes = str(original.get("sample_attributes", ""))
            aliases = "tissue|organ" if name == "tissue" else "genotype|cultivar|ecotype"
            matches = re.findall(
                rf"(?:^|[;|\n])\s*(?:{aliases})\s*[:=]\s*([^;|\n]+)", attributes, re.I
            )
            matches.extend(v for alias in aliases.split("|") for v in explicit.get(alias, set()))
            if matches and all(value.strip().casefold() != v.strip().casefold() for v in matches):
                issues.append("Proposed value differs from the sample's explicit archive attribute")
            if name == "tissue" and re.search(
                r"(?:control|treated|replicate)[ _-]*\d|^IRBB\d", value, re.I
            ):
                issues.append("A sample or treatment label may have been used as tissue")
            if name == "genotype" and re.search(
                r"\b(young panicle|leaf|root|seedling)\b", value, re.I
            ):
                issues.append("An organ or developmental label may have been used as genotype")
        if field["status"] == "known" and name == "assay":
            from . import eligibility

            check = eligibility.classify(state.get("original", {}), {"assay": value})
            if check["eligibility"] in {"excluded", "uncertain"}:
                issues.append(check["reason"])
        supported = field["status"] == "known" and bool(citations)
        verbatim = supported and value.casefold() in text
        result[name] = {
            "version": VERSION,
            "input_hash": fingerprint([field, state.get("source_scopes", {})]),
            "support": "quoted_value"
            if verbatim
            else "model_interpretation"
            if supported
            else "unresolved",
            "issues": issues,
            "needs_verification": supported and (bool(issues) or not verbatim),
            "verification": None,
        }
    return result


def cross_sample(rows: list[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    """Flag contradictory identity claims, never fill from majority labels."""
    found: dict[str, dict[str, list[str]]] = {}
    for identity, field in [
        ("biosample_accession", "organism"),
        ("experiment_accession", "biological_replicate"),
    ]:
        groups: dict[str, list[tuple[str, str]]] = {}
        for row in rows:
            values = {f["field"]: str(f["value"]) for f in row["fields"] if f["status"] == "known"}
            if values.get(identity) and values.get(field):
                groups.setdefault(values[identity], []).append((row["id"], values[field]))
        for key, members in groups.items():
            if len({value.casefold() for _, value in members}) > 1:
                for sid, _ in members:
                    found.setdefault(sid, {}).setdefault(field, []).append(
                        f"Conflicting {field.replace('_', ' ')} values share {identity} {key}"
                    )
    return found
