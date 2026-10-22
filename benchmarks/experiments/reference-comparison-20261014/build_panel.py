"""Build the experiment's explicit reference panel from verified local assets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from genesis_curation.benchmark.spec import sha256, write_json


def build(work: Path, originals: Path, out: Path) -> None:
    references = []
    downloaded = json.loads((work / "reference-downloads/downloads.json").read_text())["assets"]
    existing = json.loads((originals / "acquisition-state.json").read_text())["assets"]

    def asset(name: str, *, old: bool = False) -> dict[str, str]:
        record = (existing if old else downloaded)[name]
        if old and not record["verified"]:
            raise ValueError("Original reference has not been verified")
        path = (originals if old else work / "reference-downloads") / name
        if sha256(path) != record["sha256"]:
            raise ValueError("Reference changed: " + name)
        return {
            "path": str(path.resolve()),
            "sha256": record["sha256"],
            "url": record["url"],
        }

    def add(
        name: str,
        species: str,
        cultivar: str,
        assembly: str,
        accession: str,
        fasta: dict[str, str],
        source: dict[str, Any],
        annotation: dict[str, str] | None = None,
        annotation_release: str | None = None,
        organelles: tuple[str, str] | None = None,
        mode: str = "transcript_features",
    ) -> None:
        row = {
            "id": name,
            "species": species,
            "cultivar": cultivar,
            "assembly": assembly,
            "assembly_accession": accession,
            "fasta": fasta,
            "source": source,
            "mitochondrial": [organelles[0]] if organelles else [],
            "plastid": [organelles[1]] if organelles else [],
        }
        if annotation:
            row.update(
                annotation=annotation,
                annotation_release=annotation_release,
                annotation_tss_mode=mode,
            )
        references.append(row)
        if name == "col-cen-1.2-tair10":
            row["annotation_transcript_types"] = [
                "mRNA",
                "tRNA",
                "ncRNA",
                "miRNA",
                "snoRNA",
                "rRNA",
                "snRNA",
            ]

    add(
        "tair10-araport11",
        "Arabidopsis_thaliana",
        "Col-0",
        "TAIR10",
        "EnsemblPlants59/TAIR10",
        asset("arabidopsis.fa.gz", old=True),
        {"distribution": "Ensembl Plants 59; gene_source araport11"},
        asset("arabidopsis.gtf.gz", old=True),
        "Araport11 via Ensembl Plants 59",
        ("Mt", "Pt"),
    )
    for release, mode in (
        ("araport11", "exon_bounds"),
        ("tair10", "transcript_features"),
    ):
        add(
            "col-cen-1.2-" + release,
            "Arabidopsis_thaliana",
            "Col-0",
            "Col-CEN v1.2",
            "Col-CEN/abb9b614d91c8a0bbd05a199c694fb5eafb5fe30/v1.2",
            asset("Col-CEN_v1.2.fasta.gz"),
            {
                "distribution": "Author GitHub release; Git blob hashes verified",
                "url": "https://github.com/schatzlab/Col-CEN/tree/abb9b614d91c8a0bbd05a199c694fb5eafb5fe30/v1.2",
            },
            asset("Col-CEN_v1.2_genes." + release + ".gff3.gz"),
            release + " lifted to Col-CEN v1.2",
            ("ChrM", "ChrC"),
            mode,
        )
    add(
        "lyrata-ensembl19",
        "Arabidopsis_lyrata",
        "MN47",
        "v1.0",
        "EnsemblPlants19/v.1.0",
        asset("Arabidopsis_lyrata.v.1.0.19.dna.toplevel.fa.gz"),
        {
            "distribution": "Ensembl Plants 19",
            "role": "Repository legacy filename reconstructed from the matching release",
        },
    )
    add(
        "lyrata-phytozome2.1",
        "Arabidopsis_lyrata",
        "MN47",
        "v1",
        "Phytozome:384",
        asset("Alyrata_384_v1.fa"),
        {
            "distribution": "JGI public NERSC mirror",
            "study": "GSE298980",
            "bundle_version": "v2.1",
            "qualification": "v2.1 is the annotation bundle; FASTA is v1",
        },
        asset("Alyrata_384_v2.1.gene_exons.gff3"),
        "Phytozome v2.1",
    )
    add(
        "sorghum-sorbi1",
        "Sorghum_bicolor",
        "BTx623",
        "Sorbi1",
        "EnsemblPlants19/Sorbi1",
        asset("Sorghum_bicolor.Sorbi1.19.dna.toplevel.fa.gz"),
        {
            "distribution": "Ensembl Plants 19",
            "role": "Repository legacy filename reconstructed from the matching release",
        },
    )
    for folder, browser, name, cultivar, assembly, accession, role in (
        (
            "browser-rtx430",
            "SbicolorRTx430_v2_1",
            "sorghum-rtx430-v2",
            "RTx430",
            "v2.0",
            "Phytozome:552",
            "Study GSE299020 reference; v2.1 bundle",
        ),
        (
            "browser-btx623",
            "Sbicolor_v5_1",
            "sorghum-btx623-v5",
            "BTx623",
            "v5.0",
            "Phytozome:730",
            "Assembly named in repository test sheet; v5.1 browser annotation bundle",
        ),
    ):
        receipt = work / folder / "receipt.json"
        if receipt.exists():
            record = json.loads(receipt.read_text())
            fasta = work / folder / record["fasta"]["name"]
            if sha256(fasta) != record["fasta"]["sha256"]:
                raise ValueError("Browser reference changed")
            add(
                name,
                "Sorghum_bicolor",
                cultivar,
                assembly,
                accession,
                {
                    "path": str(fasta.resolve()),
                    "sha256": record["fasta"]["sha256"],
                    "url": record["source"],
                },
                {
                    "distribution": "Publisher public JBrowse sequence",
                    "browser_dataset": browser,
                    "role": role,
                    "integrity": record["integrity"],
                    "receipt_sha256": sha256(receipt),
                },
            )
    add(
        "maize-agpv4",
        "Zea_mays",
        "B73",
        "B73 RefGen v4",
        "EnsemblPlants46/B73_RefGen_v4",
        asset("maize.fa.gz", old=True),
        {
            "distribution": "Ensembl Plants 46",
            "study_doi": "10.3389/fpls.2024.1370618",
            "control_model": "The benchmark has no publication gDNA control",
        },
        asset("maize.gtf.gz", old=True),
        "Ensembl Plants 46 / AGPv4",
        ("Mt", "Pt"),
    )
    derived = work / "derived-references/nam5-with-v4-organelles.fa.gz"
    if derived.with_suffix(".gz.json").exists():
        record = json.loads(derived.with_suffix(".gz.json").read_text())
        if sha256(derived) != record["sha256"]:
            raise ValueError("Derived reference changed")
        add(
            "maize-nam5-common-organelles",
            "Zea_mays",
            "B73",
            "B73 NAM v5 plus AGPv4 Mt/Pt",
            "GCA_902167145.1 + explicit organelle supplement",
            {
                "path": str(derived.resolve()),
                "sha256": record["sha256"],
                "url": downloaded["Zm-B73-REFERENCE-NAM-5.0.fa.gz"]["url"],
            },
            {
                "distribution": "MaizeGDB NAM5 nuclear FASTA with unchanged Ensembl46 Mt/Pt",
                "derivation": record,
            },
            asset("Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.gff3.gz"),
            "Zm00001eb.1",
            ("Mt", "Pt"),
        )
    write_json(out, {"schema_version": 1, "references": references})
    print(out, len(references), "available reference/annotation bundles")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--original-sources", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    build(args.work, args.original_sources, args.out)
