"""Group bundles from the preprocessing scATAC workflow, with chrombpnet-atac-v1 cut signal.

Set GENESIS_BIGWIG_PYTHON to a Python with pyBigWig to also write and read back real bigWigs;
otherwise a stand-in writer checks the same counts from the bedGraph.
"""

from __future__ import annotations

import gzip
import json
import os
import stat
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from genesis_curation.contracts.records import dump, load
from genesis_curation.scatac import model, workflow
from genesis_curation.scatac.common import verify_output

SIZES = {"chr1": 400, "chr2": 300, "chr3": 300, "Pt": 60}
# Sorted by contig name, then start, as the workflow writes them. The last chr3 row ends at the
# contig end, so its chrombpnet-atac-v1 end cut falls outside the reference.
FRAGMENTS = [
    ("chr1", 100, 150, "AAA-1", 2),
    ("chr1", 100, 180, "CCC-1", 1),
    ("chr1", 120, 150, "AAA-1", 1),
    ("chr2", 50, 90, "AAA-1", 1),
    ("chr3", 200, 300, "CCC-1", 3),
]
CELLS = [("AAA-1", "g1", 3, 4), ("CCC-1", "g2", 2, 4)]
FAKE_WRITER = """#!{python}
import json, sys
sizes, bedgraph, out = sys.argv[4:7]
lines = [line.split() for line in open(bedgraph)]
assert all(int(end) == int(start) + 1 for _, start, end, _ in lines)
open(out, "w").write("".join("\\t".join(line) + "\\n" for line in lines))
contigs = {{c: int(n) for c, n in (line.split() for line in open(sizes))}}
print(json.dumps({{"sum_counts": sum(int(line[3]) for line in lines), "contigs": contigs}}))
"""


def rejects(function: Callable[..., Any], *args: object, **kwargs: object) -> None:
    try:
        function(*args, **kwargs)
    except ValueError:
        return
    raise AssertionError("Invalid workflow output was accepted")


def write_run(root: Path, fragments: list[tuple[str, int, int, str, int]]) -> Path:
    run = root / "output" / "Plant" / "lib"
    (run / "qc").mkdir(parents=True, exist_ok=True)
    with gzip.open(run / "lib.fragments.tsv.gz", "wt") as stream:
        stream.writelines("\t".join(map(str, row)) + "\n" for row in fragments)
    header = "barcode\tgroup\tfragments\tread_pairs\torganelle_fragments\torganelle_fraction\n"
    (run / "qc" / "lib.cells.tsv").write_text(
        header + "".join(f"{b}\t{g}\t{n}\t{s}\t0\t0\n" for b, g, n, s in CELLS)
    )
    peaks = {
        "g1": [("chr1", 90, 170, 20), ("chr2", 40, 100, 25), ("chr3", 150, 260, 60)],
        "g2": [("chr1", 90, 190, 50)],
    }
    for group, rows in peaks.items():
        folder = run / "groups" / group
        folder.mkdir(parents=True, exist_ok=True)
        with gzip.open(folder / f"lib.{group}.macs3_peaks.narrowPeak.gz", "wt") as stream:
            for i, (chrom, start, end, summit) in enumerate(rows):
                stream.write(f"{chrom}\t{start}\t{end}\tp{i}\t10\t.\t2\t3\t1\t{summit}\n")
        member = {b: g for b, g, _, _ in CELLS}
        fragments_in = sum(1 for f in fragments if member.get(f[3]) == group)
        cells_in = sum(1 for _, g, _, _ in CELLS if g == group)
        (folder / f"lib.{group}.scatac_qc_mqc.tsv").write_text(
            "# id: scatac_qc\npseudobulk\tcells\tfragments\tinsertions\tinsertions_in_peaks\tFRiP"
            f"\tpeaks\tTSS_enrichment\nlib.{group}\t{cells_in}\t{fragments_in}"
            f"\t{2 * fragments_in}\t1\t0.25\t{len(rows)}\tNA\n"
        )
    return run


def main() -> None:
    assert workflow.cuts(100, 150) == (100, 150), "10x fragments: cuts at start and end"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fasta = root / "genome.fa"
        fasta.write_text("".join(f">{c} test\n{'ACGT' * (n // 4)}\n" for c, n in SIZES.items()))
        writer = os.environ.get("GENESIS_BIGWIG_PYTHON")
        if writer is None:
            fake = root / "fake-bigwig"
            fake.write_text(FAKE_WRITER.format(python=sys.executable))
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            bigwig_python = fake
        else:
            bigwig_python = Path(writer)
        fields = {
            "fasta": fasta,
            "reference_id": "plant-v1",
            "assembly": "Plant v1",
            "species": "Plant",
            "mitochondrial_contigs": [],
            "plastid_contigs": ["Pt"],
        }

        # Exact per-base counts: both cuts of every fragment, the boundary cut excluded.
        scratch = root / "scratch"
        scratch.mkdir()
        stats = workflow.aggregate(
            write_run(root / "unit", FRAGMENTS) / "lib.fragments.tsv.gz",
            {b: g for b, g, _, _ in CELLS},
            SIZES,
            scratch,
        )
        assert (scratch / "g1.bedGraph").read_text().splitlines() == [
            "chr1\t100\t101\t1",
            "chr1\t120\t121\t1",
            "chr1\t150\t151\t2",
            "chr2\t50\t51\t1",
            "chr2\t90\t91\t1",
        ]
        assert (scratch / "g2.bedGraph").read_text().splitlines() == [
            "chr1\t100\t101\t1",
            "chr1\t180\t181\t1",
            "chr3\t200\t201\t1",
        ]
        assert stats["g2"] == {
            "unique_fragments": 2,
            "total_support": 4,
            "cut_count": 3,
            "excluded_boundary_cuts": 1,
        }

        run = write_run(root / "good", FRAGMENTS)
        result = workflow.import_library(run, fields, bigwig_python, root / "bundles")
        assert result["cached"] is False
        for group in ("g1", "g2"):
            bundle = verify_output(root / "bundles" / group, "scatac-group")
            data = bundle["data"]
            assert data["policy"]["signal_convention"] == "chrombpnet-atac-v1"
            assert data["group"]["identity"]["coordinate_offsets"] == [4, -5]
            assert data["group"] == load(root / "bundles" / group / "metadata.json")
            assert data["review_status"] == "UNREVIEWED" and data["model_ready"] is False
        cells = (root / "bundles" / "g1" / "cells.jsonl").read_text().splitlines()
        assert [json.loads(line)["cell_id"] for line in cells] == ["lib:AAA-1"]
        cached = workflow.import_library(run, fields, bigwig_python, root / "bundles", resume=True)
        assert cached["cached"] is True
        rejects(workflow.import_library, run, fields, bigwig_python, root / "bundles")

        # The bundle is what model export reads.
        config = {
            "schema_version": 1,
            "target": "cherimoya",
            "model_sha": model.MODELS["cherimoya"],
            "input_window": 20,
            "output_window": 10,
            "max_jitter": 0,
            "folds": {"train": ["chr1"], "validation": ["chr2"], "test": ["chr3"]},
            "background_method": "nonoverlapping-genome-tiles-v1",
        }
        dump(root / "model.json", config)
        exported = model.export(root / "bundles" / "g1", root / "model.json", root / "m1")
        data = exported["manifest"]["data"]
        assert data["peaks_per_fold"] == {"train": 1, "validation": 1, "test": 1}
        assert data["reference"]["plastid_contigs"] == ["Pt"]
        # g2 has peaks on chr1 only, so its validation and test folds are empty.
        rejects(model.export, root / "bundles" / "g2", root / "model.json", root / "m2")

        # Inconsistent or unsorted workflow outputs stop the import.
        unsorted = [FRAGMENTS[2], *FRAGMENTS[:2], *FRAGMENTS[3:]]
        rejects(
            workflow.import_library,
            write_run(root / "unsorted", unsorted),
            fields,
            bigwig_python,
            root / "out-unsorted",
        )
        stranger = [*FRAGMENTS[:4], ("chr3", 200, 300, "GGG-1", 3)]
        rejects(
            workflow.import_library,
            write_run(root / "stranger", stranger),
            fields,
            bigwig_python,
            root / "out-stranger",
        )
        missing = write_run(root / "missing", FRAGMENTS[:-1])
        rejects(workflow.import_library, missing, fields, bigwig_python, root / "out-missing")

        # Cells with only organelle fragments are listed but in no group's outputs; a group of
        # only such cells has no folder. Both are recorded and skipped.
        sparse = write_run(root / "sparse", FRAGMENTS)
        with (sparse / "qc" / "lib.cells.tsv").open("a") as stream:
            stream.write("DDD-1\tg1\t0\t5\t5\t1\nEEE-1\tg3\t0\t2\t2\t1\n")
        result = workflow.import_library(sparse, fields, bigwig_python, root / "out-sparse")
        data = result["manifest"]["data"]
        assert sorted(data["groups"]) == ["g1", "g2"]
        assert data["num_cells_without_fragments"] == 2
        assert data["groups_without_fragments"] == ["g3"]
        cells = (root / "out-sparse" / "g1" / "cells.jsonl").read_text().splitlines()
        assert [json.loads(line)["cell_id"] for line in cells] == ["lib:AAA-1"]
    print("PASS: workflow scATAC import, chrombpnet-atac-v1 cuts, model export compatibility")


if __name__ == "__main__":
    main()
