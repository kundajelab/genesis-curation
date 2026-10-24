"""Curator-selected versioned guidance. No skill-supplied code is executed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..contracts.records import fingerprint, load

BUILTIN: dict[str, Any] = {
    "id": "plant-metadata",
    "version": "1",
    "title": "Plant metadata evidence",
    "instructions": "Distinguish organism from TF source organism and assayed DNA organism. "
    "Keep accession, biological replicate and technical lane separate. Do not infer bulk versus "
    "single-cell assay from an ATAC-seq title. Do not infer genotype, stage or treatment from "
    "a related sample. Report conflicts and missing evidence explicitly. Cite only exact supplied "
    "source passages; reference guidance and prior corrections are not dataset evidence.",
    "references": [{"title": "Genesis metadata vocabulary", "id": "plant-v1"}],
}


def available(folder: Path) -> list[dict[str, Any]]:
    result = [
        BUILTIN,
        {
            **BUILTIN,
            "id": "plant-binding-metadata",
            "title": "Plant TF-binding metadata",
            "instructions": BUILTIN["instructions"]
            + " Distinguish TF tags, TF donors and assayed DNA species. "
            "and input/IgG/empty-vector controls from untreated biological conditions. "
            "Control assignment needs explicit sample relationships; no majority-label filling.",
        },
        {
            **BUILTIN,
            "id": "plant-celltype-metadata",
            "title": "Plant single-cell metadata",
            "instructions": BUILTIN["instructions"]
            + " Preserve author-reported cell_type separately from inferred_cell_type. "
            "A new inferred label requires supplied RNA marker/expression evidence, anatomical "
            "plausibility and uncertainty. Distinguish cell identity from stress or cell state; "
            "homology is a hypothesis, not marker validation. Missing expression evidence means "
            "abstain. Never treat ATAC gene activity as measured RNA expression.",
            "references": [
                {
                    "title": "Alex Borowsky: plant-celltype-annotation v0.2.1",
                    "id": "https://github.com/alexborowsky/plant-celltype-annotation/tree/"
                    "7c99b333b60e235b57e2a45efba42c032544037b",
                }
            ],
        },
    ]
    if folder.exists():
        for path in sorted(folder.glob("*.json")):
            value = load(path)
            if set(value) != {"id", "version", "title", "instructions", "references"}:
                raise ValueError(
                    "Skill requires id, version, title, instructions and references only"
                )
            if any(
                not isinstance(value[k], str) or not value[k].strip()
                for k in ("id", "version", "title", "instructions")
            ):
                raise ValueError("Invalid skill identity or instructions")
            if not isinstance(value["references"], list) or len(value["instructions"]) > 16000:
                raise ValueError("Invalid skill references or instruction budget")
            result.append(value)
    if len({r["id"] for r in result}) != len(result):
        raise ValueError("Duplicate skill ID")
    return [{**r, "sha256": fingerprint(r)} for r in result]


def select(folder: Path, skill_id: str) -> dict[str, Any]:
    if skill_id == "none":
        return {
            "id": "none",
            "version": "1",
            "instructions": "",
            "references": [],
            "sha256": fingerprint(None),
        }
    for skill in available(folder):
        if skill["id"] == skill_id:
            return skill
    raise ValueError("Unknown skill")
