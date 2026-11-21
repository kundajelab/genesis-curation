"""scatac-reads sheet building, offline: read structure, SRA parsing, library grouping,
reasons, the instrument probe and an end-to-end build from recorded NCBI SRA packages
(tests/fixtures/sheets-scatac, trimmed efetch records of real experiments)."""

from __future__ import annotations

import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import parse_qs

from helpers import rejects

from genesis_curation.sheets import scatac, species
from genesis_curation.sheets.archives import Archive, Sample, SraExperiment, SraRun, parse_sra
from genesis_curation.sheets.measure import instruments
from genesis_curation.sheets.tables import load_runs_qc, load_sheet

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sheets-scatac"
AT = species.load("Arabidopsis_thaliana")
SB = species.load("Sorghum_bicolor")
PACKAGES = ET.parse(FIXTURES / "sra-packages.xml").getroot().findall("EXPERIMENT_PACKAGE")
# Entrez UIDs of the fixture packages: their position, from 1.
UIDS = {str(i + 1): package for i, package in enumerate(PACKAGES)}


def sra_run(accession: str, *lengths: float, spots: int = 1000) -> SraRun:
    return SraRun(accession, spots, tuple(lengths))


def experiment(
    accession: str,
    *runs: SraRun,
    title: str = "",
    library_name: str = "",
    source: str = "GENOMIC SINGLE CELL",
    biosample: str = "SAMN1",
    sample_title: str = "",
) -> SraExperiment:
    return SraExperiment(
        accession=accession,
        study="SRP1",
        study_title="",
        title=title,
        library_name=library_name,
        strategy="ATAC-seq",
        source=source,
        selection="other",
        instrument="",
        taxon=3702,
        organism="Arabidopsis thaliana",
        sample=Sample(biosample, title=sample_title),
        runs=runs,
    )


def check_structure() -> None:
    cases = {
        (8, 50, 16, 49): ("10x-atac-v1", ""),
        (150, 16, 150): ("10x-atac-v1", ""),
        (8, 24, 151, 151): ("10x-multiome-atac", ""),
        (50, 24, 50): ("10x-multiome-atac", ""),
        # sci-ATAC (SRR27704581): a 28-base index read, a sample index then the cell index
        (107, 106, 28): ("sci-atac", ""),
    }
    for lengths, expected in cases.items():
        assert scatac.run_structure(sra_run("SRR1", *lengths)) == expected, lengths
    chemistry, why = scatac.run_structure(sra_run("SRR1", 150, 150))
    assert not chemistry and why.startswith("no barcode read in SRA"), why
    for lengths in ((28, 151), (10, 10, 28, 88), (107, 106, 20), (16, 16, 50, 50)):
        chemistry, why = scatac.run_structure(sra_run("SRR1", *lengths))
        assert not chemistry and "not a chemistry the workflow reads" in why, (lengths, why)
    # an index read of another length is named, not called missing
    assert "barcode read of 20 bases" in scatac.run_structure(sra_run("SRR1", 107, 106, 20))[1]
    assert scatac.run_structure(SraRun("SRR1", 0)) == ("", "no reads in SRA (not loaded)")
    assert scatac.run_structure(sra_run("SRR1", 23.8, 50.2, 49.9))[0] == "10x-multiome-atac"
    # issue #14: a library mixing chemistries keeps the runs of the one with most spots
    library = scatac.Library(
        experiments=[
            experiment(
                "SRX1",
                sra_run("SRR1", 8, 24, 150, 150, spots=900),
                sra_run("SRR2", 8, 16, 150, 150, spots=100),
                sra_run("SRR3", 150, 150, spots=50),
            )
        ]
    )
    structures = [scatac.run_structure(run) for run in library.all_runs]
    assert scatac.keep_main_chemistry(library, structures) == "10x-multiome-atac"
    assert [run.accession for run in library.runs] == ["SRR1"], library.dropped
    assert "10x-atac-v1" in library.dropped["SRR2"]
    assert library.dropped["SRR3"].startswith("no barcode read in SRA")
    print("PASS 10x and sci-ATAC read structure, chemistry and whitelist")


def check_parse() -> None:
    found = {e.accession: e for e in parse_sra((FIXTURES / "sra-packages.xml").read_text())}
    assert len(found) == len(PACKAGES)
    bam = found["SRX10771698"]
    assert bam.runs[0].bam and bam.runs[0].lengths == (49, 49), bam.runs
    assert bam.sample.accession == "SAMN19014836" and bam.study == "SRP318614"
    assert bam.sample.attributes["ecotype"] == "Col-0"
    pool = found["SRX33315185"]
    assert pool.taxon != 3702 and scatac.mixed_pool(AT, pool), pool.organism
    assert not scatac.mixed_pool(AT, found["SRX33315188"])
    unloaded = found["SRX32782395"]
    assert unloaded.runs[0].spots == 0 and len(unloaded.runs[0].files) == 3
    rejects(lambda: parse_sra("<eSearchResult/>"), "not an sra experiment package set")
    print("PASS SRA efetch packages: runs, reads, BioSample, BAM and pooled samples")


def check_grouping() -> None:
    lanes = [
        experiment(f"SRX{i}", sra_run(f"SRR{i}", 150, 16, 150), library_name=f"atsht_S{i}")
        for i in (1, 2, 3, 4)
    ]
    libraries = scatac.group(lanes, scatac.Decisions())
    assert len(libraries) == 1 and [r.accession for r in libraries[0].runs] == [
        "SRR1",
        "SRR2",
        "SRR3",
        "SRR4",
    ]
    # One BioSample for the whole study, but the titles name four libraries.
    shared = [
        experiment(f"SRX{i}", sra_run(f"SRR{i}", 8, 24, 50, 50), title=title)
        for i, title in enumerate(("CK rep1", "CK rep2", "case rep1", "case rep2"))
    ]
    assert len(scatac.group(shared, scatac.Decisions())) == 4
    assert scatac.lane_free("snCUT&Tag of 10-day protoplast, Lane 4") == (
        "sncut&tag of 10-day protoplast"
    )
    assert scatac.lane_free("Beads_2") == "beads_2"  # a replicate, not a lane
    merged = scatac.Decisions(merge=[["SRX0", "SRX3"]])
    sizes = sorted(len(lib.experiments) for lib in scatac.group(shared, merged))
    assert sizes == [1, 1, 2], sizes
    tenx = experiment("SRX9", sra_run("SRR9", 50, 16, 50, spots=216_534_475))
    bam = experiment("SRX8", SraRun("SRR8", 216_534_457, (49, 49), bam=True))
    hifi = experiment("SRX7", SraRun("SRR7", 2_324_605, (3745,), bam=True))
    found = scatac.bam_duplicates([tenx, bam, hifi])
    assert found == {"SRX8": "same library as SRX9, submitted as BAM without the barcode read"}
    print("PASS libraries: lanes merged, shared BioSamples kept apart, BAM resubmissions")


def check_reasons() -> None:
    rna = experiment("SRX1", sra_run("SRR1", 28, 90), source="TRANSCRIPTOMIC SINGLE CELL")
    assert scatac.not_atac(rna) == "not ATAC (RNA)"
    cut = experiment("SRX2", sra_run("SRR2", 50, 24, 50), title="snCUT&Tag of 3-day protoplast")
    assert scatac.not_atac(cut) == "not ATAC (snCUT&Tag)"
    his = experiment("SRX3", sra_run("SRR3", 70, 50), title="snHis")
    assert scatac.not_atac(his) == "not ATAC (snHis)"
    atac = experiment("SRX4", sra_run("SRR4", 50, 16, 50), title="Multiome, Col-0, rep1, ATAC")
    assert scatac.not_atac(atac) == "" and scatac.looks_single_cell(atac)
    bulk = experiment("SRX5", sra_run("SRR5", 50, 50), title="ATAC_Ep_LD-rep1", source="GENOMIC")
    assert not scatac.looks_single_cell(bulk)
    assert scatac.names_species(AT, "Alyrata-Athaliana-Boleracea") and not scatac.names_species(
        AT, "Arabidopsis lyrata"
    )
    assert scatac.names_species(SB, "Sorghum bicolor x S. propinquum")
    # Within-study titles: "case" lacks the control marker the study's other titles have.
    study = [
        experiment(
            f"SRX{i}",
            sra_run(f"SRR{i}", 8, 24, 50, 50, spots=10**6),
            title=title,
            biosample="SAMN9",
            sample_title="Col-0 root",
        )
        for i, title in enumerate(("Col-0,control,rep1", "Col-0,control,rep2", "Col-0,case,rep1"))
    ]
    libraries = {lib.lead.accession: lib for lib in scatac.metadata(AT, study, scatac.Decisions())}
    assert not libraries["SRX0"].reason and not libraries["SRX1"].reason
    # issue #13: treated libraries are kept; their treatment is recorded, not a reason
    assert not libraries["SRX2"].reason, libraries["SRX2"].reason
    assert libraries["SRX0"].sample_id == "SRP1_root_control_rep1", libraries["SRX0"].sample_id
    print("PASS reasons: not ATAC, single-cell hints, mixed pools, within-study titles")


def check_decisions() -> None:
    chosen = scatac.decisions(SB)
    assert chosen.studies["SRP474955"] == {"genotype": "BTx623"}
    assert scatac.decisions(AT).studies["SRP273727"] == {"treatment": "untreated"}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "scatac.yaml"
        path.write_text("Arabidopsis_thaliana:\n  merge:\n    - experiments: [SRX1, SRX2]\n")
        rejects(lambda: scatac.decisions(AT, path), "without why")
    print("PASS data/scatac.yaml: study values, and every entry needs its evidence")


def names(run: str, rows: Sequence[int], early: str, late: str) -> list[str]:
    def name(row: int, instrument: str) -> str:
        return f"{instrument}:509:HJ5C2DSX7:1:1102:{row}:32049"

    return [name(row, early if row <= 200_000 else late) for row in rows]


def check_instruments() -> None:
    assert instruments.rows(10) == list(range(1, 11))
    assert instruments.rows(882_033_886)[:3] == [1, 100_000, 200_000]
    assert instruments.instrument("M02100:243:000000000-L424W:1:1101:14216:1630") == "M02100"
    assert instruments.instrument("LH00123:45:22FXYZLT3:1:1101:1:1") == "LH00123"
    assert instruments.instrument("1") == "" and instruments.instrument("SRR1.1 1 length=50") == ""
    mixed = instruments.probe("SRR1", 10**8, lambda run, rows: names(run, rows, "M02100", "A00178"))
    single = instruments.probe(
        "SRR2", 10**8, lambda run, rows: names(run, rows, "A00178", "A00178")
    )
    other = instruments.probe("SRR3", 10**8, lambda run, rows: names(run, rows, "A00123", "A00123"))
    verdict = instruments.library_verdict([mixed])
    assert verdict.startswith("mixed instruments: first reads from M02100, later reads from A00178")
    assert instruments.library_verdict([single, other]) == ""  # two NovaSeqs agree
    assert instruments.library_verdict([single, mixed]).startswith("mixed instruments")
    lite = instruments.probe("SRR4", 100, lambda run, rows: [str(r) for r in rows])
    assert not lite.known and instruments.library_verdict([lite]) == ""

    def missing(run: str, rows: Sequence[int]) -> list[str]:
        raise OSError("failed to resolve accession")

    failed = instruments.probe("SRR5", 100, missing)
    assert failed.error == "failed to resolve accession"
    with tempfile.TemporaryDirectory() as tmp:
        calls: list[str] = []

        def dump(run: str, rows: Sequence[int]) -> list[str]:
            calls.append(run)
            if run == "SRR5":
                raise OSError("timeout")
            return names(run, rows, "A00178", "A00178")

        for _ in range(2):
            found = instruments.measure(
                {"SRR2": 10**6, "SRR5": 10**6}, Path(tmp), dump, log=lambda _: None
            )
        assert calls == ["SRR2", "SRR5", "SRR5"], calls  # failures are asked again
        assert found["SRR2"].known and found["SRR5"].error == "timeout"
    print("PASS instrument probe: names, families, mixed libraries, cache and failures")


def eutils_fetch(url: str, data: bytes | None) -> str:
    form = {k: v[0] for k, v in parse_qs((data or b"").decode()).items()}
    if url.endswith("esearch.fcgi"):
        term = form["term"]
        if term.startswith("txid3702[Organism:noexp]"):
            ids = [
                u for u, p in UIDS.items() if p.findtext("SAMPLE/SAMPLE_NAME/TAXON_ID") == "3702"
            ]
        elif term.endswith("[Accession]"):
            study = term.removesuffix("[Accession]")
            ids = [
                u for u, p in UIDS.items() if p.findtext("STUDY/IDENTIFIERS/PRIMARY_ID") == study
            ]
        else:
            raise AssertionError(term)
        listed = "".join(f"<Id>{uid}</Id>" for uid in ids)
        return f"<eSearchResult><Count>{len(ids)}</Count><IdList>{listed}</IdList></eSearchResult>"
    if url.endswith("efetch.fcgi"):
        packages = "".join(
            ET.tostring(UIDS[uid], encoding="unicode") for uid in form["id"].split(",")
        )
        return f"<EXPERIMENT_PACKAGE_SET>{packages}</EXPERIMENT_PACKAGE_SET>"
    raise AssertionError(url)


def fixture_dump(run: str, rows: Sequence[int]) -> list[str]:
    if run in ("SRR38507144",):  # MiSeq reads first, then NovaSeq (SRP699388)
        return names(run, rows, "M02100", "A00178")
    return names(run, rows, "A00178", "A00178")


def check_build() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        waits: list[float] = []
        archive = Archive(root / "cache", fetcher=eutils_fetch, sleep=waits.append)
        options = scatac.Options(cache=root / "cache", dump=fixture_dump)
        summary = scatac.build(AT, archive, root / "out", options, log=lambda _: None)
        assert waits and all(w == 0.4 for w in waits), waits
        sheet = load_sheet(root / "out" / "scatac-reads.tsv", "scatac-reads")
        qc = load_runs_qc(root / "out" / "scatac-reads.runs-qc.tsv")
        reasons = {run: row["reason_left_out"] for run, row in qc.items()}
        kept = {row["sra_runs"]: row for row in sheet.rows}
        assert set(kept) == {
            "SRR31441069,SRR31441070,SRR31441071,SRR31441072",
            "SRR20184262",
            "SRR24921616",
            "SRR24921615",
            "SRR24921614",  # treated: kept and recorded (issue #13)
            "SRR38507149",
            "SRR12712405",
        }, (sorted(kept), reasons)
        assert kept["SRR24921616"]["chemistry"] == "10x-multiome-atac"
        assert kept["SRR24921616"]["barcode_whitelist"] == "-"
        assert kept["SRR20184262"]["barcode_whitelist"] == "737K-cratac-v1.txt"
        assert kept["SRR12712405"]["reference_genes"] == "Athaliana_447_Araport11.genes.bed"
        expected = {
            "SRR14420340": "same library as SRX16219133, submitted as BAM without the barcode read",
            "SRR38507144": "mixed instruments: first reads from M02100",
            "SRR38507152": "mixed-species pool",
            "SRR37841654": "not ATAC (snCUT&Tag)",
            "SRR37923491": "no reads in SRA (not loaded)",
            "SRR34790760": "not ATAC (RNA)",
        }
        for run, reason in expected.items():
            assert reasons[run].startswith(reason), (run, reasons[run])
        assert summary["in_sheet"] == 7 and summary["runs_in_sheet"] == 10, summary
        assert qc["SRR38507144"]["instruments"] == "A00178x10,M02100x3"
        # A rebuild reads only the cache.
        again = scatac.Options(cache=root / "cache", dump=fixture_dump)
        scatac.build(
            AT, Archive(root / "cache", offline=True), root / "again", again, lambda _: None
        )
        assert (root / "again" / "scatac-reads.tsv").read_text() == (
            root / "out" / "scatac-reads.tsv"
        ).read_text()
    print("PASS offline build from recorded SRA packages with the instrument probe")


def main() -> None:
    check_structure()
    check_parse()
    check_grouping()
    check_reasons()
    check_decisions()
    check_instruments()
    check_build()


if __name__ == "__main__":
    main()
