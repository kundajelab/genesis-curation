"""Fragment conservation, coordinate validation, deterministic serialization and resume."""

from __future__ import annotations

import copy
import csv
import gzip
import tempfile
from pathlib import Path
from typing import Any

import pysam

from genesis_curation.contracts.records import dump, load
from genesis_curation.scatac import ingest
from genesis_curation.scatac.common import digest, reference, verify_output

ROOT = Path(__file__).resolve().parents[1]


def inputs(root: Path) -> tuple[dict[str, Any], list[Path]]:
    fixture = load(ROOT / "tests/fixtures/pseudobulk/fixture.json")
    fasta = ROOT / "tests/fixtures/pseudobulk/reference.fa"
    sizes = {}
    with pysam.FastxFile(str(fasta)) as stream:
        for sequence in stream:
            assert sequence.sequence is not None
            sizes[sequence.name] = len(sequence.sequence)
    paths = []
    for library in fixture["libraries"]:
        lid = library["library_id"]
        fragments = root / (lid + ".tsv")
        fragments.write_text(
            "".join(
                f"{f['chrom']}\t{f['start']}\t{f['end']}\t{f['barcode']}\t{f['support']}\n"
                for f in reversed(fixture["fragments"])
                if f["library_id"] == lid
            )
        )
        data = {
            "schema_version": 1,
            "library": library,
            "fragments": {"path": str(fragments), "sha256": digest(fragments)},
            "producer": {
                "format": "10x-atac",
                "version": "fixture-1",
                "offsets": [4, -5],
                "interval": "BED0-half-open",
                "deduplication_scope": "library-barcode",
                "barcode_correction": None,
                "whitelist_sha256": None,
            },
            "reference": {
                "reference_id": library["reference_id"],
                "species": library["species"],
                "assembly": library["assembly"],
                "fasta": {"path": str(fasta), "sha256": digest(fasta)},
                "contigs": sizes,
                "mitochondrial_contigs": [],
                "plastid_contigs": [],
            },
        }
        path = root / (lid + ".json")
        dump(path, data)
        paths.append(path)
    return fixture, paths


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _fixture, paths = inputs(root)
        long_fasta = root / "unwrapped.fa"
        long_fasta.write_text(">long\n" + "ACGT" * 600000 + "\n")
        ref: dict[str, Any] = {
            "reference_id": "long",
            "assembly": "synthetic",
            "species": "synthetic",
            "fasta": {"path": str(long_fasta), "sha256": digest(long_fasta)},
            "contigs": {"long": 2400000},
            "mitochondrial_contigs": [],
            "plastid_contigs": [],
        }
        assert reference(ref, root)[1] == {"long": 2400000}
        long_fasta.write_text(">long\nACGT*\n")
        ref["fasta"]["sha256"] = digest(long_fasta)
        ref["contigs"] = {"long": 5}
        try:
            reference(ref, root)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid FASTA alphabet accepted")
        totals = {"fragments": 0, "support": 0}
        for path in paths:
            output = root / path.stem
            result = ingest.run(path, output)
            assert not result["cached"]
            counts = result["manifest"]["data"]["counts"]
            for key in totals:
                totals[key] += counts[key]
            assert ingest.run(path, output, resume=True)["cached"]
            second = root / (path.stem + "-second")
            ingest.run(path, second)
            for name in ("fragments.tsv.gz", "fragments.tsv.gz.tbi", "barcodes.tsv"):
                assert (output / name).read_bytes() == (second / name).read_bytes()
            with pysam.TabixFile(str(output / "fragments.tsv.gz")) as index:
                assert len(list(index.fetch())) == counts["fragments"]
            with (output / "barcodes.tsv").open() as stream:
                assert len(list(csv.DictReader(stream, delimiter="\t"))) == counts["barcodes"]
        assert totals == {"fragments": 9, "support": 12}
        data = load(paths[0])
        original = Path(data["fragments"]["path"]).read_text()
        first = original.splitlines()[0]
        failures = {
            "duplicate": original + first + "\n",
            "empty": "",
            "unknown-contig": "unknown\t1\t2\tA\t1\n",
            "bad-interval": "chr1\t20\t10\tA\t1\n",
            "missing-barcode": "chr1\t1\t2\t\t1\n",
            "bad-support": "chr1\t1\t2\tA\t0\n",
        }
        for name, text in failures.items():
            source = root / (name + ".tsv")
            source.write_text(text)
            changed = copy.deepcopy(data)
            changed["fragments"] = {"path": str(source), "sha256": digest(source)}
            manifest = root / (name + ".json")
            dump(manifest, changed)
            try:
                ingest.run(manifest, root / name)
            except ValueError:
                pass
            else:
                raise AssertionError("Invalid input accepted: " + name)
            assert not (root / name).exists()
        data["producer"]["format"] = "10x-arc"
        six = root / "six.tsv"
        six.write_text("\n".join(line + "\t+" for line in original.splitlines()) + "\n")
        data["fragments"] = {"path": str(six), "sha256": digest(six)}
        dump(root / "six.json", data)
        ingest.run(root / "six.json", root / "six")
        with gzip.open(root / "six/fragments.tsv.gz", "rt") as stream:
            assert all(len(line.rstrip().split("\t")) == 6 for line in stream)
        (root / "six/barcodes.tsv").write_text("damaged")
        try:
            verify_output(root / "six", "scatac-fragments")
        except ValueError:
            pass
        else:
            raise AssertionError("Changed output accepted")
    print(
        "PASS: fragments/support conserved, indexed deterministic outputs, ARC strand, invalid rows"
    )


if __name__ == "__main__":
    main()
