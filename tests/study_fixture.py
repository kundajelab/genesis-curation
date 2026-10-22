"""Synthetic two-library, multi-lane bulk ATAC study used by the study and CLI checks.

Copied from genesis-preprocessing tests/validate_supported_atac.py so these checks do not
import the pipeline's own package.
"""

from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

from genesis_curation.atac.inputs import FIELDS, digest
from genesis_curation.benchmark.fixture import create


def create_study_fixture(folder: Path) -> tuple[Path, Path, Path]:
    """The same real, two-library/multi-lane fixture for standalone and study validation."""
    fixture = folder / "fixture"
    create(fixture, assay="bulk-ATAC")
    ref = {
        "reference_id": "toy",
        "species": "synthetic",
        "assembly": "synthetic-v1",
        "annotation_release": "synthetic-v1",
        "genome_size": 2002000,
        "genome_size_method": "synthetic total bases",
        "fasta": str(fixture / "reference.fa.gz"),
        "fasta_sha256": digest(fixture / "reference.fa.gz"),
        "tss": str(fixture / "tss.bed"),
        "tss_sha256": digest(fixture / "tss.bed"),
        "mitochondrial_contigs": ["mitochondria"],
        "plastid_contigs": ["plastid"],
    }
    registry = folder / "references.json"
    registry.write_text(json.dumps({"schema_version": 1, "references": [ref]}))
    # Divide a library into technical lanes; both use the same basenames in different folders.
    for mate in (1, 2):
        with gzip.open(fixture / f"pe_treatment.R{mate}.fastq.gz", "rt") as source:
            for lane in (1, 2):
                target = fixture / f"lane{lane}"
                target.mkdir(exist_ok=True)
                with gzip.open(target / f"R{mate}.fastq.gz", "wt") as output:
                    for _ in range(16000):
                        for _ in range(4):
                            output.write(source.readline())
    sheet = folder / "samples.tsv"
    rows = []
    for library, rep in [("first", "1"), ("second", "2")]:
        for lane in (1, 2):
            r1, r2 = [fixture / f"lane{lane}/R{mate}.fastq.gz" for mate in (1, 2)]
            rows.append(
                {
                    "library_id": library,
                    "sample_id": "plant",
                    "biological_replicate": rep,
                    "lane_id": str(lane),
                    "reference_id": "toy",
                    "read1": str(r1),
                    "read2": str(r2),
                    "read1_sha256": digest(r1),
                    "read2_sha256": digest(r2),
                    "mapq": 30,
                    "duplicates": "exclude",
                    "adapter_r1": "-",
                    "adapter_r2": "-",
                }
            )
    with sheet.open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=sorted(FIELDS), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    return sheet, registry, fixture
