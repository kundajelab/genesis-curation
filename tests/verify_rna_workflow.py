"""RNA metadata, exact paper bindings and restart-safe study enrichment."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

from genesis_curation.contracts.records import canonical, fingerprint
from genesis_curation.llm import providers
from genesis_curation.web import (
    catalog,
    constraints,
    intake,
    papers,
    preparation,
    research_search,
    runtime,
    studies,
    taxonomy,
    workflow,
)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="rna-evidence-") as folder:
        root = Path(folder)
        runtime.initialize(root, "tester", "synthetic-rna-password")
        ids = catalog.import_rows(
            root / "catalog",
            canonical(
                [
                    {
                        "id": "SRR1234",
                        "run_accession": "SRR1234",
                        "sample_name": "leaf_01",
                        "organism": "Sorghum bicolor",
                        "assay": "RNA-seq",
                        "library_selection": "polyA",
                        "cell_resolution": "bulk",
                        "age": "14",
                        "age_unit": "days after germination",
                    },
                    {
                        "id": "SRR12345",
                        "run_accession": "SRR12345",
                        "sample_name": "leaf_02",
                        "organism": "Sorghum bicolor",
                        "assay": "RNA-seq",
                    },
                ]
            ),
            "synthetic.json",
            "synthetic",
            "id",
            "public",
            "tester",
        )
        key = studies.link(root / "catalog", ids, "SRP123456", "tester", accession="SRP123456")
        rows = studies.get(root / "catalog", key)["records"]
        by_name = {r["name"]: r for r in rows}
        one, two = by_name["SRR1234"], by_name["SRR12345"]
        matches = papers.bind(
            rows,
            "SRR12345 is from roots.\nSRR1234 and SRR12345 were sequenced.\n"
            "leaf_01: leaf tissue.\nGSE123456 used leaves.\nSRR123456789 unrelated.",
        )
        assert len(matches[one["id"]]) == len(matches[two["id"]]) == 1
        assert matches[one["id"]][0]["alias"] == "leaf_01"
        fields = {f["field"]: f for f in one["fields"]}
        assert fields["age_unit"]["value"] == "days after germination"
        assert fields["library_selection"]["value"] == "polyA"
        assert preparation.select_skill(root / "skills", one)["id"] == "plant-rna-metadata"
        cell = {
            **one,
            "original": {**one["original"], "_modality": "RNA-seq"},
            "fields": [
                {**f, "value": "single-cell RNA-seq"} if f["field"] == "assay" else f
                for f in one["fields"]
            ],
        }
        assert preparation.select_skill(root / "skills", cell)["id"] == "plant-celltype-metadata"
        requested = {"library_selection": "polyA", "tissue": "root"}
        checks = constraints.assess(one, requested)
        assert checks["library_selection"]["status"] == "supported"
        assert checks["tissue"]["status"] == "unresolved"
        searches = research_search.recipes(["Sorghum bicolor"], ["RNA-seq"], "RNA pilot", requested)
        assert any(q.get("terms") == ["polyA", "root"] for q in searches)
        assert any(not q.get("terms") for q in searches)  # Preserve broad coverage.
        config_path = Path(__file__).parents[1] / "examples/curation/providers.mock.json"
        config = providers.select(config_path, "mock") | {"mode": "live"}
        task = {
            "id": "synthetic-job",
            "actor": "tester",
            "payload": {
                "study": key,
                "provider": "mock",
                "provider_hash": fingerprint(config),
                "constraints": requested,
            },
        }
        calls = []

        def annotate(*args: object, **kwargs: object) -> dict[str, Any]:
            calls.append("annotate")
            studies.preparation(root / "catalog", key, {"status": "COMPLETED"})
            return {"study": key}

        def enrich(*args: object, **kwargs: object) -> dict[str, Any]:
            intake.attach(
                root / "catalog",
                one["id"],
                "tissue: leaf\nSRR1234",
                "https://www.ebi.ac.uk/europepmc/test",
                "sample",
                "tester",
            )
            return {"status": "retrieved", "changed": [one["id"]]}

        with (
            patch.object(providers, "select", return_value=config),
            patch.object(preparation, "annotate", side_effect=annotate),
            patch.object(papers, "enrich", side_effect=enrich),
            patch.object(taxonomy, "resolve", return_value={}) as resolver,
        ):
            result = workflow.run(root, config_path, task)
            assert result["workflow_stage"] == "paper-retrieval" and result["pending"]
            assert studies.get(root / "catalog", key)["preparation"]["status"] == "RUNNING"
            # Simulate evidence attachment before a lost checkpoint. An idempotent retry must
            # still annotate the attached paper evidence.
            enrich()
            with patch.object(
                papers, "enrich", return_value={"status": "retrieved", "changed": []}
            ):
                assert workflow.run(root, config_path, task)["workflow_stage"] == "paper-annotation"
            assert workflow.run(root, config_path, task)["workflow_stage"] == "validation"
            assert not workflow.run(root, config_path, task)["pending"]
            assert len(calls) == 2 and resolver.call_count == 1
            state = studies.get(root / "catalog", key)
            flow = state["preparation"]["workflow"]
            assert flow["final"]["supported"] > flow["initial"]["supported"]
            assert flow["constraints"][one["id"]]["tissue"]["status"] == "contradicted"
            assert any("Requested root" in t["reason"] for t in state["tasks"])
            workflow.run(root, config_path, task)
            assert len(calls) == 2  # Completed checkpoint does not re-invoke the model.
        xml = (
            "<TaxaSet><Taxon><ScientificName>Sorghum bicolor</ScientificName><LineageEx>"
            "<Taxon><TaxId>33090</TaxId></Taxon></LineageEx></Taxon></TaxaSet>"
        )
        with (
            patch.object(intake, "fetch", return_value={"text": xml, "retrieved_at": "fixture"}),
            patch.object(taxonomy.mining_sources, "taxon", return_value=4558),
        ):
            resolved = taxonomy.resolve(
                root, studies.get(root / "catalog", key)["records"], "tester"
            )
        assert resolved["Sorghum bicolor"]["id"] == "NCBITaxon:4558"
        assert catalog.get(root / "catalog", one["id"])["taxonomy"]["status"] == "verified"
    print("PASS RNA workflow: exact sample binding, constraints, checkpoints, enrichment, taxonomy")


if __name__ == "__main__":
    main()
