"""ATAC-seq sheet building, offline: assay and treatment rules, GSA files, read metrics, a
mapped run with a stand-in chromap, and an end-to-end Sorghum build from recorded ENA and GSA
responses (tests/fixtures/sheets-atac)."""

from __future__ import annotations

import gzip
import json
import stat
import sys
import tempfile
from collections.abc import Iterator
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

from helpers import rejects

from genesis_curation.sheets import atac, classify, files, species, writers
from genesis_curation.sheets.archives import Archive, Sample
from genesis_curation.sheets.measure import atac as checks
from genesis_curation.sheets.tables import load_runs_qc, load_sheet

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sheets-atac"
AT = species.load("Arabidopsis_thaliana")
SB = species.load("Sorghum_bicolor")


def quiet(message: str) -> None:
    del message


def run(**values: str) -> dict[str, str]:
    base = {
        "run_accession": "SRR1000001",
        "study_accession": "PRJNA1",
        "sample_accession": "SAMN1",
        "tax_id": "3702",
        "library_source": "GENOMIC",
        "library_selection": "other",
        "library_layout": "PAIRED",
        "instrument_platform": "ILLUMINA",
        "experiment_title": "",
        "sample_title": "",
        "study_title": "",
        "fastq_ftp": "",
        "fastq_bytes": "",
    }
    return base | values


def check_assays() -> None:
    for title, term in (
        ("Col0_WTgDNA", "gDNA"),  # glued to the sample name
        ("ATAC input1", "input"),  # digits after the term
        ("leaf_CUT&Tag_H3K27me3", "CUT&Tag"),
        ("sci-ATAC-seq plate 2", "sci-ATAC"),
        ("Tn5 only control", "Tn5 only"),
        ("MNase_rep1", "MNase"),
    ):
        flag = classify.atac_assay_flag(run(sample_title=title))
        assert flag == f"not bulk ATAC (title: {term})", (title, flag)
    for title in ("ATAC-seq of Col-0 leaves rep1", "inputs of nitrogen are not here", "chipped"):
        assert classify.atac_assay_flag(run(sample_title=title)) == "", title
    assert classify.atac_assay_flag(run(library_source="GENOMIC SINGLE CELL")).endswith("CELL)")
    assert classify.atac_assay_flag(run(library_selection="MNase")) == (
        "not bulk ATAC (selection MNase)"
    )
    assert classify.atac_assay_flag(run(study_title="Capture ATAC of maize")).endswith(
        "capture ATAC)"
    )
    assert classify.atac_assay_flag(run(), "Hi-C library") == "not bulk ATAC (title: Hi-C)"

    def treatment(text: str) -> str:
        sample = Sample("SAMN1", attributes={"treatment": text})
        value = classify.classify_sample(AT, sample, run(), treated=classify.ATAC_TREATED)
        return value.treatment

    for text in ("CIM 4 days", "shoot regeneration", "Pst DC3000 24 hpi", "2,4-D"):
        assert treatment(text) == "treated", text
        assert classify.classify_sample(
            AT, Sample("S", attributes={"treatment": text}), run()
        ).treatment in ("other condition", "treated")
    assert treatment("mock") == "control / untreated"
    background = Sample("S", attributes={"genetic background": "Ler"})
    value = classify.classify_sample(AT, background, run(), more_genotype=atac.GENOTYPE_FIELDS)
    assert value.genotype == "other accession / cultivar", value
    assert classify.classify_sample(AT, background, run()).genotype == "not stated"
    print("PASS ATAC assay flags, treatments (CIM/SIM, regeneration, hpi) and genotype fields")


def check_files() -> None:
    folder = "download.cncb.ac.cn/gsa3/CRA002789/CRR145290/"
    gsa = run(
        run_accession="CRR145290",
        fastq_ftp=f"{folder}CRR145290_f1.fastq.gz;{folder}CRR145290_r2.fastq.gz",
        fastq_bytes="4264738330;4096632824",
    )
    reads = files.resolve(gsa)
    assert reads.layout == "paired" and reads.read2 == f"https://{folder}CRR145290_r2.fastq.gz"
    fq = gsa | {"fastq_ftp": gsa["fastq_ftp"].replace("fastq.gz", "fq.gz")}
    assert files.resolve(fq).read1.endswith("CRR145290_f1.fq.gz")
    other = gsa | {"fastq_ftp": f"{folder}XCRR145290_1.fastq.gz;{folder}CRR145290.fastq.gz"}
    assert files.resolve(other | {"library_layout": "SINGLE"}).layout == "single"
    one_file = "ftp.sra.ebi.ac.uk/x/SRR1000001/SRR1000001.fastq.gz"
    item = atac.Candidate(run(fastq_ftp=one_file, library_layout="SINGLE"), "ena")
    assert atac.basic_reason(AT, item) == "single-end"
    assert atac.basic_reason(AT, atac.Candidate(run(tax_id="3701"), "tab")) == (
        "other species (taxon 3701)"
    )
    listing = (FIXTURES / "gsa-listing.html").read_text()

    def fetch(url: str, data: bytes | None) -> str:
        if "/gsa3/" in url:
            return listing
        raise ValueError("HTTP 404")

    with tempfile.TemporaryDirectory() as tmp:
        archive = Archive(Path(tmp), fetcher=fetch, attempts=1)
        found = archive.gsa_files("CRA002789", "CRR145290")
        assert [size for _, size in found] == [4264738330, 4096632824], found
        assert found[0][0] == f"{folder}CRR145290_f1.fastq.gz"
        again = Archive(Path(tmp), offline=True).gsa_files("CRA002789", "CRR145290")
        assert again == found  # kept in gsa.json
        rejects(lambda: Archive(Path(tmp), offline=True).gsa_files("CRA1", "CRR1"), "offline")

        def busy(url: str, data: bytes | None) -> str:
            if "/gsa2/" in url:
                raise HTTPError(url, 503, "busy", Message(), None)
            raise ValueError("HTTP 404")

        flaky = Archive(Path(tmp), fetcher=busy, sleep=lambda _: None)
        assert flaky.gsa_files("CRA2", "CRR2") == []
        assert "CRR2" not in json.loads((Path(tmp) / "gsa.json").read_text())  # asked again
    print("PASS GSA mates (_f1/_r2) and download-server listings; PE only")


def genes_bed(path: Path) -> Path:
    # Two genes on Chr1: + strand TSS at 10,000 and - strand TSS at 29,999 (end 30,000).
    path.write_text("Chr1\t10000\t15000\tg1\t0\t+\nChr1\t25000\t30000\tg2\t0\t-\n")
    return path


def fragments(center: int, flank: int) -> Iterator[str]:
    """`center` fragments with both cut sites at the + TSS, `flank` ones 1,950 bp away, two
    organelle fragments and one on a contig without genes."""
    for _ in range(center):
        yield "Chr1\t9996\t10005\tx\t1\n"  # cuts at 10,000 and 10,000
    for _ in range(flank):
        yield "Chr1\t11946\t11955\tx\t1\n"  # cuts at +1,950
    yield "ChrC\t100\t300\tx\t1\n"
    yield "ChrM\t100\t300\tx\t1\n"
    yield "scaffold_9\t100\t400\tx\t1\n"


def check_metrics() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        genes = checks.Genes(genes_bed(Path(tmp) / "genes.bed"))
        assert genes.genic("Chr1", 12_000) and not genes.genic("Chr1", 20_000)
        item = checks.fragment_metrics("R1", 100, fragments(20, 10), genes, {"ChrC", "ChrM"})
        assert item.mapped_fragments == 33 and item.mapped_fraction == "0.330", item
        assert item.organelle_fraction == "0.061", item
        # 40 cuts in the central bin over 20 cuts spread across 200 flank bins: 0.4 / 0.1.
        assert item.tss_enrichment == "4.00" and item.tss_peak == "4.00", item
        assert item.nfr_fraction == "0.968" and item.median_fragment == "9", item
        assert item.promoter_fraction == "0.6452", item  # 40 of 62 nuclear cuts
        flat = checks.fragment_metrics("R2", 100, fragments(0, 10), genes, set())
        assert flat.tss_enrichment == "0.00", flat
        empty = checks.fragment_metrics("R3", 100, iter(()), genes, set())
        assert empty.tss_enrichment == "NA" and empty.organelle_fraction == "NA"

        one, two = Path(tmp) / "r1.fq", Path(tmp) / "r2.fq"
        one.write_text("@a\nAC\n+\nII\n" * 5)
        two.write_text("@a\nAC\n+\nII\n" * 3)
        assert checks.pair_files(one, two) == 3 and one.read_text().count("@a") == 3
        rejects(lambda: checks.Tools.find("/missing/chromap"), "chromap")
    for metrics, reason in (
        (None, "not measured"),
        (checks.Metrics("R", failed="chromap failed"), "not measured (chromap failed)"),
        (checks.Metrics("R", mapped_fraction="0.01"), "few fragments (mapped fraction 0.01)"),
        (
            checks.Metrics("R", mapped_fraction="0.5", organelle_fraction="0.97"),
            "mostly organelle reads (0.97)",
        ),
        (
            checks.Metrics(
                "R", mapped_fraction="0.5", organelle_fraction="0.1", tss_enrichment="1.0"
            ),
            "no accessibility signal (TSS enrichment 1.0)",
        ),
        (checks.Metrics("R", mapped_fraction="0.5", tss_enrichment="2.3"), ""),
    ):
        assert atac.read_reason(metrics) == reason, (metrics, reason)
    print("PASS read metrics: TSS enrichment, organelle, NFR, promoter share, cut-offs")


def fake_chromap(folder: Path) -> str:
    """A stand-in chromap: index mode writes the index; mapping writes fragments at the TSS."""
    script = folder / "chromap"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "out = args[args.index('-o') + 1]\n"
        "if '-i' in args:\n"
        "    open(out, 'w').write('index')\n"
        "else:\n"
        "    near, far = 'Chr1\\t9996\\t10005\\tx\\t1\\n', 'Chr1\\t11946\\t11955\\tx\\t1\\n'\n"
        "    open(out, 'w').writelines([near] * 300 + [far] * 200)\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


def check_measure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tools = checks.Tools.find(fake_chromap(root))
        fasta = root / "genome.fa.gz"
        fasta.write_bytes(gzip.compress(b">Chr1\nACGT\n"))
        index = checks.chromap_index(fasta, root / "cache" / "genome.index", tools)
        assert index.read_text() == "index"
        calls: list[str] = []

        def sampler(name: str, url: str, target: Path, pairs: int) -> int:
            calls.append(url)
            if "broken" in url:
                return 0
            target.write_text("@r\nACGT\n+\nIIII\n" * pairs)
            return pairs

        runs = {"SRR1": ("u1", "u2"), "SRR2": ("broken1", "broken2")}
        args = (root / "work", fasta, index, genes_bed(root / "genes.bed"), {"ChrC"}, tools)
        found = checks.measure(runs, *args, pairs=checks.MIN_PAIRS, sampler=sampler, log=quiet)
        assert found["SRR1"].mapped_fraction == "0.025" and found["SRR1"].pairs == 20_000
        assert found["SRR2"].failed.startswith("download failed"), found["SRR2"]
        assert not (root / "work" / "runs" / "SRR1").exists()  # reads deleted
        calls.clear()
        again = checks.measure(runs, *args, pairs=checks.MIN_PAIRS, sampler=sampler, log=quiet)
        assert again["SRR1"] == found["SRR1"] and calls == ["broken1", "broken2"], calls
    print("PASS chromap measurement resumes from metrics.tsv and retries failed downloads")


def fixture_fetch(url: str, data: bytes | None) -> str:
    if url.endswith("/search"):
        assert data is not None
        name = "ena-runs.tsv" if b"tax_eq%284558%29" in data else "ena-tab-runs.tsv"
        return (FIXTURES / name).read_text()
    if "/browser/api/xml/" in url:
        return (FIXTURES / "ena-samples.xml").read_text()
    if "download.cncb.ac.cn/gsa3/" in url:
        run_id = url.rstrip("/").rsplit("/", 1)[-1]
        return (FIXTURES / "gsa-listing.html").read_text().replace("CRR145290", run_id)
    raise ValueError(f"HTTP 404 for {url}")


def check_build() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        archive = Archive(root / "cache", fetcher=fixture_fetch, attempts=1)
        options = atac.Options(
            cache=root / "cache",
            measure=False,
            measure_from=FIXTURES / "metrics.tsv",
            extra_runs=FIXTURES / "tab.tsv",
        )
        summary = atac.build(SB, archive, root / "out", options, log=lambda _: None)
        assert summary["runs"] == 39, summary
        assert summary["sources"] == {"ena": 31, "tab": 4, "tab, GSA": 4}, summary
        sheet = load_sheet(root / "out" / "atac-seq.tsv", "atac-seq")
        qc = load_runs_qc(root / "out" / "atac-seq.runs-qc.tsv")
        reasons = {r: row["reason_left_out"] for r, row in qc.items()}
        kept = {"CRR145291", "CRR145292", "CRR145293", "SRR5748780", "SRR5748781"}
        kept |= {"SRR5748782", "SRR8742450", "SRR8742451"}
        assert set(sheet.by_run) == kept, reasons
        expected = {
            "CRR145290": "low FRiP in a full pipeline run (0.066)",
            "SRR8742452": "not bulk ATAC (title: Input)",
            "SRR27500249": "not bulk ATAC (title: sci-ATAC)",
            "SRR27704580": "not bulk ATAC (library_source GENOMIC SINGLE CELL)",
            "SRR13810444": "genotype: other accession / cultivar",
            "SRR37075961": "genotype: not stated",
        }
        for accession, reason in expected.items():
            assert reasons[accession] == reason, (accession, reasons[accession])
        row = sheet.by_run["CRR145291"]
        assert row["read1_url"].endswith("/gsa3/CRA002789/CRR145291/CRR145291_f1.fastq.gz")
        assert row["sample_id"] == "CRA002789_leaf_CRR145291"
        assert row["reference_genes"] == "Sbicolor_730_v5.1_PtMt.genes.bed"
        assert qc["SRR5748780"]["tss_enrichment"] == "2.79"
        writers.validate("atac-seq", sheet.rows)
        # A rebuild reads the cache only.
        offline = Archive(root / "cache", offline=True)
        atac.build(SB, offline, root / "again", options, log=lambda _: None)
        assert (root / "again" / "atac-seq.tsv").read_text() == (
            root / "out" / "atac-seq.tsv"
        ).read_text()
        # Without reused metrics nothing is measured, and every run says why.
        bare = atac.Options(cache=root / "cache", measure=False, extra_runs=FIXTURES / "tab.tsv")
        rejects(lambda: atac.build(SB, offline, root / "bare", bare, log=quiet), "non-empty")
        qc = load_runs_qc(root / "bare" / "atac-seq.runs-qc.tsv")
        assert qc["SRR5748780"]["reason_left_out"] == "not measured"
        rejects(
            lambda: atac.build(
                SB, offline, root / "x", atac.Options(cache=root / "cache"), log=lambda _: None
            ),
            "chromap",
        )
    print("PASS offline Sorghum build: ENA, tab and GSA runs, reused metrics, one reason each")


def main() -> None:
    check_assays()
    check_files()
    check_metrics()
    check_measure()
    check_build()


if __name__ == "__main__":
    main()
