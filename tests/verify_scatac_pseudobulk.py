"""Compare streaming pseudobulks to the independent pre-existing contract fixture."""

from __future__ import annotations

import copy
import gzip
import importlib.util
import tempfile
from pathlib import Path

from verify_scatac_fragments import ROOT, inputs

from genesis_curation.contracts.records import canonical, dump, load
from genesis_curation.scatac import ingest, pseudobulk


def main() -> None:
    spec = importlib.util.spec_from_file_location(
        "original_contract", ROOT / "tests/fixtures/pseudobulk/contract.py"
    )
    assert spec is not None and spec.loader is not None
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fixture, manifests = inputs(root)
        directories = []
        for manifest in manifests:
            directory = root / manifest.stem
            ingest.run(manifest, directory)
            directories.append(directory)
        annotations = root / "cells.jsonl"
        annotations.write_text("".join(canonical(c) + "\n" for c in fixture["cells"]))
        policy = root / "policy.json"
        dump(
            policy,
            {
                "schema_version": 1,
                "version": "fixture-v1",
                "signal_convention": "chrombpnet-atac-v1",
                "organellar": "exclude",
                "boundary_cuts": "error",
                "independent_library_merges": [],
            },
        )
        result = pseudobulk.aggregate(directories, annotations, policy, root / "groups")
        data = result["manifest"]["data"]
        assert data["counts"] == {
            "input_fragments": 9,
            "input_support": 12,
            "selected_fragments": 9,
            "selected_support": 12,
        }
        expected = contract.form_groups(
            fixture["libraries"], fixture["cells"], fixture["fragments"]
        )
        assert set(data["groups"]) == {g["task_id"] for g in expected}
        for group in expected:
            found = data["groups"][group["task_id"]]
            assert found["unique_fragments"] == group["usable_fragments"]
            assert found["cell_count"] == len(group["cells"])
            assert found["cut_count"] == 2 * group["usable_fragments"]
            with gzip.open(root / "groups" / group["task_id"] / "fragments.tsv.gz", "rt") as stream:
                assert len(list(stream)) == group["usable_fragments"]
        assert pseudobulk.aggregate(directories, annotations, policy, root / "groups", resume=True)[
            "cached"
        ]
        pseudobulk.aggregate(directories, annotations, policy, root / "repeat")
        assert result["manifest"]["outputs"] == load(root / "repeat/complete.json")["outputs"]
        assert pseudobulk.cuts(10, 30, [4, -5]) == (10, 30)
        assert pseudobulk.cuts(10, 30, [0, 0]) == (14, 25)
        revised = copy.deepcopy(fixture["cells"])
        revised[0]["harmonized_label"] = "revised"
        annotations.write_text("".join(canonical(c) + "\n" for c in revised))
        try:
            pseudobulk.aggregate(directories, annotations, policy, root / "groups", resume=True)
        except ValueError:
            pass
        else:
            raise AssertionError("Stale annotations accepted by resume")
        revised.append(revised[0])
        annotations.write_text("".join(canonical(c) + "\n" for c in revised))
        try:
            pseudobulk.aggregate(directories, annotations, policy, root / "duplicate")
        except ValueError:
            pass
        else:
            raise AssertionError("Duplicate cell annotations accepted")
    print("PASS: task identities, replicates, conservation, cut positions, resume")


if __name__ == "__main__":
    main()
