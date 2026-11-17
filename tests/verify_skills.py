"""Built-in guidance is installed, routed and used without external skill files."""

from __future__ import annotations

import tempfile
from pathlib import Path

from helpers import rejects

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
            assert "Shared evidence rules" in item["instructions"]
            assert item["description"]
            assert item["references"] and len(item["instructions"]) <= 16000
            assert item["sha256"] == fingerprint({k: v for k, v in item.items() if k != "sha256"})
        assert "Poly(A)" in skills.select(None, "plant-celltype-metadata")["instructions"]
        assert "abstain" in skills.select(None, "plant-celltype-metadata")["instructions"]
        assert "CHH" in skills.select(None, "plant-methylation-metadata")["instructions"]
        custom = folder / "custom-evidence"
        custom.mkdir(parents=True)
        references = custom / "references"
        references.mkdir()
        guidance = references / "evidence.md"
        guidance.write_text("Check exact sample accessions.")
        content = (
            "---\nname: custom-evidence\ndescription: Review supplied evidence.\n"
            "metadata:\n  version: '1'\n  includes: [references/evidence.md]\n---\n"
            "Keep unresolved sample mappings for review.\n"
        )
        entry = custom / "SKILL.md"
        entry.write_text(content)
        first = skills.select(folder, "custom-evidence")
        assert "Check exact sample accessions." in first["instructions"]
        guidance.write_text("Require exact experiment accessions too.")
        assert skills.select(folder, "custom-evidence")["sha256"] != first["sha256"]
        # Local includes cannot read neighboring files or credentials.
        (folder / "private.md").write_text("Synthetic private text")
        entry.write_text(content.replace("references/evidence.md", "../private.md"))
        rejects(lambda: skills.available(folder))
        entry.write_text(content)
        guidance.unlink()
        guidance.symlink_to(folder / "private.md")
        rejects(lambda: skills.available(folder))
        guidance.unlink()
        guidance.write_text("x" * 16001)
        rejects(lambda: skills.available(folder))
        guidance.write_text("Valid evidence guidance.")
        entry.write_text(content.replace("metadata:", "metadata: !!python/tuple"))
        rejects(lambda: skills.available(folder))
        entry.write_text(content.replace("custom-evidence", "wrong-folder"))
        rejects(lambda: skills.available(folder))
        entry.write_text(content)
        duplicate = folder / "plant-metadata"
        duplicate.mkdir()
        (duplicate / "SKILL.md").write_text(
            content.replace("custom-evidence", "plant-metadata").replace(
                "  includes: [references/evidence.md]\n", ""
            )
        )
        rejects(lambda: skills.available(folder))
        (duplicate / "SKILL.md").unlink()
        duplicate.rmdir()
        (folder / "old.json").write_text("{}")
        rejects(lambda: skills.available(folder))

    print(
        "PASS built-in skills: all nine modalities, accession routing, "
        "shared discovery guidance, hashes and safeguards"
    )


if __name__ == "__main__":
    main()
