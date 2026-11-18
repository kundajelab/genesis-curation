"""Read genesis-preprocessing sample sheets and runs-qc tables into run-level records."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

RUN = re.compile(r"(?<![A-Za-z0-9])((?:[SDE]RR|CRR)\d+)(?![0-9])")

# Columns compared between a candidate and a gold sheet, per workflow. "layout" and
# "control" are derived (see Sheet.fields); file names are compared by basename.
FIELDS: dict[str, tuple[str, ...]] = {
    "rna-seq": ("layout", "strandedness", "reference_fasta"),
    "atac-seq": ("layout", "reference_fasta"),
    "dap-seq": ("layout", "control", "reference_fasta"),
    "tss-seq": ("layout", "method", "trim_5p", "adapter_3p", "reference_fasta"),
    "scatac-reads": ("library", "chemistry", "barcode_whitelist", "reference_fasta"),
    "scatac-seq": ("reference_fasta",),
}
BASENAMES = {"reference_fasta", "barcode_whitelist"}


def read_tsv(path: Path) -> list[dict[str, str]]:
    """Rows of a TSV; repeated header lines (from concatenated files) are skipped."""
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        header = reader.fieldnames or []
        return [
            {key: (value or "").strip() for key, value in row.items() if key is not None}
            for row in reader
            if [row.get(name) for name in header] != header
        ]


def runs_in(*values: str) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        for run in RUN.findall(value):
            seen.setdefault(run, None)
    return list(seen)


def basename(value: str) -> str:
    return PurePosixPath(value).name if value not in ("", "-") else value


@dataclass
class Sheet:
    """A sample sheet keyed by run accession; scATAC rows contribute every run."""

    workflow: str
    rows: list[dict[str, str]]
    by_run: dict[str, dict[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.workflow not in FIELDS:
            raise ValueError(
                f"Unknown workflow {self.workflow!r}; expected one of {sorted(FIELDS)}"
            )
        for row in self.rows:
            for run in self.row_runs(row):
                self.by_run[run] = row

    @staticmethod
    def row_runs(row: dict[str, str]) -> list[str]:
        return runs_in(row.get("sra_runs", ""), row.get("read1_url", ""), row.get("read2_url", ""))

    def fields(self, run: str) -> dict[str, str]:
        row = self.by_run[run]
        values: dict[str, str] = {}
        for name in FIELDS[self.workflow]:
            if name == "layout":
                values[name] = "single" if row.get("read2_url", "-") in ("", "-") else "paired"
            elif name == "library":
                values[name] = ",".join(sorted(self.row_runs(row)))
            elif name == "control":
                control = row.get("control_sample", "-")
                match = [r for r in self.rows if r.get("sample_id") == control]
                values[name] = ",".join(self.row_runs(match[0])) if match else control
            elif name in BASENAMES:
                values[name] = basename(row.get(name, ""))
            else:
                values[name] = row.get(name, "")
        return values


def load_sheet(path: Path, workflow: str) -> Sheet:
    return Sheet(workflow, read_tsv(path))


def load_runs_qc(path: Path) -> dict[str, dict[str, str]]:
    """Runs-qc rows keyed by run; library-level rows (scATAC) expand to each run."""
    table: dict[str, dict[str, str]] = {}
    for row in read_tsv(path):
        for run in runs_in(row.get("run", ""), row.get("runs", "")):
            table.setdefault(run, row)
    return table
