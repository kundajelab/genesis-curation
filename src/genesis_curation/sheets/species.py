"""Per-species settings for sheet building, from the package's `data/species.yaml`."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml

DATA = Path(__file__).resolve().parent / "data"


@dataclass(frozen=True)
class References:
    fasta: str
    annotation: str
    genes: str
    organelle_contigs: str

    @property
    def star_index(self) -> str:
        """The STAR index folder name genesis-preprocessing builds for this fasta/annotation."""
        fasta = re.sub(r"\.(fa|fasta|fna)\.gz$", "", self.fasta)
        return f"{fasta}.{re.sub(r'\.gz$', '', self.annotation)}.star"


@dataclass(frozen=True)
class Species:
    folder: str
    scientific_name: str
    taxon: int
    reference_genotype: str
    reference: re.Pattern[str]
    title_marker: re.Pattern[str]
    gene_names_mean_mutant: bool
    mutant_terms: tuple[re.Pattern[str], ...]
    references: References
    reference_lines: re.Pattern[str] | None = None


def parse(folder: str, entry: dict[str, Any]) -> Species:
    reference = re.compile(entry["reference_pattern"], re.I)
    return Species(
        folder=folder,
        scientific_name=entry["scientific_name"],
        taxon=int(entry["taxon"]),
        reference_genotype=entry["reference_genotype"],
        reference=reference,
        title_marker=re.compile(entry["title_marker"], re.I)
        if entry.get("title_marker")
        else reference,
        gene_names_mean_mutant=bool(entry.get("gene_names_mean_mutant", False)),
        mutant_terms=tuple(re.compile(term, re.I) for term in entry.get("mutant_terms") or []),
        references=References(**entry["references"]),
        reference_lines=re.compile(entry["reference_lines"], re.I)
        if entry.get("reference_lines")
        else None,
    )


@cache
def catalog(path: Path = DATA / "species.yaml") -> dict[str, Species]:
    entries = yaml.safe_load(path.read_text())
    return {folder: parse(folder, entry) for folder, entry in entries.items()}


def load(folder: str) -> Species:
    species = catalog()
    if folder not in species:
        raise ValueError(f"No species entry for {folder!r}; add one to {DATA / 'species.yaml'}")
    return species[folder]
