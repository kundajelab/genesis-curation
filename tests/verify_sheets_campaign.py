"""Sheet campaign: reused gold measurements per workflow, scoring only with gold, failures
recorded without stopping the other workflows; species with several taxa."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

from genesis_curation.sheets import cli
from genesis_curation.sheets.species import load


def check_taxa() -> None:
    sorghum, rice = load("Sorghum_bicolor"), load("Oryza_sativa")
    assert sorghum.tax_query == "tax_eq(4558)", sorghum.tax_query
    assert rice.tax_query == "(tax_eq(4530) OR tax_eq(39947))", rice.tax_query
    assert rice.has_taxon("39947") and not rice.has_taxon("39946")
    assert rice.ncbi_organism.startswith("(txid4530[Organism:noexp] OR ")


def main() -> None:
    check_taxa()
    builds: list[argparse.Namespace] = []
    scored: list[str] = []

    def fake_build(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
        builds.append(args)
        if args.workflow == "dap-seq":
            raise RuntimeError("archive unavailable")
        args.out.mkdir(parents=True)
        return {"in_sheet": 2, "runs": 3}, 0

    def fake_score(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
        scored.append(args.workflow)
        return {"precision": 1.0, "recall": 0.5, "traps": {"passed": 1, "checked": 2}}, 0

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = root / "gold.yaml"
        manifest.write_text(
            "gold:\n"
            "  - {species: Sorghum_bicolor, workflow: rna-seq, sheet: s.tsv, runs_qc: s.qc.tsv}\n"
            "  - {species: Sorghum_bicolor, workflow: tss-seq, sheet: t.tsv, runs_qc: t.qc.tsv}\n"
            "  - {species: Oryza_sativa_x, workflow: atac-seq, sheet: a.tsv, runs_qc: a.qc.tsv}\n"
        )
        args = argparse.Namespace(
            species="Sorghum_bicolor",
            out=root / "out",
            workflows=["rna-seq", "tss-seq", "atac-seq", "dap-seq"],
            gold_dir=root / "gold",
            cache=None,
            offline=True,
            no_probe=True,
            atac_extra_runs=root / "extra.tsv",
            dap_skip_runs_in=[root / "repo.tsv"],
            manifest=manifest,
            traps=None,
            history=None,
            label="test",
        )
        with (
            mock.patch.object(cli, "run_build", fake_build),
            mock.patch.object(cli, "run_score", fake_score),
        ):
            report, status = cli.run_campaign(args)

        by_workflow = {b.workflow: b for b in builds}
        assert by_workflow["rna-seq"].strandedness_from == root / "gold" / "s.qc.tsv"
        assert by_workflow["rna-seq"].no_measure is True
        assert by_workflow["tss-seq"].measure_from == root / "gold" / "t.qc.tsv"
        # no Sorghum ATAC gold in this manifest: nothing to reuse
        assert by_workflow["atac-seq"].measure_from is None
        assert by_workflow["atac-seq"].extra_runs == root / "extra.tsv"
        assert by_workflow["dap-seq"].skip_runs_in == [root / "repo.tsv"]
        assert by_workflow["tss-seq"].cache == root / "out" / "cache" / "tss-seq"
        assert scored == ["rna-seq", "tss-seq"], scored
        assert status == 1
        workflows = report["workflows"]
        assert "error" in workflows["dap-seq"]
        assert workflows["rna-seq"]["traps"] == "1/2"
        assert "precision" not in workflows["atac-seq"]
        saved = json.loads((root / "out" / "campaign.json").read_text())
        assert saved == report
        assert (root / "out" / "rna-seq" / "score.json").exists()
    print("sheets campaign: ok")


if __name__ == "__main__":
    main()
