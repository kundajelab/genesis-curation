"""LLM evaluation harness: gold cases and splits, quote checks, metrics and history, run
offline with a mock provider and a fake archive."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Iterable
from pathlib import Path
from unittest import mock

from verify_ai import configuration

from genesis_curation.llm import evaluation
from genesis_curation.registry import store
from genesis_curation.sheets import adjudicate
from genesis_curation.sheets.archives import Sample
from genesis_curation.sheets.species import load

QC = "run\tsample_id\tstrandedness\tin_sheet\treason_left_out\n"


def write_gold(root: Path) -> None:
    folder = root / "Arabidopsis_thaliana"
    folder.mkdir(parents=True)
    (folder / "rna-seq-curated.runs-qc.tsv").write_text(
        QC
        + "SRR1\tPRJNA100_leaf_S1_SRR1\tunstranded\tyes\t\n"
        + "SRR2\tPRJNA100_leaf_S2_SRR2\tunstranded\tno\t"
        + "sample title suggests a mutant or treatment\n"
        + "SRR3\tPRJNA100_leaf_S3_SRR3\tunstranded\tno\ttoo few reads on genes\n"
        + "SRR4\tPRJNA200_root_S4_SRR4\tunstranded\tno\tnot standard RNA-seq (single-cell)\n"
    )
    (folder / "atac-seq-curated.runs-qc.tsv").write_text(
        "run\tstudy\tin_sheet\treason_left_out\nSRR9\tPRJNA300\tyes\t\n"
    )


def check_cases(gold: Path) -> None:
    cases = evaluation.adjudicate_cases(gold, "Arabidopsis_thaliana", "rna-seq")
    by_run = {c["run"]: c for c in cases}
    # read-level reasons are not metadata decisions
    assert set(by_run) == {"SRR1", "SRR2", "SRR4"}, by_run
    assert by_run["SRR1"]["expected"] == "keep"
    assert by_run["SRR2"]["expected"] == "leave" and by_run["SRR2"]["reason_class"] == "genotype"
    assert by_run["SRR4"]["reason_class"] == "assay"
    triage = {
        (c["study"], c["assay"]): c["expected"]
        for c in evaluation.triage_cases(gold, "Arabidopsis_thaliana")
    }
    assert triage[("PRJNA100", "RNA-seq")] == "match"
    assert triage[("PRJNA200", "RNA-seq")] == "exclude"  # all runs another assay
    assert triage[("PRJNA300", "ATAC-seq")] == "match"
    assert triage[("PRJNA300", "RNA-seq")] == "exclude"  # kept for ATAC, never an RNA study
    assert triage[("PRJNA100", "ATAC-seq")] == "exclude"
    assert {evaluation.split_of(s) for s in ("PRJNA100", "PRJNA100")} == {
        evaluation.split_of("PRJNA100")
    }
    both = evaluation.sample(cases * 1, "dev", 10, 10, 1) + evaluation.sample(
        cases, "test", 10, 10, 1
    )
    assert sorted(c["run"] for c in both) == ["SRR1", "SRR2", "SRR4"]


def check_quotes() -> None:
    keep = {"run": "R", "decision": "keep", "reason_class": "", "reason": "WT", "quotes": ["Col-0"]}
    assert adjudicate.verified(keep, ["ecotype Col-0 leaf"])["decision"] == "keep"
    assert adjudicate.verified(keep, ["ecotype Ler"])["decision"] == "uncertain"
    assert adjudicate.verified({**keep, "quotes": []}, ["x"])["decision"] == "uncertain"


def check_adjudicate_run(gold: Path, root: Path) -> None:
    runs = {
        "SRR1": {
            "run_accession": "SRR1",
            "sample_accession": "SAMN1",
            "sample_title": "Col-0 leaf rep1",
            "study_title": "Leaf study",
        },
        "SRR2": {
            "run_accession": "SRR2",
            "sample_accession": "SAMN2",
            "sample_title": "abc1 mutant leaf",
            "study_title": "Leaf study",
        },
    }
    answer = {
        "decisions": [
            {
                "run": "SRR1",
                "decision": "keep",
                "reason_class": "",
                "reason": "wild type",
                "quotes": ["Col-0 leaf"],
            },
            {
                "run": "SRR2",
                "decision": "leave",
                "reason_class": "genotype",
                "reason": "mutant",
                "quotes": ["abc1 mutant"],
            },
        ]
    }
    config = {
        **configuration(),
        "mock_response": answer,
        "pricing": {"input_per_million": 4, "output_per_million": 20},
    }

    def fake_runs(
        self: object, accessions: Iterable[str], fields: object = None
    ) -> list[dict[str, str]]:
        return [runs[a] for a in accessions if a in runs]

    def fake_samples(self: object, accessions: Iterable[str]) -> dict[str, Sample]:
        return {a: Sample(a, attributes={"ecotype": "Col-0"}) for a in accessions}

    cases = [
        c
        for c in evaluation.adjudicate_cases(gold, "Arabidopsis_thaliana", "rna-seq")
        if c["run"] in runs
    ]
    state = root / "state"
    store.initialize(state / "catalog")
    with (
        mock.patch.object(evaluation.Archive, "runs", fake_runs),
        mock.patch.object(evaluation.Archive, "samples", fake_samples),
        mock.patch("genesis_curation.web.mining_sources.inspect", side_effect=ValueError("off")),
    ):
        results = evaluation.run_adjudicate(state, config, cases, root / "cache")
    metrics = evaluation.adjudicate_metrics(results)
    assert metrics["agreement"] == 1.0 and metrics["uncertain"] == 0.0, metrics
    # a decision for a run the model was not asked about is ignored; a missing one is uncertain
    decisions, _ = adjudicate.adjudicate_study(
        state / "catalog",
        {**config, "mock_response": {"decisions": answer["decisions"][:1]}},
        load("Arabidopsis_thaliana"),
        "rna-seq",
        {"accession": "PRJNA100", "title": "Leaf study", "text": ""},
        [(runs["SRR1"], None), (runs["SRR2"], None)],
    )
    assert [d["decision"] for d in decisions] == ["keep", "uncertain"], decisions


def check_report(gold: Path, root: Path) -> None:
    providers = root / "runtime" / "config"
    providers.mkdir(parents=True)
    (providers / "providers.json").write_text(
        json.dumps(
            {"schema_version": 1, "providers": [configuration()], "default_provider": "test"}
        )
    )
    store.initialize(root / "runtime" / "state" / "catalog")
    args = argparse.Namespace(
        task="adjudicate",
        root=root / "runtime",
        provider=None,
        gold=gold,
        species=["Arabidopsis_thaliana"],
        workflow="rna-seq",
        split=evaluation.split_of("PRJNA100"),
        studies=5,
        per_study=5,
        seed=1,
        max_output=None,
        cache=root / "cache",
        out=root / "out",
        history=root / "LLM-HISTORY.md",
        label="offline",
        dry_run=False,
    )
    fake = [
        {"run_accession": r, "sample_accession": "S" + r, "sample_title": "t"}
        for r in ("SRR1", "SRR2", "SRR4")
    ]
    with (
        mock.patch.object(evaluation.Archive, "runs", lambda self, a, f=None: fake),
        mock.patch.object(evaluation.Archive, "samples", lambda self, a: {}),
        mock.patch("genesis_curation.web.mining_sources.inspect", side_effect=ValueError("off")),
    ):
        report, status = evaluation.evaluate(args)
    assert status == 0 and report["provider_mode"] == "mock"
    assert report["metrics"]["uncertain"] == 1.0  # the default mock answers uncertain
    history = (root / "LLM-HISTORY.md").read_text()
    assert "| adjudicate |" in history and "offline" in history
    assert len((root / "out" / "cases.jsonl").read_text().splitlines()) == report["cases"]


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        gold = root / "gold"
        write_gold(gold)
        check_cases(gold)
        check_quotes()
        check_adjudicate_run(gold, root)
        check_report(gold, root)
    print("llm eval: ok")


if __name__ == "__main__":
    main()
