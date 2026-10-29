"""Read the shared cell metadata contract without running preprocessing."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ..contracts.records import load, loads
from ..scatac.common import text_file


def cells(path: Path) -> Iterator[dict[str, Any]]:
    validator = Draft202012Validator(load(Path(__file__).parents[1] / "scatac/assets/cell-v1.json"))
    with text_file(path) as stream:
        for number, line in enumerate(iter(lambda: stream.readline(1024 * 1024 + 1), ""), 1):
            if len(line) > 1024 * 1024:
                raise ValueError(f"Cell record too large at line {number}")
            cell = loads(line)
            errors = list(validator.iter_errors(cell))
            if errors:
                raise ValueError(f"Invalid cell contract at line {number}: {errors[0].message}")
            for key in (
                "library_id",
                "barcode",
                "author_original_label",
                "harmonized_label",
                "annotation_source",
                "annotation_status",
            ):
                if not isinstance(cell.get(key), str):
                    raise ValueError(f"Cell field {key} requires a string")
            if (
                type(cell.get("atac_include")) is not bool
                or type(cell.get("atac_present")) is not bool
            ):
                raise ValueError("Explicit Boolean ATAC inclusion/presence is required")
            if cell["atac_include"]:
                if not cell["atac_present"] or any(
                    not cell[k].strip() or cell[k] in {"UNKNOWN", "UNSPECIFIED"}
                    for k in ("harmonized_label", "annotation_source")
                ):
                    raise ValueError("Included cells require present ATAC and source-backed labels")
            yield cell
