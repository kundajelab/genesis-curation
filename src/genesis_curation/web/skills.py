"""Curator-selected versioned guidance. No skill-supplied code is executed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..contracts.records import fingerprint, load

BUILTIN = {
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
    result = [BUILTIN]
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
