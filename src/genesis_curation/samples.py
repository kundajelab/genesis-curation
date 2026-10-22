"""Validate one DAP-seq sample sheet before any downloads are started."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from urllib.parse import urlparse

SAMPLE_HEADER = (
    "sample_id",
    "species",
    "read1_url",
    "read2_url",
    "control_sample",
    "reference_fasta",
)
IDENTIFIER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
URL = re.compile(r"[A-Za-z0-9_./:%?=&+~@#,-]+")


def load_samples(sheet: Path, references: Path) -> list[dict[str, str]]:
    """Validate schema, sample identities, reference files, and control assignments."""
    with sheet.open(newline="") as stream:
        reader = csv.reader(stream, delimiter="\t")
        if tuple(next(reader, [])) != SAMPLE_HEADER:
            raise ValueError(f"{sheet}: expected header {' '.join(SAMPLE_HEADER)}")
        rows: list[dict[str, str]] = []
        for number, fields in enumerate(reader, 2):
            if len(fields) != len(SAMPLE_HEADER) or any(not field for field in fields):
                raise ValueError(f"{sheet}:{number}: expected six nonempty fields")
            row = dict(zip(SAMPLE_HEADER, fields, strict=True))
            for key in ("sample_id", "species", "reference_fasta"):
                if not IDENTIFIER.fullmatch(row[key]):
                    raise ValueError(f"{sheet}:{number}: unsafe {key}: {row[key]}")
            if not row["reference_fasta"].endswith((".fa.gz", ".fasta.gz", ".fna.gz")):
                raise ValueError(f"{sheet}:{number}: reference must be a gzipped FASTA basename")
            if not (references / row["reference_fasta"]).is_file():
                raise ValueError(f"{sheet}:{number}: reference missing: {row['reference_fasta']}")
            for key in ("read1_url", "read2_url"):
                value = row[key]
                if key == "read2_url" and value == "-":
                    continue
                parsed = urlparse(value)
                if (
                    not URL.fullmatch(value)
                    or parsed.scheme not in ("http", "https")
                    or not parsed.netloc
                ):
                    raise ValueError(f"{sheet}:{number}: invalid {key}")
            rows.append(row)
    if not rows:
        raise ValueError(f"{sheet}: no samples")
    references_by_id: dict[str, str] = {}
    for row in rows:
        filename = row["reference_fasta"]
        reference_id = re.sub(r"\.(fa|fasta|fna)\.gz$", "", filename)
        previous = references_by_id.setdefault(reference_id, filename)
        if previous != filename:
            raise ValueError(
                f"{sheet}: reference ID collision: {previous} and {filename} both use "
                f"{reference_id}; use distinct reference basenames"
            )
    samples = {row["sample_id"]: row for row in rows}
    if len(samples) != len(rows):
        raise ValueError(f"{sheet}: duplicate sample_id")
    for row in rows:
        control_id = row["control_sample"]
        if control_id == "-":
            continue
        control = samples.get(control_id)
        if (
            control is None
            or control_id == row["sample_id"]
            or control["control_sample"] != "-"
            or control["species"] != row["species"]
            or control["reference_fasta"] != row["reference_fasta"]
            or (control["read2_url"] == "-") != (row["read2_url"] == "-")
        ):
            raise ValueError(f"{sheet}: invalid control {control_id} for {row['sample_id']}")
    return rows
