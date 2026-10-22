"""Model fold isolation and evidence validation do not infer biological readiness."""

from __future__ import annotations

import copy
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from genesis_curation.contracts.records import dump
from genesis_curation.scatac import evidence, model
from genesis_curation.scatac.common import complete


def rejects(function: Callable[..., Any], *args: object) -> None:
    try:
        function(*args)
    except ValueError, KeyError:
        return
    raise AssertionError("Invalid input was accepted")


def main() -> None:
    sizes = {"chr1": 10000, "chr2": 10000, "chr3": 10000}
    folds = {"train": ["chr1"], "validation": ["chr2"], "test": ["chr3"]}
    assert model.folds(folds, sizes)["chr3"] == "test"
    rejects(model.folds, {**folds, "test": ["chr1"]}, sizes)
    rejects(model.folds, {**folds, "test": ["unknown"]}, sizes)
    rejects(model.folds, {**folds, "validation": []}, sizes)
    evidence_doc: dict[str, Any] = {
        "schema_version": 1,
        "studies": {
            "PRJNA1": {
                "fragments": {
                    "status": "AVAILABLE",
                    "review_status": "UNREVIEWED",
                    "detail": "Fixture",
                    "sources": [
                        {
                            "url": "https://example.org/evidence",
                            "sha256": "a" * 64,
                            "retrieved_date": "2026-10-10",
                        }
                    ],
                }
            }
        },
    }
    assert evidence.validate(evidence_doc) == evidence_doc
    changed = copy.deepcopy(evidence_doc)
    changed["studies"]["PRJNA1"]["fragments"]["review_status"] = "APPROVED"
    rejects(evidence.validate, changed)
    changed = copy.deepcopy(evidence_doc)
    changed["studies"]["PRJNA1"]["fragments"]["sources"][0]["url"] = (
        "https://user:pass@example.org/"
    )
    rejects(evidence.validate, changed)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dump(root / "evidence.json", evidence_doc)
        rows = [{"study_key": "PRJNA1", "metadata_completeness": 1.0}]
        evidence.apply(root, rows)
        assert rows[0]["fragments"] == "AVAILABLE"
        assert rows[0]["status"] == "NEEDS_EVIDENCE"
        # Content corruption must fail regardless of the declared model-ready status.
        (root / "chrom.sizes").write_text("chr1\t10000\n")
        complete(root, "scatac-model", {"config": {"folds": folds}}, 0)
        (root / "chrom.sizes").write_text("chr1\t10\n")
        rejects(model.validate, root)
    print("PASS: disjoint plant chromosome folds, evidence review gates, tamper detection")


if __name__ == "__main__":
    main()
