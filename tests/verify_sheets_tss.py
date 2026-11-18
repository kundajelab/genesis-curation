"""TSS-seq sheet building, offline: method and exclusion rules, read structure, the trim sweep
(with a stand-in STAR), trim choice and an end-to-end build from recorded ENA responses
(tests/fixtures/sheets-tss)."""

from __future__ import annotations

import gzip
import sys
import tempfile
from collections import Counter
from pathlib import Path

from helpers import rejects

from genesis_curation.sheets import species, tss, writers
from genesis_curation.sheets.archives import Archive
from genesis_curation.sheets.measure import tss as signal
from genesis_curation.sheets.tables import load_runs_qc, load_sheet

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sheets-tss"
AT = species.load("Arabidopsis_thaliana")


def run(**values: str) -> dict[str, str]:
    base = {
        "run_accession": "SRR1",
        "study_accession": "PRJNA1",
        "secondary_study_accession": "SRP1",
        "sample_accession": "SAMN1",
        "library_source": "TRANSCRIPTOMIC",
        "library_selection": "cDNA",
        "instrument_platform": "ILLUMINA",
        "experiment_title": "",
        "sample_title": "",
        "library_name": "",
        "study_title": "",
    }
    return base | values


def method(**values: str) -> tuple[str, str, str]:
    return tss.method_of(run(**values), AT, {})


def check_methods() -> None:
    assert method(sample_title="Col0_TSS-Seq_rep1")[:2] == ("TSS-seq", "run text")
    assert method(library_name="Cap_trap_random_primer")[0] == "CAGE"
    assert method(sample_title="seed, 26h light, rep1, csRNA-seq")[0] == "csRNA-seq"
    assert method(sample_title="Root smar2C2 rep1")[0] == "Smar2C2"
    assert method(sample_title="leaf RAMPAGE")[0] == "RAMPAGE"
    assert method(sample_title="nAnT-iCAGE leaf")[0] == "nAnT-iCAGE"
    assert method(sample_title="rep1", study_title="TSS-seq of leaves")[:2] == (
        "TSS-seq",
        "study title",
    )
    assert method(sample_title="Im1389", library_selection="CAGE")[:2] == (
        "CAGE",
        "library selection",
    )
    for values, reason in (
        ({"sample_title": "col0_fb_1.nanoPARE"}, "nanoPARE"),
        ({"sample_title": "mRNA NAD_P rep2"}, "NAD-RNA"),
        ({"sample_title": "5'GRO-seq"}, "nascent"),
        ({"library_name": "Cap_less_random_primer"}, "cap-less"),
        ({"experiment_title": "RNA-Seq_WT/mock treatment rep.1"}, "RNA-seq run"),
        ({"sample_title": "rrp4-2, 57h light, rep2, sRNA-seq"}, "small RNA run"),
        (
            {"library_selection": "CAGE", "study_title": "RNA-seq of accessions"},
            "study is RNA-seq",
        ),
        ({"sample_title": "leaf rep1"}, "no TSS method named"),
    ):
        found = method(**values)
        assert found[0] == "" and reason in found[2], (values, found)
    table = {"SRP1": {"method": "nAnT-iCAGE"}}
    assert tss.method_of(run(sample_title="CAGE rep1"), AT, table)[:2] == (
        "nAnT-iCAGE",
        "data/tss.yaml",
    )
    # The GEO suffix "<species> OTHER" and the species name are not the run's own text.
    text = tss.run_text(
        run(
            experiment_title="NextSeq 500 sequencing: GSM1: TSS-seq rep1 Arabidopsis thaliana OTHER"
        ),
        AT,
    )
    assert text == "TSS-seq rep1", text
    assert tss.treatment("Germinating seed, 26h light, rep1") == ""
    assert tss.treatment("WT_cold_rep1").startswith("treatment: cold")
    assert tss.treatment("Blue 30 min | control") == ""
    titles = tss.StudyTitles(AT, ["Col-0 TSS rep1", "abc1 TSS rep1", "x TSS rep1"])
    assert titles.genotype("Col-0 TSS rep1") == ""
    assert "no wild-type marker" in titles.genotype("x TSS rep1")
    assert AT.reference_lines is not None and AT.reference_lines.search("T87 cells")
    assert tss.usable(run(instrument_platform="PACBIO_SMRT")).startswith("not short-read")
    print("PASS methods from run text, study title, selection and data; unsupported methods")


def check_read_structure() -> None:
    assert signal.clip(b"ACGTACGTACGTTCGTATGCCGTCTT") == 12
    assert signal.clip(b"ACGTACGTACGT") == 12
    records = [
        (b"r1", b"GACGT" + b"AGATCGGAAGAGC", b"I" * 18),
        (b"r2", b"GACGT" + b"TCGTATGCCG" + b"AAA", b"I" * 18),
        (b"r3", b"TACGTTCGTATGCCGTCTT", b"I" * 19),
        (b"r4", b"CCCCCCCCCCCCCCCCCC", b"I" * 18),
    ]
    value = signal.profile("SRR1", records)
    assert value.reads == 4 and value.first_g == 0.5
    assert value.adapter == "TCGTATGCCGTCTTCTGCTTG", value.adapter_rates
    assert signal.Profile("SRR1", 100, {"TruSeq": 0.005}).adapter == signal.TRUSEQ
    # A TruSeq dimer read through to the index adapter tail counts for TruSeq (DRR029684).
    dimer = b"TCGGAAGAGCGGTTCAGCAGGAATGCCGAGACCGATCTCGTATGCCG"
    assert signal.first_adapter(dimer) == ("TruSeq", 0) and signal.clip(dimer) == 0
    primer = signal.SMART_PRIMER.replace("T", "A", 1)  # one mismatch is allowed
    read = b"NN" + primer.encode() + b"ACGTACGTACGT" + b"AT" + b"GGGACTG"
    assert [s for _, s, _ in signal.smart_reads([(b"r", read, b"I" * len(read))])] == [b"GGGACTG"]
    assert signal.smart_reads([(b"r", b"ACGT" * 20, b"I" * 80)]) == []
    print("PASS adapter rates, cap G share, adapter clipping and Smar2C2 primer + UMI")


def check_offsets() -> None:
    gff = (
        "##gff-version 3\n"
        "c1\tx\tgene\t100\t500\t.\t+\t.\tID=g1\n"
        "c1\tx\tmRNA\t101\t500\t.\t+\t.\tID=t1;Parent=g1\n"
        "c1\tx\texon\t101\t200\t.\t+\t.\tParent=t1\n"
        "c1\tx\tmRNA\t900\t1000\t.\t-\t.\tID=t2\n"
        "c1\tx\texon\t900\t1000\t.\t-\t.\tParent=t2\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "genes.gff3.gz"
        path.write_bytes(gzip.compress(gff.encode()))
        starts = signal.transcript_starts(path)
    # Genes are not transcripts; a minus-strand transcript starts at its end.
    assert starts == {("c1", "+"): [100], ("c1", "-"): [999]}, starts
    assert signal.five_prime(0, 101, "20M") == ("+", 100)
    assert signal.five_prime(16, 981, "10M100N9M1S") == ("-", 1098)
    assert signal.nearest([100], 102, "+") == 2 and signal.nearest([999], 1001, "-") == -2
    assert signal.nearest([100], 300, "+") is None
    assert signal.offset_mode(Counter({1: 5, -1: 5, 3: 2})) == -1
    assert signal.offset_mode(Counter()) is None

    def stats(trim: int, close: int, mode: int | None) -> signal.TrimStats:
        return signal.TrimStats(trim, 1000, 900, close, 600, mode)

    # An 8 nt UMI (SRP142625): trim 4 reaches mode 0 with more ends near, then jumps.
    modes = {4: 0, 5: -3, 6: -2, 7: -1, 8: 0, 9: 1, 10: 2}
    sweep = {t: stats(t, 400 - 10 * t, mode) for t, mode in modes.items()}
    assert signal.choose_trim(sweep, 0) == 8
    assert signal.choose_trim(sweep, -1) == 7
    sweep = {0: stats(0, 300, 9), 8: stats(8, 400, 1), 9: stats(9, 100, 2)}
    assert signal.choose_trim(sweep, 0) == 8  # no steady mode 0: the most ends near
    sweep[7] = stats(7, 50, 0)
    assert signal.choose_trim(sweep, 0) == 8  # a mode reached only where few ends are near
    assert signal.choose_trim({}, 0) == 0
    print("PASS transcript starts, 5' ends, offsets, offset mode ties and trim choice")


FAKE_STAR = """
import sys
args = sys.argv[1:]
reads = args[args.index("--readFilesIn") + 1]
prefix = args[args.index("--outFileNamePrefix") + 1]
lines = open(reads).read().split("\\n")
with open(prefix + "Aligned.out.sam", "w") as out:
    out.write("@HD\\tVN:1.4\\n")
    for i in range(0, len(lines) - 3, 4):
        name, seq = lines[i][1:], lines[i + 1]
        trim = int(name.split(":")[1])
        # Reads carry a 7 nt linker that maps upstream of the start until it is trimmed.
        fields = [name, "0", "c1", str(101 - 7 + trim), "255", f"{len(seq)}M", "*", "0", "0", seq]
        out.write("\\t".join([*fields, "*"]) + "\\n")
"""


def check_measure() -> None:
    gff = "c1\tx\tmRNA\t101\t500\t.\t+\t.\tID=t1\nc1\tx\texon\t101\t500\t.\t+\t.\tParent=t1\n"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "genes.gff3").write_text(gff)
        star = root / "STAR"
        star.write_text(f"#!{sys.executable}\n{FAKE_STAR}")
        star.chmod(0o755)
        calls: list[str] = []

        def sample(run: str, url: str, target: Path, reads: int) -> int:
            calls.append(run)
            if run == "SRR2":
                return 0
            body = b"@%s.%d\n%s\n+\n%s\n"
            target.write_bytes(
                b"".join(
                    body % (run.encode(), i, b"G" + b"ACGT" * 10, b"I" * 41) for i in range(50)
                )
            )
            return 50

        jobs = [signal.Job("SRR1", ("u1",)), signal.Job("SRR2", ("u2",))]
        tools = signal.Tools(str(star), 2)
        found = signal.measure(
            jobs, root / "work", root, root / "genes.gff3", tools, sample=sample, log=lambda _: None
        )
        value = found["SRR1"]
        assert value.profile.reads == 50 and value.profile.first_g == 1.0
        assert value.sweep[7].offset_mode == 0 and value.sweep[0].offset_mode == -7
        assert signal.choose_trim(value.sweep, 0) == 7 and signal.choose_trim(value.sweep, -1) == 6
        assert found["SRR2"].profile.reads == 0
        measured = tss.measured_signal("CAGE", value)
        assert measured and (measured.trim, measured.offset_mode, measured.near) == (6, "-1", 1.0)
        # A rebuild reads the work folder; only the failed download is tried again.
        calls.clear()
        signal.measure(
            jobs, root / "work", root, root / "genes.gff3", tools, sample=sample, log=lambda _: None
        )
        assert calls == ["SRR2"], calls
    rejects(lambda: signal.Tools.find("/missing/STAR"), "STAR")
    print("PASS trim sweep with a stand-in STAR, resumed from the work folder")


def fixture_fetch(url: str, data: bytes | None) -> str:
    if url.endswith("/search"):
        assert data is not None and b"tax_eq%283702%29" in data, data
        return (FIXTURES / "ena-runs.tsv").read_text()
    if "/browser/api/xml/" in url:
        return (FIXTURES / "ena-samples.xml").read_text()
    raise AssertionError(url)


REUSED = (
    "run\treads_sampled\tmapped_unique_fraction\tnear_TSS_10\tnear_TSS_100\toffset_mode\t"
    "trim_5p\tadapter_3p\n"
    "SRR10045003\t50000\t0.709\t0.177\t0.620\t-1\t9\tTCGTATGCCGTCTTCTGCTTG\n"
    "DRR098884\t50000\t0.951\t0.166\t0.601\t-1\t-\tAGATCGGAAGAGC\n"
    "SRR7064004\t50000\t0.926\t0.146\t0.523\t0\t8\tAGATCGGAAGAGC\n"
    "SRR2102806\t50000\t0.045\t0.123\t0.352\t5\t-\tAGATCGGAAGAGC\n"
    "SRR2097904\t50000\t0.769\t0.027\t0.118\t-1\t-\tAGATCGGAAGAGC\n"
)


def check_build() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reused = root / "signals.tsv"
        reused.write_text(REUSED)
        archive = Archive(root / "cache", fetcher=fixture_fetch)
        options = tss.Options(
            cache=root / "cache", measure=False, measure_from=reused, probe_duplicates=False
        )
        summary = tss.build(AT, archive, root / "out", options, log=lambda _: None)
        assert summary["runs"] == 13 and summary["reused_signals"] == 5, summary
        sheet = load_sheet(root / "out" / "tss-seq.tsv", "tss-seq")
        qc = load_runs_qc(root / "out" / "tss-seq.runs-qc.tsv")
        reasons = {r: row["reason_left_out"] for r, row in qc.items()}
        assert set(sheet.by_run) == {"SRR10045003", "DRR098884", "SRR7064004"}, reasons
        fields = sheet.fields("SRR10045003")
        assert (fields["method"], fields["trim_5p"], fields["adapter_3p"]) == (
            "CAGE",
            "9",
            "TCGTATGCCGTCTTCTGCTTG",
        ), fields
        assert sheet.fields("DRR098884")["trim_5p"] == "-"
        assert sheet.fields("SRR7064004")["method"] == "TSS-seq"
        expected = {
            "SRR6967084": "nanoPARE",
            "SRR8618705": "NAD-RNA capture",
            "DRR098888": "cap-less control",
            "DRR221901": "RNA-seq run",
            "SRR10030449": "study is RNA-seq",
            "DRR095142": "genotype: mutant",
            "SRR7772739": "treatment: cold",
            "SRR2102806": "few uniquely mapped",
            "SRR2097904": "no TSS signal",
            "SRR27227855": "TSS signal not measured",
        }
        for accession, reason in expected.items():
            assert reason in reasons[accession], (accession, reasons[accession])
        assert all(reasons[r] for r in qc if r not in sheet.by_run)
        offline = Archive(root / "cache", offline=True)
        tss.build(AT, offline, root / "again", options, log=lambda _: None)
        assert (root / "again" / "tss-seq.tsv").read_text() == (
            root / "out" / "tss-seq.tsv"
        ).read_text()
    rejects(
        lambda: tss.build(
            AT,
            Archive(root / "none", fetcher=fixture_fetch),
            root / "x",
            tss.Options(cache=root / "none", probe_duplicates=False),
            log=lambda _: None,
        ),
        "--references",
    )
    assert writers.header("tss-seq")[4:7] == ["method", "trim_5p", "adapter_3p"]
    print("PASS offline build from recorded ENA responses with reused TSS signal")


def main() -> None:
    check_methods()
    check_read_structure()
    check_offsets()
    check_measure()
    check_build()


if __name__ == "__main__":
    main()
