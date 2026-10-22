"""Reject reference comparisons confounded by changed inputs or scientific policies."""

from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

from bind_chains import bind
from compare_references import matched_inputs
from genesis_curation.benchmark.references import inspect_fasta
from genesis_curation.benchmark.spec import sha256


def main() -> None:
    plan = {
        "spec": {
            "parameters": {"mapq": [30]},
            "seed": 1,
            "methods": [{"id": "baseline", "aligner": "bwa-mem2"}],
        },
        "dataset": {
            "assay": "DAP-seq",
            "libraries": [
                {
                    "id": "tf",
                    "layout": "SE",
                    "raw_templates": 100,
                    "control": "control",
                    "read1": "tf",
                },
                {
                    "id": "control",
                    "layout": "SE",
                    "raw_templates": 100,
                    "control": None,
                    "read1": "control",
                },
            ],
        },
        "assets": {"tf": {"sha256": "a" * 64}, "control": {"sha256": "b" * 64}},
    }
    assert matched_inputs([plan, copy.deepcopy(plan)])["read_sha256"]["tf"][0] == "a" * 64
    for change in (
        "missing_library",
        "control",
        "read",
        "count",
        "policy",
        "seed",
        "method",
    ):
        other = copy.deepcopy(plan)
        if change == "missing_library":
            other["dataset"]["libraries"].pop()
        elif change == "control":
            other["dataset"]["libraries"][0]["control"] = None
        elif change == "read":
            other["assets"]["control"]["sha256"] = "c" * 64
        elif change == "count":
            other["dataset"]["libraries"][0]["raw_templates"] = 99
        elif change == "policy":
            other["spec"]["parameters"]["mapq"] = [10]
        elif change == "seed":
            other["spec"]["seed"] = 2
        else:
            other["spec"]["methods"][0]["aligner"] = "bowtie2"
        try:
            matched_inputs([plan, other])
        except ValueError:
            pass
        else:
            raise AssertionError(f"Accepted confounded reference comparison: {change}")
    print("PASS matched reference libraries, controls, sampled bytes and scientific policies")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        bundles = []
        for name, header in (("left", "original"), ("right", "renamed")):
            fasta = root / (name + ".fa")
            fasta.write_text(f">{header}\nACGTACGT\n>repeat1\nAAAA\n>repeat2\nAAAA\n")
            bundle = root / (name + ".json")
            bundle.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "scan": inspect_fasta(fasta),
                        "reference": {"species": "synthetic", "fasta": "fasta"},
                        "assets": {"fasta": {"path": str(fasta), "sha256": sha256(fasta)}},
                    }
                )
            )
            bundles.append(bundle)
        bind(*bundles, None, root / "mapped")
        mapping = json.loads((root / "mapped/mapping.json").read_text())
        assert mapping["identity_mapping"]["matched_contigs"] == 1
        assert mapping["identity_mapping"]["excluded_nonunique_contigs"] == [
            ["repeat1", "repeat2"],
            ["repeat1", "repeat2"],
        ]
        assert "original 8 + 0 8 renamed 8 + 0 8" in (root / "mapped/forward.chain").read_text()
    print("PASS identical contig mapping excludes duplicated sequence identities")


if __name__ == "__main__":
    main()
