"""DAP-seq sheet building, offline: DAP wording, control words, automatic and declared pairing,
split mates, layout rules, the read-length probe and an end-to-end build from recorded ENA
responses (tests/fixtures/sheets-dap)."""

from __future__ import annotations

import gzip
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from helpers import rejects

from genesis_curation.sheets import dap, pairing, probes, species
from genesis_curation.sheets.archives import Archive
from genesis_curation.sheets.pairing import Library, StudyRule
from genesis_curation.sheets.tables import load_runs_qc, load_sheet

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sheets-dap"
AT = species.load("Arabidopsis_thaliana")


def check_wording() -> None:
    for text in ("HY5 DAP-seq", "WRKY1 DAP-1", "DAPseq_GUS", "LFY ampDAP-seq", "seq-DAP-seq"):
        assert dap.names_dap(text), text
    for text in ("Col-0 endosperm 7 DAP rep 3", "R1_84DAP_I", "seed_DAP6", "at_6_dap_1"):
        assert not dap.names_dap(text), text  # days after pollination
    for text in ("dark-adapted", "DAPI staining", "multiDAP sequencing"):
        assert not dap.names_dap(text), text
    strategies = {"OTHER"}
    run = {
        "experiment_title": "NextSeq 500 sequencing: WT rep 1 5GRO-seq",
        "library_strategy": "OTHER",
    }
    assert dap.assay_reason(run, strategies) == "not DAP-seq (5GRO-seq)"
    run = {"experiment_title": "HiSeq X Ten sequencing: Input AthDNAc", "library_strategy": "OTHER"}
    assert dap.assay_reason(run, strategies) == ""  # an instrument name is not an assay
    run = {"experiment_title": "NAC103-GFP, input, rep1", "library_strategy": "ChIP-Seq"}
    assert dap.assay_reason(run, strategies) == "not DAP-seq (ChIP-Seq)"
    print("PASS DAP wording and other assays")


def check_controls() -> None:
    for label in (
        "PIX-HALO",
        "pHALO_Sb_1",
        "pIXHALO_Col-B_b",
        "GFP_DAP_IM_rep2",
        "NAC103-Halo, input",
        "empty vector replicate 1",
        "bead-only rep 1",
        "DAPseq_GUS",
        "AtInput",
        "no bait protein (empty) DAP-seq",
    ):
        assert pairing.is_control(label), label
    for label in (
        "NAC103-Halo, DAP, rep2",
        "Halo-awn1",
        "GST-MSD1 rep 2",
        "DAP-bHLH99",
        "repeat 1",
    ):
        assert not pairing.is_control(label), label
    assert pairing.replicate("MYB67 replicate 2") == "2"
    assert pairing.replicate("SbBES1_1") == "1"
    assert pairing.names("GSM5958417 lib_8") == set()  # accessions and numbers name nothing
    print("PASS control words")


def run(accession: str, title: str, **values: str) -> dict[str, str]:
    base = {
        "run_accession": accession,
        "experiment_title": f"NextSeq 500 sequencing: {title}",
        "scientific_name": "Arabidopsis thaliana",
        "library_layout": "PAIRED",
        "read_count": "5000000",
        "instrument_model": "NextSeq 500",
    }
    return base | values


def check_pairing() -> None:
    runs = [
        run("SRR1", "MYC2_rep1", instrument_model="HiSeq 2500"),
        run("SRR2", "MYC4_rep1", instrument_model="NovaSeq 6000"),
        run("SRR3", "Input DNAa_rep1", instrument_model="HiSeq 2500"),
        run("SRR4", "Input DNAa_rep2", instrument_model="HiSeq 2500"),
        run("SRR5", "Input DNAb", instrument_model="NovaSeq 6000"),
    ]
    units = {u.label: u for u in pairing.assign(runs, StudyRule("PRJNA1"))}
    assert units["MYC2_rep1"].control == "Input DNAa_rep1", units["MYC2_rep1"]
    assert units["MYC4_rep1"].control == "Input DNAb"  # sequenced in the same batch
    assert units["Input DNAb"].role == "control"
    declared = pairing.catalog().rule("PRJNA649293")
    units = {u.label: u for u in pairing.assign(runs, declared)}
    assert units["MYC2_rep1"].pairing == "declared"
    # Mates uploaded as two single-end runs: read 2 is left out, read 1 stays single-end.
    runs = [
        run("SRR10", "MwMYB1"),
        run("SRR12", "Input2", library_layout="SINGLE", read_count="46570824"),
        run("SRR13", "Input1", library_layout="SINGLE", read_count="46570824"),
    ]
    units = {u.run["run_accession"]: u for u in pairing.assign(runs, StudyRule("PRJNA2"))}
    assert units["SRR12"].reason.startswith("read 2 of SRR13"), units["SRR12"]
    assert units["SRR13"].label == "Input" and units["SRR10"].control == units["SRR13"].label
    print("PASS automatic, declared and split-mate pairing")


def check_assemble() -> None:
    pe, se = ("https://x/T_1.fastq.gz", "https://x/T_2.fastq.gz"), ("https://x/C.fastq.gz", "-")
    built = pairing.assemble(
        [Library("t_pe", *pe, "c_se"), Library("t_se", *se, "c_pe")],
        [Library("c_se", *se), Library("c_pe", "https://x/C_1.fastq.gz", "https://x/C_2.fastq.gz")],
        "Arabidopsis_thaliana",
        "ref.fa.gz",
    )
    rows = {row["sample_id"]: row for row in built.rows}
    assert rows["t_pe"]["read2_url"] == "-"  # paired treatment, single-end control: read 1 only
    assert rows["t_se"]["control_sample"] == "c_pe_read1"
    assert rows["c_pe_read1"]["read2_url"] == "-" and "c_pe" not in rows  # only the copy is used
    assert set(rows) == {"t_pe", "t_se", "c_se", "c_pe_read1"}
    bad = [dict(rows["t_pe"], control_sample="missing")]
    rejects(lambda: pairing.check_controls(bad), "not a sheet row")
    print("PASS layout rules and control checks")


def fastq(lengths: list[int]) -> bytes:
    records = "".join(f"@r{i}\n{'A' * n}\n+\n{'I' * n}\n" for i, n in enumerate(lengths))
    return gzip.compress(records.encode())


def check_probe() -> None:
    raw = fastq([100] * 150)
    assert probes.read_lengths(raw[: len(raw) // 2]) == [100] * len(
        probes.read_lengths(raw[: len(raw) // 2])
    )
    assert probes.probe("u", lambda url, size: raw).uniform
    trimmed = probes.probe("u", lambda url, size: fastq([100, 98, 75] * 40))
    assert trimmed.problem.startswith("reads trimmed before upload (first 100 reads have 3 lengths")
    calls: list[str] = []

    def failing(url: str, size: int) -> bytes:
        calls.append(url)
        raise OSError("refused")

    with tempfile.TemporaryDirectory() as tmp:
        cache = probes.LengthCache(
            Path(tmp) / "lengths.json", failing, attempts=2, sleep=lambda s: None
        )
        assert cache.lengths(["https://x/a"])["https://x/a"].problem.startswith(
            "read-length probe failed"
        )
        assert len(calls) == 2
        again = probes.LengthCache(Path(tmp) / "lengths.json", lambda url, size: raw)
        assert again.lengths(["https://x/a"])["https://x/a"].uniform  # failures are retried
        offline = probes.LengthCache(Path(tmp) / "lengths.json", failing, offline=True)
        assert offline.lengths(["https://x/a"])["https://x/a"].uniform  # read from the cache
    print("PASS read-length probe")


def fixture_fetch(url: str, data: bytes | None) -> str:
    if "/filereport" in url:
        accession = parse_qs(urlparse(url).query)["accession"][0]
        path = FIXTURES / f"{accession}.tsv"
        if path.exists():
            return path.read_text()
        return (FIXTURES / "search.tsv").read_text().splitlines()[0] + "\n"
    assert data is not None and b"tax_eq%283702%29" in data, data
    return (FIXTURES / "search.tsv").read_text()


TRIMMED = ("SRR28539615", "SRR28539616", "SRR28539628")  # PRJNA1095882: FUL-SOC1, FUL, input


def fake_reads(url: str, size: int) -> bytes:
    return fastq([151, 149, 120] * 40 if any(r in url for r in TRIMMED) else [150] * 120)


def check_build() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repository = root / "dap-seq-REPO.tsv"
        repository.write_text(
            "sample_id\tread1_url\nx\thttps://ftp.sra.ebi.ac.uk/vol1/fastq/SRR393/056/SRR39373456/"
            "SRR39373456_1.fastq.gz\n"
        )
        archive = Archive(root / "cache", fetcher=fixture_fetch)
        options = dap.Options(root / "cache", skip_runs_in=[repository], fetch_reads=fake_reads)
        dap.build(AT, archive, root, options, log=lambda message: None)
        sheet = load_sheet(root / "dap-seq.tsv", "dap-seq")
        qc = load_runs_qc(root / "dap-seq.runs-qc.tsv")
        reasons = {run: row["reason_left_out"] for run, row in qc.items()}
        # PRJNA1090874: input read 2 uploaded as its own run; treatments use read 1 only.
        assert reasons["SRR28418942"].startswith("read 2 of SRR28418943")
        assert sheet.fields("SRR28418941") == {
            "layout": "single",
            "control": "SRR28418943",
            "reference_fasta": "Athaliana_447_TAIR10.fa.gz",
        }
        # PRJNA1086931: replicate n pairs with empty vector replicate n.
        assert sheet.fields("SRR28314166")["control"] == "SRR28314164"
        # PRJNA649293: declared batch inputs; tomato and Marchantia DNA left out.
        assert sheet.fields("SRR12341146")["control"] == "SRR12341150"
        assert reasons["SRR12341135"].startswith("DNA of another species (Solanum")
        assert reasons["SRR12341124"] == ""  # 1.0M reads: kept
        assert reasons["SRR12341125"].startswith("too few reads")
        # PRJNA1095882: reads trimmed before upload, so its treatments lose their control.
        assert reasons["SRR28539615"].startswith("reads trimmed before upload")
        # PRJNA1483568: one of its runs is in a repository sheet, so the whole study is.
        assert reasons["SRR39373456"] == "in repository sheet dap-seq-REPO"
        assert reasons["SRR39373459"] == "in repository sheet dap-seq-REPO"
        assert reasons["ERR17427126"] == dap.DAYS_REASON
        assert all(
            row["control_sample"] == "-"
            or row["control_sample"] in {r["sample_id"] for r in sheet.rows}
            for row in sheet.rows
        )
        offline = Archive(root / "cache", offline=True)
        options = dap.Options(root / "cache", skip_runs_in=[repository])
        dap.build(AT, offline, root / "again", options, log=lambda message: None)
        assert (root / "again" / "dap-seq.tsv").read_text() == (root / "dap-seq.tsv").read_text()
    print("PASS end-to-end build from recorded ENA responses")


def main() -> None:
    check_wording()
    check_controls()
    check_pairing()
    check_assemble()
    check_probe()
    check_build()


if __name__ == "__main__":
    main()
