"""Sheet scoring: run keys, derived fields, precision/recall, reason classes and traps."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from genesis_curation.sheets import score as scoring
from genesis_curation.sheets.tables import load_runs_qc, load_sheet

ENA = "https://ftp.sra.ebi.ac.uk/vol1/fastq"
DAP_HEADER = "sample_id\tspecies\tread1_url\tread2_url\tcontrol_sample\treference_fasta"
QC_HEADER = "run\tstudy\tin_sheet\treason_left_out"


def url(run: str, mate: str = "") -> str:
    return f"{ENA}/{run[:6]}/{run}/{run}{mate}.fastq.gz"


def dap_row(sample: str, run: str, control: str, paired: bool = False) -> str:
    read1 = url(run, "_1" if paired else "")
    read2 = url(run, "_2") if paired else "-"
    return f"{sample}\tA_thaliana\t{read1}\t{read2}\t{control}\tgenome.fa.gz"


def write(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n")
    return path


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        gold = write(
            root / "gold.tsv",
            [
                DAP_HEADER,
                dap_row("ctl", "SRR100", "-"),
                dap_row("tf1", "SRR101", "ctl"),
                dap_row("tf2", "SRR102", "ctl", paired=True),
                DAP_HEADER,  # concatenated files repeat the header; it must be skipped
            ],
        )
        gold_qc = write(
            root / "gold.qc.tsv",
            [
                QC_HEADER,
                "SRR100\tP1\tyes\t",
                "SRR101\tP1\tyes\t",
                "SRR102\tP1\tyes\t",
                "SRR103\tP1\tno\treads trimmed before upload (variable read lengths)",
                "SRR104\tP1\tno\tnot DAP-seq: 'DAP' means days after pollination",
            ],
        )
        candidate = write(
            root / "candidate.tsv",
            [
                DAP_HEADER,
                dap_row("input", "SRR100", "-"),
                dap_row("tf1", "SRR101", "input"),
                dap_row("tf2", "SRR102", "input"),  # wrong layout
                dap_row("tf4", "SRR104", "input"),  # assay trap missed
                dap_row("tf9", "SRR109", "input"),  # gold never saw it
            ],
        )
        candidate_qc = write(
            root / "candidate.qc.tsv",
            [QC_HEADER, "SRR103\tP1\tno\tread lengths differ in the first 100 reads"],
        )

        sheet = load_sheet(gold, "dap-seq")
        assert sorted(sheet.by_run) == ["SRR100", "SRR101", "SRR102"], sheet.by_run
        assert sheet.fields("SRR102") == {
            "layout": "paired",
            "control": "SRR100",
            "reference_fasta": "genome.fa.gz",
        }
        assert sorted(load_runs_qc(gold_qc)) == ["SRR100", "SRR101", "SRR102", "SRR103", "SRR104"]

        result = scoring.score("dap-seq", gold, candidate, gold_qc, candidate_qc)
        assert result["true_positive"] == 3 and result["recall"] == 1.0, result
        assert result["precision"] == 0.6 and result["precision_known"] == 0.75, result
        assert result["unseen_by_gold"] == 1 and result["false_positive_examples"] == ["SRR104"]
        # Controls are compared by run, so renaming the control sample is not an error.
        assert result["fields"]["control"]["accuracy"] == 1.0, result["fields"]
        assert result["fields"]["layout"]["examples"] == [
            {"run": "SRR102", "gold": "paired", "candidate": "single"}
        ]
        assert result["reasons"] == {
            "compared": 1,
            "agreement": 1.0,
            "pairs": {"files -> files": 1},
        }, result["reasons"]
        print("PASS run keys, derived fields, precision/recall, unseen runs and reason classes")

        for reason, expected in (
            ("paired run served as one file", "files"),
            ("not standard RNA-seq (3' end, QuantSeq)", "assay"),
            ("duplicate submission of SRR1 (same reads)", "duplicate"),
            ("genotype: mutant / transgenic", "genotype"),
            ("treatment: 12 h light", "treatment"),
            ("too few reads on genes", "qc"),
            ("no control library in the study", "control"),
            ("", ""),
        ):
            assert scoring.reason_class(reason) == expected, (reason, expected)
        print("PASS reason classes")

        traps = write(
            root / "traps.yaml",
            [
                "traps:",
                "  - {id: trimmed, species: At, workflow: dap-seq, runs: [SRR103],",
                "     expect: excluded, reason_class: files}",
                "  - {id: dap-word, species: At, workflow: dap-seq, runs: [SRR104],",
                "     expect: excluded, reason_class: assay}",
                "  - {id: paired, species: At, workflow: dap-seq, runs: [SRR102],",
                "     expect: included, fields: {layout: paired}}",
                "  - {id: unseen, species: At, workflow: dap-seq, runs: [SRR999],",
                "     expect: excluded}",
                "  - {id: other, species: At, workflow: rna-seq, runs: [SRR1], expect: excluded}",
            ],
        )
        loaded = scoring.load_traps(traps)
        checked = scoring.check_traps(
            loaded,
            "dap-seq",
            "At",
            load_sheet(candidate, "dap-seq"),
            load_runs_qc(candidate_qc),
        )
        outcome = {item["id"]: item["problems"] for item in checked["results"]}
        assert outcome == {
            "trimmed": [],
            "dap-word": ["SRR104: included"],
            "paired": ["SRR102: layout='single', expected 'paired'"],
            "unseen": ["SRR999: not seen"],
        }, outcome
        assert checked["passed"] == 1 and checked["checked"] == 4
        print("PASS traps: outcome, reason class, fields, not-seen runs, workflow filter")

        history = root / "HISTORY.md"
        command = [
            sys.executable,
            "-m",
            "genesis_curation.cli",
            "sheets",
            "score",
            "--workflow",
            "dap-seq",
            "--species",
            "At",
            "--gold-sheet",
            str(gold),
            "--gold-qc",
            str(gold_qc),
            "--candidate-sheet",
            str(candidate),
            "--candidate-qc",
            str(candidate_qc),
            "--traps",
            str(traps),
            "--history",
            str(history),
            "--label",
            "fixture",
        ]
        for _ in range(2):
            output = subprocess.run(command, check=True, capture_output=True, text=True).stdout
        assert json.loads(output)["traps"]["passed"] == 1
        lines = history.read_text().splitlines()
        assert lines[0].startswith("| Date |") and len(lines) == 4, lines
        assert lines[-1].endswith("| At | dap-seq | fixture | 0.6 | 1.0 | 0.6667 | 1/4 |"), lines
        print("PASS CLI score and history rows")


if __name__ == "__main__":
    main()
