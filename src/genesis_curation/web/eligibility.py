"""Conservative per-library safeguards; discovery hits do not establish eligibility."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

VERSION = "library-eligibility/3"
TSS = re.compile(
    r"\b(cage(?:-seq)?|rampage|nanopare|tss-seq2?|csrna-seq|stripe-seq|nanocage)\b", re.I
)
CELL = re.compile(r"\b(scATAC|snATAC|single[- ]cell|single[- ]nucleus|multiome)\b", re.I)
CONTROL = re.compile(
    r"\b(input control|genomic DNA control|not an ATAC library|IgG control)\b", re.I
)


def record(state: dict[str, Any], metadata: dict[str, Any]) -> dict[str, str]:
    """Include exact-library experiment evidence without changing imported metadata."""
    original = dict(state.get("original", {}))
    accession = str(original.get("experiment_accession", "")).strip()
    if accession:
        for source in state.get("sources", []):
            if state.get("source_scopes", {}).get(source["version"]) == "study_context":
                continue
            snapshot = source["data"]["snapshot"]
            if not snapshot.lstrip().startswith("<"):
                continue
            try:
                document = ET.fromstring(snapshot)
            except ET.ParseError:
                continue
            experiments = [e for e in document.iter() if e.tag.rsplit("}", 1)[-1] == "EXPERIMENT"]
            if len(experiments) != 1 or experiments[0].get("accession") != accession:
                continue
            # Only the matched experiment's library descriptor is relevant. Study
            # titles, related experiments and paper context cannot establish this.
            for descriptor in experiments[0].iter():
                if descriptor.tag.rsplit("}", 1)[-1] != "LIBRARY_DESCRIPTOR":
                    continue
                for element in descriptor:
                    target = {
                        "LIBRARY_STRATEGY": "library_strategy",
                        "LIBRARY_CONSTRUCTION_PROTOCOL": "protocol",
                    }.get(element.tag.rsplit("}", 1)[-1])
                    if target:
                        value = " ".join(element.itertext()).strip()
                        original[target] = str(original.get(target, "")) + " " + value
    return classify(original, metadata)


def classify(original: dict[str, Any], metadata: dict[str, Any]) -> dict[str, str]:
    """Use library attributes only, never propagate an entire study's assay to every sample."""
    values = {k: str(v or "") for k, v in original.items()}
    assay = " ".join(values.get(k, "") for k in ("library_strategy", "assay", "method"))
    text = " ".join(
        values.get(k, "")
        for k in (
            "sample_title",
            "sample_attributes",
            "library_name",
            "title",
            "note",
            "protocol",
        )
    )
    claimed = str(metadata.get("assay", ""))
    modality = (
        "TSS"
        if TSS.search(assay + " " + claimed)
        else (
            "scATAC"
            if "atac" in (assay + " " + claimed).casefold() and CELL.search(assay + " " + claimed)
            else "Bulk ATAC"
            if "atac" in (assay + " " + claimed).lower()
            else "Other"
        )
    )
    if modality == "Other":
        patterns = {
            "DAP-seq": r"\b(?:amp)?DAP[- ]seq\b",
            "ChIP-seq": r"\bChIP[- ]seq\b",
            "WGBS": r"\b(?:WGBS|bisulfite[- ]seq)\b",
            "MPRA": r"\b(?:MPRA|STARR[- ]seq|massively parallel reporter)\b",
            "RNA-seq": r"\b(?:sc|sn)?RNA[- ]seq\b",
        }
        modality = next(
            (
                name
                for name, pattern in patterns.items()
                if re.search(pattern, assay + " " + claimed, re.I)
            ),
            "Other",
        )
    if CONTROL.search(text):
        return {
            "modality": modality,
            "eligibility": "excluded",
            "reason": "Library marked as control",
        }
    if modality in {"Bulk ATAC", "scATAC"}:
        if re.search(r"\b(amplicon|RNA[- ]seq|WGS|ChIP[- ]seq)\b", assay, re.I):
            return {
                "modality": modality,
                "eligibility": "uncertain",
                "reason": "Archive strategy conflicts with ATAC claim",
            }
        if "atac" not in assay.lower():
            return {
                "modality": modality,
                "eligibility": "uncertain",
                "reason": "Missing library-level archive ATAC evidence",
            }
        if CELL.search(assay + " " + text):
            modality = "scATAC"
            return {
                "modality": modality,
                "eligibility": "uncertain",
                "reason": "Verify per-cell barcode and multiome modality evidence",
            }
        if modality == "scATAC":
            return {
                "modality": modality,
                "eligibility": "uncertain",
                "reason": "Single-cell claim lacks library-level evidence",
            }
        return {
            "modality": modality,
            "eligibility": "candidate",
            "reason": "Archive ATAC library; bulk protocol still requires review",
        }
    if modality == "TSS":
        return {
            "modality": modality,
            "eligibility": "uncertain",
            "reason": "Verify 5-prime protocol and mapping intent",
        }
    if modality in {"DAP-seq", "ChIP-seq", "WGBS", "MPRA", "RNA-seq"}:
        return {
            "modality": modality,
            "eligibility": "candidate"
            if re.search(patterns[modality], assay, re.I)
            else "uncertain",
            "reason": "Library-level assay claim; protocol evidence retained",
        }
    return {
        "modality": modality,
        "eligibility": "unclassified",
        "reason": "No supported target modality established",
    }
