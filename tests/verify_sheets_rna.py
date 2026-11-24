"""RNA-seq sheet building, offline: classes, assay flags, read files, strandedness, duplicates,
writers and an end-to-end build from recorded ENA responses (tests/fixtures/sheets-rna)."""

from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
from collections.abc import Iterator
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import yaml
from helpers import rejects

from genesis_curation.sheets import classify, files, rna, species, writers
from genesis_curation.sheets.archives import Archive, Sample
from genesis_curation.sheets.measure import duplicates, strand
from genesis_curation.sheets.species import DATA
from genesis_curation.sheets.tables import load_runs_qc, load_sheet

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sheets-rna"
AT = species.load("Arabidopsis_thaliana")
SB = species.load("Sorghum_bicolor")


def run(**values: str) -> dict[str, str]:
    base = {
        "run_accession": "SRR1000001",
        "study_accession": "PRJNA1",
        "sample_accession": "SAMN1",
        "library_source": "TRANSCRIPTOMIC",
        "library_selection": "cDNA",
        "library_layout": "SINGLE",
        "instrument_platform": "ILLUMINA",
        "read_count": "20000000",
        "experiment_title": "",
        "sample_title": "",
        "fastq_ftp": "",
        "fastq_bytes": "",
    }
    return base | values


def genotype(entry: species.Species, value: str) -> str:
    sample = Sample("SAMN1", attributes={"genotype": value})
    return classify.classify_sample(entry, sample, run()).genotype


def check_species() -> None:
    assert AT.references.star_index == (
        "Athaliana_447_TAIR10.Athaliana_447_Araport11.gene_exons.gff3.star"
    )
    assert SB.references.organelle_contigs == "Pt,Mt" and SB.taxon == 4558
    rejects(lambda: species.load("Zea_mays_unknown"), "no species entry")
    print("PASS species table")


def check_classes() -> None:
    assert genotype(AT, "Col-0") == "reference (Col-0)"
    assert genotype(AT, "Columbia_0") == "reference (Col-0)"
    assert genotype(AT, "abi3-1") == "mutant / transgenic"
    assert genotype(AT, "phl1") == "mutant / transgenic"  # bare gene name
    assert genotype(SB, "Tx430") == "other accession / cultivar"  # same shape, a cultivar
    assert genotype(SB, "BTx623") == "reference (BTx623)"
    assert genotype(SB, "BTx623 bmr6") == "mutant / transgenic"
    assert genotype(AT, "wild type") == "wild type, accession not stated"
    sample = Sample("SAMN1", attributes={"treatment": "mock", "tissue": "rosette leaves"})
    value = classify.classify_sample(AT, sample, run())
    assert (value.treatment, value.tissue) == ("control / untreated", "leaf"), value
    sample = Sample("SAMN1", attributes={"treatment": "heat stress 2 h"})
    assert classify.classify_sample(AT, sample, run()).treatment == "treated"
    assert classify.tissue_label("whole plant") == "whole-plant"
    # a growth protocol or stage can state the treatment the treatment field denies
    deficit = {"treatment": "No treatment", "growth_protocol": "Phosphorous deficit"}
    assert (
        classify.classify_sample(SB, Sample("S", attributes=deficit), run()).treatment == "treated"
    )
    routine = {"treatment": "none", "growth_protocol": "16 h light / 8 h dark, 22 C"}
    assert (
        classify.classify_sample(AT, Sample("S", attributes=routine), run()).treatment != "treated"
    )
    mock = {"growth_protocol": "inoculated with 2ul PBSx1 close to the root tip"}
    assert classify.classify_sample(AT, Sample("S", attributes=mock), run()).treatment != "treated"
    for stage, expected in (
        ("4 days after aphid infestation", True),
        ("0 days after aphid infestation", False),
    ):
        value = classify.classify_sample(SB, Sample("S", attributes={"dev_stage": stage}), run())
        assert (value.treatment == "treated") is expected, stage
    assert genotype(SB, "Tx623*SV") == "other accession / cultivar"
    bare = Sample("S", attributes={"cultivar": "BTx623", "genotype": "MT"})
    assert classify.classify_sample(SB, bare, run()).genotype == "mutant / transgenic"
    print("PASS genotype, treatment and tissue classes")


def check_assays() -> None:
    for title, label in (
        ("RNAseq_Col0_Polysome", "polysome"),  # underscore before the term
        ("RNA-Seq of Arabidopsis thaliana POT1a IP: cell culture", "IP"),
        ("CK-S IP sequencing data replicate 1", "IP"),
        ("m6A IP rep1", "m6A IP"),
        ("QuantSeq for Arabidopsis thaliana", "3' end"),
        ("Sorghum Root smar2C2", "TSS"),
        ("scRNA-seq of cotyledon", "single-cell"),
        ("TRAP_seq in 35S_LD", "translatome"),
        ("icSHAPE library", "RNA structure"),
    ):
        flag = classify.assay_flag(run(experiment_title=title))
        assert flag == f"not standard RNA-seq ({label})", (title, flag)
    for title in ("leaf shape mutants are not here", "Col-0 rosette, ripening", "trapped"):
        assert classify.assay_flag(run(experiment_title=title)) == "", title
    assert classify.assay_flag(run(library_selection="CAGE")).endswith("(selection CAGE)")
    reference = classify.StudyTitles(AT, []).wild_type
    assert classify.transgene_flag(run(sample_title="35S:SUC2_OE line"), reference)
    study_level = run(experiment_title="RNAseq of rog mutants", sample_title="Col0_cont-rep3")
    assert classify.transgene_flag(study_level, reference) == ""
    assert classify.transgene_flag(study_level | {"sample_title": "rep3"}, reference)
    # Inducible and tagged lines (2026-11-23 gold review).
    for title in (
        "seedlings of VP16-GR (DEX)",
        "pER8-LEC2-72h-R1",
        "VPS34 DN line2_rep3",
        "ECT2 HyperTRIBE FLAG-ADAR",
        "tps1-2 GVG::TPS1",
    ):
        assert classify.transgene_flag(run(sample_title=title), reference), title
    # The line is often named only in the sample alias.
    for alias in (
        "bon123pad4-1_MOCK1",
        "OE-E2Fa-DPa-3",
        "brx_mutant",
        "DN2-4 VPS34 DN",
        "cat2 gsnor1-3 biological replicate 4",
        "eno2-1",
        "WT/p35S::CUC2:GR DEX",
    ):
        assert classify.alias_flag(run(sample_alias=alias), reference), alias
    for alias in (
        "GSM8014186",
        "SAMD01597362",
        "E-MTAB-7203:L5 23 set_A",
        "Col-0_mock_1",
        "WT_KO_line_pool",
        "control-1_S11",
        "GR_leaf_rep1",
        "ENO2-1",
        "Pb0-1",
        "NP3-2",
        "rep1-2",
        "flg22-2",
        "Irradiated or unirradiated",
        "FN.pp5-1",
    ):
        assert classify.alias_flag(run(sample_alias=alias), reference) == "", alias
    for title in ("RNA-seq of Col-0 (control for 10X tpp-KO)", "chromatin interaction network"):
        assert classify.assay_flag(run(experiment_title=title)) == "", title
    assert classify.assay_flag(run(sample_title="chromatin-bound RNA")).endswith("fraction)")
    assert classify.assay_flag(run(sample_title="chromatin rep2 RNA-seq")).endswith("fraction)")
    assert classify.usable(run(instrument_platform="OXFORD_NANOPORE")).startswith("not short")
    assert classify.usable(run(library_selection="size fractionation")).startswith("small RNA")

    study = [
        run(experiment_title="x", sample_title="WT_mock_1"),
        run(experiment_title="x", sample_title="WT_heat_1"),
        run(experiment_title="x", sample_title="pif4_mock_1"),
    ]
    rule = classify.StudyTitles(AT, study)
    assert rule.flag(study[0]) == ""
    assert rule.flag(study[1]) == ""  # issue #13: treatment is recorded, not a reason
    plain = classify.SampleClass("reference (Col-0)", "not stated", "leaf")
    assert rule.record(plain, study[1]).treatment_text == "title: heat"
    assert rule.record(plain, study[0]) == plain
    cases = classify.StudyTitles(
        AT, [run(sample_title="Col-0,control,rep1"), run(sample_title="Col-0,case,rep1")]
    )
    assert cases.record(plain, run(sample_title="Col-0,case,rep1")).treatment == "treated"
    hours = classify.StudyTitles(
        AT, [run(sample_title="seedling_0h_rep1"), run(sample_title="seedling_12h_rep1")]
    )
    assert hours.treatment(run(sample_title="seedling_12h_rep1")) == "title: 12h"
    assert hours.treatment(run(sample_title="seedling_0h_rep1")) == ""
    assert "no wild-type marker" in rule.flag(study[2])
    # issue #13: a treatment stated only in the study text comes from data/treatments.yaml
    treated = run(run_accession="DRR737566", study_accession="PRJDB36269")
    assert "nitrogen deprivation" in classify.curated_treatment("Oryza_sativa", treated)
    assert classify.curated_treatment("Oryza_sativa", run(run_accession="DRR1")) == ""
    for folder, entries in yaml.safe_load((DATA / "treatments.yaml").read_text()).items():
        assert all(e.get("why") and e.get("treatment") for e in entries), folder
    print("PASS assay flags (lookarounds, not \\b), transgene and within-study title rules")


def served(name: str, layout: str, *mates: tuple[str, int]) -> dict[str, str]:
    urls = ";".join(f"ftp.sra.ebi.ac.uk/vol1/fastq/X/{name}/{name}{m}.fastq.gz" for m, _ in mates)
    sizes = ";".join(str(size) for _, size in mates)
    return run(run_accession=name, library_layout=layout, fastq_ftp=urls, fastq_bytes=sizes)


def check_files() -> None:
    one = files.resolve(served("SRR20079009", "PAIRED", ("", 493170989)))
    assert one.reason == "paired run served as one file", one
    pair = files.resolve(served("SRR2", "SINGLE", ("_1", 9_000_000), ("_2", 9_000_000)))
    assert pair.layout == "paired" and pair.read2.endswith("SRR2_2.fastq.gz"), pair
    three = served("SRR3", "PAIRED", ("", 5_000_000), ("_1", 9_000_000), ("_2", 9_000_000))
    assert files.resolve(three).layout == "paired"
    single = files.resolve(served("SRR4", "SINGLE", ("", 9_000_000)))
    assert single.layout == "single" and single.read1.startswith("https://ftp.sra.ebi.ac.uk/")
    assert files.resolve(served("SRR5", "SINGLE", ("", 999_999))).reason == "FASTQ under 1 MB"
    assert files.resolve(run()).reason == "no read files"
    print("PASS read files from ENA's served list")


def fastq(n: int, length: int = 50) -> bytes:
    return b"".join(b"@r%d\n%s\n+\n%s\n" % (i, b"A" * length, b"I" * length) for i in range(n))


def chunks_of(data: bytes) -> Iterator[bytes]:
    for start in range(0, len(data), 1000):
        yield data[start : start + 1000]


def check_strand() -> None:
    assert strand.call(19_000, 1_000) == "forward"
    assert strand.call(100, 2_900) == "reverse"
    assert strand.call(1_500, 1_500) == "unstranded"
    assert strand.call(2_100, 900) == "strandedness unclear"
    assert strand.call(1_000, 900) == "too few reads on genes"
    item = strand.result("SRR1", 20_000, 9_000, 1_000)
    assert (item.forward_fraction, item.gene_fraction) == ("0.900", "0.500"), item

    # Two gzip members, as some uploads are; records stream across chunk boundaries.
    data = gzip.compress(fastq(30)) + gzip.compress(fastq(30))
    assert len(list(strand.fastq_records(chunks_of(data)))) == 60
    for broken, message in (
        (fastq(5) + b"@r\nAAAA\n+\nII\n", "invalid"),
        (fastq(5) + b"@r\nAA\0A\n+\nIIII\n", "NUL"),
        (fastq(5) + b"@r\nAAAA\n", "truncated"),
    ):
        rejects(lambda b=broken: list(strand.fastq_records(chunks_of(gzip.compress(b)))), message)

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "SRR1.fq"
        calls: list[str] = []

        def flaky(url: str) -> Iterator[bytes]:
            calls.append(url)
            body = fastq(10) if len(calls) > 1 else fastq(3) + b"@r\nAA\0A\n+\nIIII\n"
            return chunks_of(gzip.compress(body))

        assert strand.sample_reads("SRR1", "u", target, reads=8, chunks=flaky, pause=0) == 8
        assert len(calls) == 2, calls  # the NUL-corrupted first download was retried
        lines = target.read_text().splitlines()
        assert lines[0] == "@SRR1:r0" and len(lines) == 32
        assert strand.sample_reads("SRR1", "u", target, chunks=flaky) == 8  # resumed

        def broken(url: str) -> Iterator[bytes]:
            return chunks_of(gzip.compress(fastq(3) + b"@r\nAA\0A\n+\nIIII\n"))

        short = Path(tmp) / "SRR2.fq"
        # Three valid reads before a fault on every attempt: too few to keep, so it failed.
        assert strand.sample_reads("SRR2", "u", short, reads=8, chunks=broken, pause=0) == 0
        assert not short.exists()

        core = Path(tmp) / "core"
        core.write_text("SRR1:r0\tAssigned\t1\tg\nSRR1:r1\tUnassigned_NoFeatures\t0\tNA\n")
        assert strand.assigned(core) == {"SRR1": 1}

        store = Path(tmp) / "calls.tsv"
        header = "run\tstrandedness\treads_sampled\tforward_reads\treverse_reads\t"
        store.write_text(
            header + "forward_fraction\tgene_fraction\nSRR1\treverse\t20000\t1\t9\t0.1\t0.5\n"
        )
        assert strand.load_calls(store)["SRR1"].reverse_reads == 9
    rejects(
        lambda: strand.Tools.find(star="/missing/STAR", featurecounts="/x", gffread="/x"), "STAR"
    )
    print("PASS strandedness calls, FASTQ validation, retried downloads and resumable calls")


def check_duplicates() -> None:
    reads = {"SRR1": b"A", "SRR2": b"C", "SRR3": b"A", "SRR4": b"G"}

    def head(url: str) -> bytes:
        return gzip.compress(b"@x\n%s\n+\nI\n" % (reads[url] * 20) * 60)

    counts = {"SRR1": "12345678", "SRR2": "12345678", "SRR3": "12345678", "SRR4": "2000000"}
    assert duplicates.candidate_groups(counts) == [["SRR1", "SRR2", "SRR3"]]
    with tempfile.TemporaryDirectory() as tmp:
        found = duplicates.find(counts, {r: r for r in counts}, Path(tmp), fetch=head)
        assert found == {"SRR3": "SRR1"}, found
        assert set(json.loads((Path(tmp) / "first-reads.json").read_text())) == {
            "SRR1",
            "SRR2",
            "SRR3",
        }
    print("PASS duplicate submissions by equal read counts and first reads")


def check_writers() -> None:
    source = json.loads((writers.SCHEMAS / "SOURCE.json").read_text())
    for name, digest in source["sha256"].items():
        assert hashlib.sha256((writers.SCHEMAS / name).read_bytes()).hexdigest() == digest, name
    assert "rna-seq.samples.schema.json" in source["sha256"]
    assert writers.header("rna-seq") == [
        "sample_id",
        "species",
        "read1_url",
        "read2_url",
        "strandedness",
        "reference_fasta",
        "reference_annotation",
        "organelle_contigs",
    ]
    row = {
        "sample_id": "P_leaf_S_SRR1",
        "species": "Arabidopsis_thaliana",
        "read1_url": "https://x.org/SRR1.fastq.gz",
        "read2_url": "-",
        "strandedness": "reverse",
        "reference_fasta": "genome.fa.gz",
        "reference_annotation": "genes.gff3.gz",
        "organelle_contigs": "ChrC,ChrM",
    }
    writers.validate("rna-seq", [row])
    rejects(lambda: writers.validate("rna-seq", [row | {"strandedness": "unclear"}]), "schema")
    rejects(lambda: writers.validate("rna-seq", [row, row]), "repeats")
    with tempfile.TemporaryDirectory() as tmp:
        bad = [{"run": "SRR1", "in_sheet": "no", "reason_left_out": ""}]
        rejects(lambda: writers.write_runs_qc(Path(tmp) / "qc.tsv", bad), "in_sheet=no")
    print("PASS vendored schemas match SOURCE.json; header, schema and runs-qc invariants")


def fixture_fetch(url: str, data: bytes | None) -> str:
    if url.endswith("/search"):
        assert data is not None and b"tax_eq%283702%29" in data, data
        return (FIXTURES / "ena-runs.tsv").read_text()
    if "/browser/api/xml/" in url:
        return (FIXTURES / "ena-samples.xml").read_text()
    raise AssertionError(url)


def check_archive_retries() -> None:
    attempts: list[int] = []

    def throttled(url: str, data: bytes | None) -> str:
        attempts.append(1)
        if len(attempts) < 3:
            raise HTTPError(url, 429, "slow down", Message(), None)
        return "a\tb\n1\t2\n"

    with tempfile.TemporaryDirectory() as tmp:
        waits: list[float] = []
        archive = Archive(Path(tmp), fetcher=throttled, sleep=waits.append)
        assert archive.request("https://x/search", {"q": "1"}) == "a\tb\n1\t2\n"
        assert waits == [2.0, 4.0] and archive.requests == 1
        assert Archive(Path(tmp), offline=True).request("https://x/search", {"q": "1"})
        rejects(lambda: Archive(Path(tmp), offline=True).request("https://x/other"), "offline")
    print("PASS ENA requests back off on 429, are cached by request and work offline")

    def unknown_sample(url: str, data: bytes | None) -> str:
        names = url.rsplit("/", 1)[-1].split(",")
        if "SAMN2" in names:
            raise HTTPError(url, 404, "not found", Message(), None)
        return (
            "<SAMPLE_SET>"
            + "".join(f'<SAMPLE accession="{n}"><TITLE>t</TITLE></SAMPLE>' for n in names)
            + "</SAMPLE_SET>"
        )

    with tempfile.TemporaryDirectory() as tmp:
        found = Archive(Path(tmp), fetcher=unknown_sample).samples(["SAMN1", "SAMN2", "SAMN3"])
        assert not found["SAMN1"].missing and not found["SAMN3"].missing, found
        assert found["SAMN2"].missing, found
    print("PASS a sample ENA does not know is recorded missing without failing its batch")


def check_build() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reused = root / "calls.tsv"
        reused.write_text(
            "run\tstrandedness\treads_sampled\tforward_fraction\tgene_fraction\n"
            "DRR031752\tunstranded\t20000\t0.499\t0.975\n"
            "SRR8294703\tforward\t20000\t0.948\t0.9\n"
            "SRR29139257\treverse\t20000\t0.030\t0.9\n"
            "SRR17332297\ttoo_few_reads\t20000\t0.5\t0.01\n"
        )
        archive = Archive(root / "cache", fetcher=fixture_fetch)
        options = rna.Options(
            cache=root / "cache", measure=False, strandedness_from=reused, probe_duplicates=False
        )
        summary = rna.build(AT, archive, root / "out", options, log=lambda _: None)
        assert summary["runs"] == 12 and archive.requests == 2, summary
        sheet = load_sheet(root / "out" / "rna-seq.tsv", "rna-seq")
        qc = load_runs_qc(root / "out" / "rna-seq.runs-qc.tsv")
        reasons = {r: row["reason_left_out"] for r, row in qc.items()}
        assert set(sheet.by_run) == {"DRR031752", "SRR8294703", "SRR29139257"}, reasons
        assert sheet.fields("SRR29139257")["strandedness"] == "reverse"
        assert sheet.fields("DRR031752")["layout"] == "paired"
        expected = {
            "SRR20079009": "paired run served as one file",
            "SRR9618710": "not standard RNA-seq (single-cell)",
            "SRR12272506": "not standard RNA-seq (3' end)",
            "SRR26414733": "not standard RNA-seq (IP)",
            "SRR28843394": "not standard RNA-seq (polysome)",
            "SRR10742663": "not standard RNA-seq (IP)",
            "SRR17332297": "too few reads on genes",
            "DRR031755": "strandedness not measured",
        }
        for accession, reason in expected.items():
            assert reasons[accession] == reason, (accession, reasons[accession])
        assert all(reasons[r] for r in qc if r not in sheet.by_run)
        row = sheet.by_run["DRR031752"]
        assert row["sample_id"].startswith("PRJDB3784_shoot_SAMD") and row["reference_fasta"] == (
            "Athaliana_447_TAIR10.fa.gz"
        )
        # A rebuild reads the cache only.
        offline = Archive(root / "cache", offline=True)
        rna.build(AT, offline, root / "again", options, log=lambda _: None)
        assert (root / "again" / "rna-seq.tsv").read_text() == (
            root / "out" / "rna-seq.tsv"
        ).read_text()
    print("PASS offline build from recorded ENA responses with reused strandedness")


def main() -> None:
    check_species()
    check_classes()
    check_assays()
    check_files()
    check_strand()
    check_duplicates()
    check_writers()
    check_archive_retries()
    check_build()


if __name__ == "__main__":
    main()
