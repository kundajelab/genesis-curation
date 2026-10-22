"""Offline reference identity, coordinates and annotation-version checks."""

from __future__ import annotations

import copy
import gzip
import json
import tempfile
from pathlib import Path
from typing import Any

from verify_benchmark_spec import rejects

from genesis_curation.benchmark.reference_mapping import (
    chain_record,
    rename_chain,
    validate_chain,
)
from genesis_curation.benchmark.references import (
    annotation_tss,
    inspect_fasta,
    prepare_reference,
)
from genesis_curation.benchmark.spec import sha256, write_json


def main() -> None:
    plus = "q\t100\t10\t42\t+\tt\t200\t100\t133\t30\t35\t60\tcg:Z:5M2I15M3D10M"
    minus = plus.replace("\t+\t", "\t-\t")
    expected_plus = "chain 30 q 100 + 10 42 t 200 + 100 133 1\n5\t2\t0\n15\t0\t3\n10\n\n"
    expected_minus = "chain 30 q 100 + 10 42 t 200 - 67 100 2\n10\t0\t3\n15\t2\t0\n5\n\n"
    assert chain_record(plus, 1, {"q": 100}, {"t": 200}) == expected_plus
    assert chain_record(minus, 2, {"q": 100}, {"t": 200}) == expected_minus
    for malformed in (
        plus.replace("5M2I15M3D10M", "32M"),
        plus.replace("cg:Z:", "xx:Z:"),
        plus.replace("\t42\t", "\t142\t"),
    ):
        rejects(lambda malformed=malformed: chain_record(malformed, 1, {"q": 100}, {"t": 200}))
    rejects(lambda: chain_record(plus, 1, {"q": 101}, {"t": 200}))
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        chain = root / "known.chain"
        chain.write_text(expected_plus + expected_minus)
        assert validate_chain(chain, {"q": 100}, {"t": 200})["chains"] == 2
        rename_chain(chain, root / "renamed.chain", {"q": "chrq"}, {}, {"chrq": 100}, {"t": 200})
        chain.write_text(expected_plus + expected_minus.replace(" q ", " chrq "))
        rejects(
            lambda: rename_chain(
                chain,
                root / "collision.chain",
                {"q": "chrq"},
                {},
                {"chrq": 100},
                {"t": 200},
            )
        )
        chain.write_text(expected_plus + expected_minus)
        rejects(lambda: validate_chain(chain, {"q": 101}, {"t": 200}))
        for text in (
            expected_plus.replace("\n10\n", "\n9\n"),
            expected_plus + expected_plus,
            expected_plus.replace("15\t0\t3", "15\t0\t-3"),
        ):
            chain.write_text(text)
            rejects(lambda: validate_chain(chain, {"q": 100}, {"t": 200}))
        fasta = root / "reference.fa"
        fasta.write_text(">nuclear description\nACGTacgtNN\n>Mt\nAAAA\n>Pt\nCCCC\n")
        original = inspect_fasta(fasta)
        assert original["contigs"]["nuclear"]["length"] == 10
        assert original["contigs"]["nuclear"]["acgt"] == 8
        assert original["contigs"]["nuclear"]["softmasked"] == 4
        reformatted = root / "wrapped.fa.gz"
        with gzip.open(reformatted, "wt") as stream:
            stream.write(">Pt\ncccc\n>nuclear\nACGT\nACGTNN\n>Mt\nAAAA\n")
        alternate = inspect_fasta(reformatted)
        assert original["sequence_set_sha256"] == alternate["sequence_set_sha256"]
        assert original["content_sha256"] != alternate["content_sha256"]
        renamed = root / "renamed.fa"
        renamed.write_text(fasta.read_text().replace("nuclear description", "chr1"))
        value = inspect_fasta(renamed)
        assert original["sequence_set_sha256"] != value["sequence_set_sha256"]
        assert original["sequence_multiset_sha256"] == value["sequence_multiset_sha256"]
        bad = root / "bad.fa"
        for text in (">a\nACGT\n>a\nACGT\n", ">a\n", "ACGT\n", ">a\nBAD!\n"):
            bad.write_text(text)
            rejects(lambda: inspect_fasta(bad))

        annotation = root / "annotation.gff3"
        annotation.write_text(
            "nuclear\ttest\tmRNA\t2\t8\t.\t+\t.\tID=t1;Parent=g1\n"
            "nuclear\ttest\tmRNA\t2\t8\t.\t-\t.\tID=t2;Parent=g2\n"
            "Mt\ttest\tmRNA\t1\t4\t.\t+\t.\tID=m1;Parent=mg\n"
        )
        positions, genes = annotation_tss(annotation, original["contigs"], {"Mt", "Pt"}, {})
        assert positions == [("nuclear", 1, "+"), ("nuclear", 7, "-")]
        assert len(genes) == 2
        annotation.write_text(
            annotation.read_text() + "nuclear\ttest\ttRNA\t4\t7\t.\t+\t.\tID=t3;Parent=g3\n"
        )
        positions, _ = annotation_tss(
            annotation,
            original["contigs"],
            {"Mt", "Pt"},
            {},
            transcript_types=["mRNA", "tRNA"],
        )
        assert ("nuclear", 3, "+") in positions
        rejects(
            lambda: annotation_tss(
                annotation, original["contigs"], set(), {}, transcript_types=["CDS"]
            )
        )
        rejects(
            lambda: annotation_tss(
                annotation, original["contigs"], set(), {}, "exon_bounds", ["mRNA"]
            )
        )
        exon = root / "exons.gtf"
        exon.write_text(
            'nuclear\ttest\texon\t2\t4\t.\t-\t.\tgene_id "g"; transcript_id "t";\n'
            'nuclear\ttest\texon\t6\t9\t.\t-\t.\tgene_id "g"; transcript_id "t";\n'
        )
        positions, _ = annotation_tss(exon, original["contigs"], set(), {}, "exon_bounds")
        assert positions == [("nuclear", 8, "-")]
        rejects(lambda: annotation_tss(exon, original["contigs"], set(), {}))
        exon.write_text(exon.read_text().replace("\t6\t9\t", "\t6\t11\t"))
        rejects(lambda: annotation_tss(exon, original["contigs"], set(), {}, "exon_bounds"))
        annotation.write_text(annotation.read_text().replace("nuclear", "chr1"))
        rejects(lambda: annotation_tss(annotation, original["contigs"], {"Mt"}, {}))

        panel: dict[str, Any] = {
            "schema_version": 1,
            "references": [
                {
                    "id": "fixture",
                    "species": "synthetic",
                    "cultivar": "synthetic",
                    "assembly": "fixture-v1",
                    "assembly_accession": "synthetic-v1",
                    "fasta": {
                        "path": fasta.name,
                        "sha256": sha256(fasta),
                        "url": "https://example.org/ref.fa",
                    },
                    "annotation": {
                        "path": annotation.name,
                        "sha256": sha256(annotation),
                        "url": "https://example.org/annotation.gff3",
                    },
                    "annotation_release": "fixture-v1",
                    "annotation_contig_aliases": {"chr1": "nuclear"},
                    "mitochondrial": ["Mt"],
                    "plastid": ["Pt"],
                    "source": {"synthetic": True},
                }
            ],
        }
        manifest = root / "panel.json"
        write_json(manifest, panel)
        prepared = prepare_reference(manifest, "fixture", root / "prepared")
        assert prepared["nuclear_acgt"] == 8
        assert prepared == prepare_reference(manifest, "fixture", root / "prepared")
        for field, value in (
            ("schema_version", 2),
            ("schema_version", True),
            ("references", [None]),
        ):
            changed = copy.deepcopy(panel)
            changed[field] = value
            write_json(manifest, changed)
            rejects(lambda: prepare_reference(manifest, "fixture", root / "invalid"))
        write_json(manifest, panel)
        gene_table = root / "prepared/tss-genes.tsv"
        gene_table.write_text(gene_table.read_text() + "corruption\n")
        rejects(lambda: prepare_reference(manifest, "fixture", root / "prepared"))
        fasta.write_text(fasta.read_text().replace("ACGTacgtNN", "ACGTacgtNA"))
        rejects(lambda: prepare_reference(manifest, "fixture", root / "changed"))
        assert json.loads((root / "prepared/reference.json").read_text())["scan"]["scan"] == "FULL"
    print("Benchmark reference identity/annotation checks passed")


if __name__ == "__main__":
    main()
