"""Sample-sheet campaigns in the app: request checks, the worker job, and listings that
expose counts and file names but no server paths."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest import mock

from helpers import rejects

from genesis_curation.web import runtime, sheets_api, worker


def main() -> None:
    assert sheets_api.campaign(
        {"species": "Oryza_sativa", "workflows": ["tss-seq", "rna-seq"]}
    ) == {
        "species": "Oryza_sativa",
        "workflows": ["rna-seq", "tss-seq"],
    }
    rejects(lambda: sheets_api.campaign({"species": "Homo_sapiens", "workflows": ["rna-seq"]}))
    rejects(lambda: sheets_api.campaign({"species": "Oryza_sativa", "workflows": []}))
    rejects(lambda: sheets_api.campaign({"species": "Oryza_sativa", "workflows": ["../x"]}))
    rejects(lambda: sheets_api.campaign(["rna-seq"]))

    with tempfile.TemporaryDirectory(prefix="genesis-sheets-web-") as temporary:
        root = Path(temporary)
        runtime.initialize(root, "curator", "synthetic-sheets-password")
        payload = sheets_api.campaign({"species": "Sorghum_bicolor", "workflows": ["rna-seq"]})
        key = runtime.enqueue(root, sheets_api.KIND, "curator", payload)
        steps: list[str] = []

        def fake_campaign(
            args: argparse.Namespace, step: Callable[[int, str], None]
        ) -> tuple[dict[str, Any], int]:
            assert args.out == root / "sheets" / key and args.cache == root / "sheets" / "cache"
            assert args.gold_dir is None  # no evals folder in this test
            for number, workflow in enumerate(args.workflows):
                step(number, workflow)
                steps.append(workflow)
            out = args.out / "rna-seq"
            out.mkdir(parents=True)
            (out / "rna-seq.tsv").write_text("sample_id\n")
            (out / "rna-seq.runs-qc.tsv").write_text("run\n")
            report = {
                "species": args.species,
                "workflows": {
                    "rna-seq": {"in_sheet": 1, "runs": 2, "sheet": str(out / "rna-seq.tsv")}
                },
            }
            (args.out / "campaign.json").write_text(json.dumps(report))
            return report, 0

        task = {"id": key, "kind": sheets_api.KIND, "payload": payload, "actor": "curator"}
        with (
            mock.patch.object(sheets_api.sheets, "EVALS", root / "no-evals"),
            mock.patch.object(sheets_api.sheets, "run_campaign", fake_campaign),
        ):
            result = worker.execute(root, root / "providers.json", task)
        assert result == {"campaign": key, "failed": 0}, result
        assert steps == ["rna-seq"]
        [listed] = sheets_api.listing(root)
        assert listed["id"] == key and listed["species"] == "Sorghum_bicolor"
        assert listed["workflows"]["rna-seq"] == {
            "in_sheet": 1,
            "runs": 2,
            "files": ["rna-seq.tsv", "rna-seq.runs-qc.tsv"],
        }
        assert temporary not in json.dumps(listed)
    print("PASS sample-sheet campaigns: request checks, worker job, path-free listing")


if __name__ == "__main__":
    main()
