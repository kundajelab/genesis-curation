"""Built-in guidance is installed, routed and used without external skill files."""

from __future__ import annotations

import tempfile
from pathlib import Path

from genesis_curation.contracts.records import fingerprint
from genesis_curation.web import mining, preparation, skills


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary) / "no-custom-skills"
        catalog = skills.available(folder)
        ids = {item["id"] for item in catalog}
        expected = {
            "RNA-seq": "plant-rna-metadata",
            "TSS": "plant-tss-metadata",
            "Bulk ATAC": "plant-atac-metadata",
            "scATAC": "plant-atac-metadata",
            "DAP-seq": "plant-binding-metadata",
            "ChIP-seq": "plant-binding-metadata",
            "WGBS": "plant-methylation-metadata",
            "MPRA": "plant-mpra-metadata",
            "Genomes": "plant-genome-metadata",
        }
        for modality, target in expected.items():
            state = {"original": {"_modality": modality}, "fields": [], "sources": []}
            assert preparation.select_skill(folder, state)["id"] == target
        for assay, target in {
            "RNA-seq": "plant-rna-metadata",
            "scRNA-seq": "plant-celltype-metadata",
            "ATAC-seq": "plant-atac-metadata",
            "scATAC-seq": "plant-atac-metadata",
            "CAGE": "plant-tss-metadata",
            "RAMPAGE": "plant-tss-metadata",
            "WGBS": "plant-methylation-metadata",
            "bisulfite-seq": "plant-methylation-metadata",
            "MPRA": "plant-mpra-metadata",
            "STARR-seq": "plant-mpra-metadata",
            "ChIP-seq": "plant-binding-metadata",
            "DAP-seq": "plant-binding-metadata",
        }.items():
            state = {
                "original": {"library_strategy": assay},
                "fields": [{"field": "assay", "value": assay, "status": "known"}],
                "sources": [],
            }
            assert preparation.select_skill(folder, state)["id"] == target, assay
        # Mentioning another assay in a sample description must not override a known modality.
        state = {
            "original": {"library_strategy": "WGBS"},
            "fields": [],
            "sources": [
                {"version": "text", "data": {"snapshot": "Compared to single cell RNA-seq"}}
            ],
        }
        assert preparation.select_skill(folder, state)["id"] == "plant-methylation-metadata"
        assert mining.GUIDANCE == skills.select(None, "plant-discovery")
        for required in (
            "plant-literature-evidence",
            "plant-measured-qc",
            "plant-study-review",
            "plant-identity-metadata",
        ):
            assert required in ids
        for item in catalog:
            assert skills.COMMON in item["instructions"]
            assert item["references"] and len(item["instructions"]) <= 16000
            assert item["sha256"] == fingerprint({k: v for k, v in item.items() if k != "sha256"})
        assert "Poly(A)" in skills.select(None, "plant-celltype-metadata")["instructions"]
        assert "abstain" in skills.select(None, "plant-celltype-metadata")["instructions"]
        assert "CHH" in skills.select(None, "plant-methylation-metadata")["instructions"]
    print(
        "PASS built-in skills: all nine modalities, accession routing, "
        "shared discovery guidance, hashes and safeguards"
    )


if __name__ == "__main__":
    main()
