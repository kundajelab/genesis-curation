"""Conservative per-library safeguards; discovery hits do not establish eligibility."""

from __future__ import annotations

import re
from typing import Any

VERSION = "library-eligibility/1"
TSS = re.compile(
    r"\b(cage(?:-seq)?|rampage|nanopare|tss-seq2?|csrna-seq|stripe-seq|nanocage)\b", re.I
)
CELL = re.compile(r"\b(scATAC|snATAC|single[- ]cell|single[- ]nucleus|multiome)\b", re.I)
CONTROL = re.compile(
    r"\b(input control|genomic DNA control|not an ATAC library|IgG control)\b", re.I
)


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
            if CELL.search(assay + " " + claimed)
            else "Bulk ATAC"
            if "atac" in (assay + " " + claimed).lower()
            else "Other"
        )
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
    return {
        "modality": modality,
        "eligibility": "unclassified",
        "reason": "No supported target modality established",
    }
