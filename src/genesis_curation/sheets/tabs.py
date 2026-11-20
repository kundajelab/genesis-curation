"""Candidate runs listed in a metadata-sheet tab export (`--extra-runs`).

A tab export is a TSV with a `species` column and the runs of each row in `run_accessions`
or `sra_runs` (separated by commas, semicolons or spaces); rows with no runs give their
`experiment_accession` instead (ENA and SRA searches accept either). Builders add the listed
accessions their own search missed, so every run the sheet lists gets a decision and a reason.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from .species import Species

RUN_COLUMNS = ("run_accessions", "sra_runs")
GSA = re.compile(r"^CR[RX]\d+$")


def split(value: str) -> list[str]:
    return [part for part in re.split(r"[;, ]+", value or "") if part]


def names_species(species: Species, name: str) -> bool:
    genus = species.scientific_name.split()[0]
    name = name.strip()
    return name.startswith(species.scientific_name) or name == genus


def tab_rows(path: Path, species: Species) -> dict[str, dict[str, str]]:
    """accession -> tab row, for the species' rows (runs, else the experiment)."""
    found: dict[str, dict[str, str]] = {}
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if not names_species(species, row.get("species") or ""):
                continue
            runs = next((split(row[c]) for c in RUN_COLUMNS if split(row.get(c) or "")), [])
            for accession in runs or split(row.get("experiment_accession") or ""):
                found.setdefault(accession, row)
    return found


def is_gsa(accession: str) -> bool:
    return bool(GSA.match(accession))
