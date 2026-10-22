"""Offline known-answer benchmark regressions; no network or Docker dependency."""

from __future__ import annotations

import gzip
import json
import tempfile
from pathlib import Path

import pysam

from genesis_curation.benchmark.evaluation import alignment_truth, peak_truth, replicate_evaluation
from genesis_curation.benchmark.metrics import (
    Regions,
    alignment_identity,
    assess,
    compare,
    correlation,
    filter_bam,
    intersection,
    ranks,
    tn5_cuts,
    tss_enrichment,
)
from genesis_curation.benchmark.reads import paired, subset
from genesis_curation.benchmark.spec import identifier, public_url


def rejects(function: object) -> None:
    assert callable(function)
    try:
        function()
    except ValueError:
        return
    raise AssertionError("Expected input rejection")


def bam_checks(root: Path) -> None:
    header = pysam.AlignmentHeader.from_dict(
        {
            "HD": {"VN": "1.6"},
            "SQ": [{"SN": "chr1", "LN": 1000}, {"SN": "plastid", "LN": 1000}],
        }
    )
    records = []
    for name, start, mapqs, duplicate, chrom in [
        ("a", 100, (60, 60), False, 0),
        ("b", 300, (10, 10), False, 0),
        ("c", 500, (60, 20), False, 0),
        ("d", 100, (60, 60), True, 0),
        ("e", 100, (60, 60), False, 1),
        ("f", 500, (255, 255), False, 0),
        ("g", 700, (9, 9), False, 0),
        ("h", 800, (30, 30), False, 0),
    ]:
        for mate in range(2):
            read = pysam.AlignedSegment(header)
            read.query_name = name
            read.flag = (99 if mate == 0 else 147) + (1024 if duplicate else 0)
            read.reference_id = chrom
            read.reference_start = start + mate * 50
            read.mapping_quality = mapqs[mate]
            read.cigarstring = "50M"
            read.query_sequence = "A" * 50
            read.next_reference_id = chrom
            read.next_reference_start = start + (1 - mate) * 50
            read.template_length = 100 if mate == 0 else -100
            records.append(read)
    records.sort(key=lambda r: (r.reference_id, r.reference_start))
    source = root / "source.bam"
    with pysam.AlignmentFile(str(source), "wb", header=header) as stream:
        for read in records:
            stream.write(read)
    for cutoff, excluded, expected in [
        (0, False, 16),
        (10, False, 10),
        (30, False, 6),
        (30, True, 4),
    ]:
        output = root / f"q{cutoff}-{excluded}.bam"
        result = filter_bam(
            source,
            output,
            layout="PE",
            mapq=cutoff,
            exclude_duplicates=excluded,
            organelles=set() if cutoff == 0 else {"plastid"},
        )
        assert result["retained_read_ends"] == expected
        if cutoff == 0:
            assert output.read_bytes() == source.read_bytes()
    bed = root / "peaks.bed"
    bed.write_text("chr1\t100\t150\n")
    value = assess(
        root / "q30-True.bam",
        bed,
        bed,
        layout="PE",
        raw_templates=8,
        organelles={"plastid"},
        temporary=root,
    )
    assert value["metrics"]["fragments_FRiP_own"]["value"] == 0.5
    assert value["metrics"]["read_ends_FRiP_own"]["value"] == 0.25
    assert value["fixed_counts"] == [1]
    truth = alignment_truth(source, source, root)
    assert truth["truth_alignment_precision"]["value"] == 1.0
    assert truth["truth_alignment_recall"]["value"] == 1.0
    other = root / "other-peaks.bed"
    other.write_text("chr1\t800\t850\n")
    assert peak_truth(bed, other)["truth_region_recall"]["value"] == 0
    record = {
        "status": "COMPLETE",
        "libraries": {"a": {"metrics": {}}, "b": {"metrics": {}}},
        "peaks": {"a": str(bed), "b": str(other)},
        "bams": {"a": str(root / "q30-True.bam"), "b": str(root / "q30-True.bam")},
    }
    declared = {
        "synthetic": False,
        "assay": "bulk-ATAC",
        "source": {"replicate_pairs": [["a", "b"]]},
        "reference": {"mitochondrial": [], "plastid": ["plastid"]},
        "libraries": [{"id": name, "layout": "PE", "raw_templates": 8} for name in ("a", "b")],
    }
    # Synthetic known-answer BAMs exercise the explicit-pair arithmetic only.
    replicate_evaluation(declared, [record], root)
    for library in record["libraries"].values():
        assert library["metrics"]["replicate_peak_jaccard"]["value"] == 0
        assert library["metrics"]["heldout_fragments_FRiP"]["value"] == 0.5
    declared["source"] = {"replicate_pairs": [["a", "unknown"]]}
    rejects(lambda: replicate_evaluation(declared, [record], root))
    for invalid in ([["a", "b"], ["b", "a"]], [["a", ["b"]]], "a,b"):
        declared["source"] = {"replicate_pairs": invalid}
        rejects(lambda: replicate_evaluation(declared, [record], root))
    bed.write_text("")
    empty = assess(
        root / "q30-True.bam",
        bed,
        bed,
        layout="PE",
        raw_templates=8,
        organelles={"plastid"},
        temporary=root,
    )
    assert empty["metrics"]["fragments_FRiP_own"]["value"] == 0
    assert empty["metrics"]["peak_jaccard"]["value"] is None
    assert alignment_identity(source, root) == alignment_identity(root / "q0-False.bam", root)
    tss_header = pysam.AlignmentHeader.from_dict({"SQ": [{"SN": "chr1", "LN": 5000}]})
    tss_bam = root / "tss.bam"
    with pysam.AlignmentFile(str(tss_bam), "wb", header=tss_header) as stream:
        for name, start in [("flank", 296), ("center", 2296)]:
            for mate in range(2):
                read = pysam.AlignedSegment(tss_header)
                read.query_name = name
                read.flag = 99 if mate == 0 else 147
                read.reference_id = 0
                read.reference_start = start + mate * 50
                read.cigarstring = "50M"
                read.query_sequence = "A" * 50
                stream.write(read)
    tss = root / "tss.tsv"
    tss.write_text("chr1\t2300\t+\nchr1\t100\t+\n")
    score = tss_enrichment(tss_bam, tss, {"chr1": 5000}, root)
    assert score["profile"][2000] == 1 and sum(score["profile"][:100]) == 2
    assert score["metric"]["value"] == 100.0 and score["edge_excluded_TSS"] == 1
    tss.write_text("chr1\t2300\t-\n")
    reverse = tss_enrichment(tss_bam, tss, {"chr1": 5000}, root)
    assert reverse["metric"]["value"] == 100.0
    assert reverse["profile"] == score["profile"][::-1]
    broken = root / "broken.bam"
    broken.write_bytes(b"not a BAM")
    rejects(
        lambda: filter_bam(
            broken,
            root / "bad.bam",
            layout="SE",
            mapq=0,
            exclude_duplicates=False,
            organelles=set(),
        )
    )


def read_checks(root: Path) -> None:
    reads = b"".join(f"@r{i}\nACGT\n+\nIIII\n".encode() for i in range(8))
    one, two = root / "one.fastq.gz", root / "two.fastq.gz"
    one.write_bytes(gzip.compress(reads, mtime=0))
    two.write_bytes(one.read_bytes())
    small1, small2 = root / "small1.gz", root / "small2.gz"
    big1, big2 = root / "big1.gz", root / "big2.gz"
    subset(one, two, small1, small2, templates=2, seed=60141, expected=8)
    subset(one, two, big1, big2, templates=4, seed=60141, expected=8)
    assert {x[0] for x in paired(small1, small2)} < {x[0] for x in paired(big1, big2)}
    assert all(a == b for _, a, b in paired(big1, big2))
    two.write_bytes(gzip.compress(b"", mtime=0))
    rejects(lambda: list(paired(one, two)))
    one.write_bytes(gzip.compress(b"@broken\nACGT\n+\nI\n", mtime=0))
    rejects(lambda: list(paired(one, two)))


def main() -> None:
    assert intersection([("a", 1, 5), ("a", 3, 8)], [("a", 4, 10)]) == 4
    assert intersection([("a", 0, 10)], [("b", 0, 10)]) == 0
    assert Regions([("a", 100, 200)]).hits("a", 200, 201) == []
    assert Regions([("a", 100, 200), ("a", 150, 160)]).hits("a", 155, 156) == [0, 1]
    assert ranks([1.0, 1.0, 3.0]) == [0.5, 0.5, 2.0]
    assert correlation([1.0, 2.0, 3.0], [3.0, 2.0, 1.0], rank=True) == -1
    assert correlation([1.0, 1.0], [1.0, 2.0]) is None
    assert correlation([], []) is None
    assert tn5_cuts(100, 200) == (104, 194)
    rejects(lambda: tn5_cuts(100, 105))
    rejects(lambda: identifier("../escape"))
    rejects(lambda: public_url("https://user:password@example.org/data"))
    rejects(lambda: public_url("https://example.org/data?token=private"))
    empty = {
        "fixed_regions": [],
        "fixed_counts": [],
        "peak_regions": [],
        "peak_scores": [],
    }
    assert compare(empty, empty)["fixed_count_spearman"]["value"] is None
    with tempfile.TemporaryDirectory() as tmp:
        bam_checks(Path(tmp))
        read_checks(Path(tmp))
        path = Path(tmp) / "result.json"
        path.write_text(json.dumps(compare(empty, empty), allow_nan=False))
        assert json.loads(path.read_text())["unmatched_current_peaks"]["value"] == 0
    print("PASS: independent interval/rank/undefined-value/cut-coordinate and identifier tests")


if __name__ == "__main__":
    main()
