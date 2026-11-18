"""Write genesis-preprocessing sample sheets and runs-qc tables.

Sheets are checked against the workflow's sample schema, vendored from genesis-preprocessing
(`schemas/`, refreshed by `scripts/sync_sheet_schemas.py`). The header is the schema's
`required` list in order, which is the exact header the workflows check.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema

SCHEMAS = Path(__file__).resolve().parent / "schemas"
# Runs-qc columns every workflow writes first; measured and classified values follow.
QC_LEADING = ("run", "study", "sample_id", "in_sheet", "reason_left_out")


@cache
def schema(workflow: str) -> dict[str, Any]:
    path = SCHEMAS / f"{workflow}.samples.schema.json"
    if not path.exists():
        raise ValueError(f"No vendored sample schema for {workflow!r}")
    return json.loads(path.read_text())


def header(workflow: str) -> list[str]:
    return list(schema(workflow)["items"]["required"])


def validate(workflow: str, rows: Sequence[dict[str, str]]) -> None:
    """Raise ValueError naming the first rows that break the schema or repeat an id."""
    value = schema(workflow)
    problems = [
        f"row {'/'.join(str(p) for p in error.absolute_path)}: {error.message}"
        for error in jsonschema.Draft202012Validator(value).iter_errors(list(rows))
    ]
    for key in value.get("uniqueEntries", []):
        seen: set[str] = set()
        for row in rows:
            if row[key] in seen:
                problems.append(f"{key} {row[key]!r} repeats")
            seen.add(row[key])
    if problems:
        raise ValueError(f"{workflow} sheet breaks its schema: " + "; ".join(problems[:5]))


def write_tsv(path: Path, columns: Sequence[str], rows: Sequence[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    with part.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle, list(columns), delimiter="\t", lineterminator="\n", extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(rows)
    part.replace(path)


def write_sheet(path: Path, workflow: str, rows: Sequence[dict[str, str]]) -> None:
    validate(workflow, rows)
    write_tsv(path, header(workflow), sorted(rows, key=lambda row: row["sample_id"]))


def write_runs_qc(path: Path, rows: Sequence[dict[str, str]]) -> None:
    """One row per candidate run. Every left-out run has exactly one reason; kept runs none."""
    for row in rows:
        kept = row["in_sheet"] == "yes"
        if kept == bool(row["reason_left_out"]):
            raise ValueError(
                f"{row['run']}: in_sheet={row['in_sheet']} with reason {row['reason_left_out']!r}"
            )
    extra = [k for k in (rows[0] if rows else {}) if k not in QC_LEADING]
    write_tsv(path, [*QC_LEADING, *extra], sorted(rows, key=lambda row: row["run"]))
