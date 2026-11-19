"""Score the app's LLM steps against the curated gold sheets: `genesis eval llm`.

Two tasks, each run through the same code the app and the sheet builders use:

- `triage`: the mining step that accepts, holds or excludes a candidate study for an assay
  (`web.mining.assess`, with its second evidence round). Gold: a study with a kept run in a
  workflow's gold sheet matches that assay; a study kept for another assay and never
  considered for this one, or whose runs were all left out as another assay, does not.
- `adjudicate`: the per-run keep or leave-out decision (`sheets.adjudicate`). Gold: kept runs
  are keep; runs left out for genotype, treatment, assay or tissue reasons are leave. Runs
  left out for read, file or duplicate reasons are not metadata decisions and are skipped.

Studies are split by a hash of their accession, so dev and test never share a study:
`--split dev` holds about 80% of each species' studies and `--split test` the rest. Tune
on dev; score test at milestones. Responses are cached in the runtime by request hash, so
rerunning the same cases costs nothing; a changed prompt, schema, model or input is a new
request.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import datetime
import functools
import hashlib
import json
import random
import time
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..sheets import adjudicate as adjudication
from ..sheets.archives import Archive
from ..sheets.score import reason_class
from ..sheets.species import load as load_species
from . import providers

ASSAYS = {
    "rna-seq": "RNA-seq",
    "atac-seq": "ATAC-seq",
    "dap-seq": "DAP-seq",
    "tss-seq": "TSS",
    "scatac-reads": "scATAC-seq",
}
METADATA_CLASSES = {"genotype", "treatment", "assay", "tissue"}
HISTORY = "LLM-HISTORY.md"
HEADER = (
    "| date | task | split | model | cases | metrics | cost USD | seconds | label |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
)


def split_of(study: str) -> str:
    return "test" if int(hashlib.sha256(study.encode()).hexdigest(), 16) % 5 == 0 else "dev"


def gold_rows(gold: Path, species: str, workflow: str) -> list[dict[str, str]]:
    path = gold / species / f"{workflow}-curated.runs-qc.tsv"
    if not path.is_file():
        return []
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    for row in rows:
        row["study"] = row.get("study") or row["sample_id"].split("_")[0]
    return rows


def triage_cases(gold: Path, species: str) -> list[dict[str, Any]]:
    """(study, assay, expected) for studies the app's archive lookup can read."""
    from ..web.intake import ACCESSION

    kept: dict[str, set[str]] = defaultdict(set)
    seen: dict[str, set[str]] = defaultdict(set)
    assay_only: dict[str, set[str]] = defaultdict(set)
    for workflow in ASSAYS:
        studies: dict[str, list[str]] = defaultdict(list)
        for row in gold_rows(gold, species, workflow):
            seen[workflow].add(row["study"])
            studies[row["study"]].append(
                "kept" if row["in_sheet"] == "yes" else reason_class(row["reason_left_out"])
            )
            if row["in_sheet"] == "yes":
                kept[workflow].add(row["study"])
        assay_only[workflow] = {s for s, c in studies.items() if set(c) == {"assay"}}
    cases = []
    for workflow, assay in ASSAYS.items():
        for study in sorted(kept[workflow]):
            cases.append({"study": study, "assay": assay, "expected": "match"})
        for study in sorted(assay_only[workflow]):
            cases.append({"study": study, "assay": assay, "expected": "exclude", "why": "assay"})
        for other in ASSAYS:
            if other == workflow:
                continue
            for study in sorted(kept[other] - seen[workflow]):
                cases.append({"study": study, "assay": assay, "expected": "exclude", "why": other})
    return [
        {**case, "species": species, "split": split_of(case["study"])}
        for case in cases
        if ACCESSION.fullmatch(case["study"])
    ]


def adjudicate_cases(gold: Path, species: str, workflow: str) -> list[dict[str, Any]]:
    cases = []
    for row in gold_rows(gold, species, workflow):
        kind = "" if row["in_sheet"] == "yes" else reason_class(row["reason_left_out"])
        if kind and kind not in METADATA_CLASSES:
            continue
        cases.append(
            {
                "species": species,
                "workflow": workflow,
                "study": row["study"],
                "run": row["run"],
                "expected": "keep" if not kind else "leave",
                "reason_class": kind,
                "gold_reason": row["reason_left_out"],
                "split": split_of(row["study"]),
            }
        )
    return cases


def verified_cases(path: Path, species: str, workflow: str) -> list[dict[str, Any]]:
    """Cases from a hand-verified table (species, workflow, study, run, decision,
    reason_class, note), such as evals/adjudication-verified.tsv."""
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    return [
        {
            "species": species,
            "workflow": workflow,
            "study": row["study"],
            "run": row["run"],
            "expected": row["decision"],
            "reason_class": row["reason_class"],
            "gold_reason": row["note"],
            "split": split_of(row["study"]),
        }
        for row in rows
        if row["species"] == species and row["workflow"] == workflow
    ]


def sample(
    cases: list[dict[str, Any]], split: str, studies: int, per_study: int, seed: int
) -> list[dict[str, Any]]:
    """Up to `per_study` cases from each of `studies` studies in the split, chosen by seed;
    within a study, keep and leave cases alternate so both are represented."""
    by_study: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        if case["split"] == split:
            by_study[case["study"]].append(case)
    rng = random.Random(seed)
    chosen = sorted(by_study)
    rng.shuffle(chosen)
    picked = []
    for study in chosen[:studies]:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for case in by_study[study]:
            groups[case["expected"]].append(case)
        for group in groups.values():
            rng.shuffle(group)
        mixed = [c for pair in zip(*groups.values(), strict=False) for c in pair]
        rest = [c for g in groups.values() for c in g if c not in mixed]
        picked += (mixed + rest)[:per_study]
    return picked


@contextlib.contextmanager
def cached_responses() -> Iterator[None]:
    """Replay cached responses for app code that does not ask for the cache itself."""
    original = providers.invoke
    providers.invoke = functools.partial(original, cache=True)  # type: ignore[assignment]
    try:
        yield
    finally:
        providers.invoke = original  # type: ignore[assignment]


def cost(usage: dict[str, Any], config: dict[str, Any]) -> float:
    prices = config.get("pricing") or {}
    tokens_in, tokens_out = usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0
    return (
        tokens_in * prices.get("input_per_million", 0)
        + tokens_out * prices.get("output_per_million", 0)
    ) / 1e6


def run_triage(
    state: Path, config: dict[str, Any], cases: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    from ..contracts.records import fingerprint
    from ..web import mining

    results = []
    for case in cases:
        name = load_species(case["species"]).scientific_name
        run: dict[str, Any] = {
            "candidates": [],
            "species": [name],
            "plan": {},
            "usage": {},
            "ledger": [],
        }
        candidate: dict[str, Any] = {
            "id": fingerprint(case["study"]),
            "accession": case["study"],
            "aliases": [case["study"]],
            "query": {"assay": case["assay"], "species": name},
            "title": "",
            "species": name,
            "status": "discovered",
        }
        try:
            with cached_responses():
                mining.assess(state, run, candidate, config, None)
                if candidate["status"] == "investigating":
                    mining.assess(state, run, candidate, config, None)
            status, error = candidate["status"], ""
        except (ValueError, OSError) as failure:
            status, error = "error", str(failure)[:300]
        results.append(
            {
                **case,
                "status": status,
                "reason": candidate.get("reason", ""),
                "error": error,
                "usage": run["usage"],
            }
        )
    return results


def run_adjudicate(
    state: Path, config: dict[str, Any], cases: list[dict[str, Any]], cache: Path
) -> list[dict[str, Any]]:
    from ..web import mining_sources

    archive = Archive(cache)
    by_study: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        by_study[(case["species"], case["workflow"], case["study"])].append(case)
    results = []
    for (species_name, workflow, study), group in by_study.items():
        species = load_species(species_name)
        records = {r["run_accession"]: r for r in archive.runs(c["run"] for c in group)}
        samples = archive.samples(r["sample_accession"] for r in records.values())
        study_text = {"accession": study, "title": "", "text": ""}
        try:
            inspected = mining_sources.inspect(state, study)
            study_text["text"] = inspected["text"]
        except ValueError, OSError:
            pass
        found = [c for c in group if c["run"] in records]
        results += [
            {**c, "decision": "error", "reason": "run not found in ENA", "usage": {}}
            for c in group
            if c["run"] not in records
        ]
        for start in range(0, len(found), adjudication.BATCH):
            batch = found[start : start + adjudication.BATCH]
            pairs = [
                (records[c["run"]], samples.get(records[c["run"]]["sample_accession"]))
                for c in batch
            ]
            if not study_text["title"]:
                study_text["title"] = pairs[0][0].get("study_title", "")
            decisions, usage = adjudication.adjudicate_study(
                state / "catalog", config, species, workflow, study_text, pairs
            )
            answers = {d["run"]: d for d in decisions}
            for number, c in enumerate(batch):
                answer = answers[c["run"]]
                results.append(
                    {
                        **c,
                        "decision": answer["decision"],
                        "predicted_class": answer["reason_class"],
                        "reason": answer["reason"],
                        "quotes": answer["quotes"],
                        # the call's usage is counted once, on its first case
                        "usage": usage if number == 0 else {},
                    }
                )
    return results


def ratio(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def triage_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    positives = [r for r in results if r["expected"] == "match"]
    negatives = [r for r in results if r["expected"] == "exclude"]
    return {
        "match_accepted": ratio(sum(r["status"] == "accepted" for r in positives), len(positives)),
        "match_not_excluded": ratio(
            sum(r["status"] in {"accepted", "uncertain"} for r in positives), len(positives)
        ),
        "exclude_excluded": ratio(
            sum(r["status"] == "excluded" for r in negatives), len(negatives)
        ),
        "exclude_accepted": ratio(
            sum(r["status"] == "accepted" for r in negatives), len(negatives)
        ),
        "excluded_precision": ratio(
            sum(r["status"] == "excluded" for r in negatives),
            sum(r["status"] == "excluded" for r in results),
        ),
        "errors": sum(r["status"] == "error" for r in results),
        "statuses": dict(Counter(f"{r['expected']}->{r['status']}" for r in results)),
    }


def adjudicate_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    decided = [r for r in results if r["decision"] in {"keep", "leave"}]
    agree = [r for r in decided if r["decision"] == r["expected"]]
    keeps = [r for r in decided if r["decision"] == "keep"]
    gold_keeps = [r for r in decided if r["expected"] == "keep"]
    return {
        "agreement": ratio(len(agree), len(decided)),
        "keep_precision": ratio(sum(r["expected"] == "keep" for r in keeps), len(keeps)),
        "keep_recall": ratio(sum(r["decision"] == "keep" for r in gold_keeps), len(gold_keeps)),
        "uncertain": ratio(sum(r["decision"] == "uncertain" for r in results), len(results)),
        "errors": sum(r["decision"] == "error" for r in results),
        "by_gold_class": {
            k or "keep": ratio(
                sum(r["decision"] == r["expected"] for r in decided if r["reason_class"] == k),
                sum(r["reason_class"] == k for r in decided),
            )
            for k in sorted({r["reason_class"] for r in decided})
        },
    }


def append_history(path: Path, row: dict[str, Any]) -> None:
    if not path.exists():
        path.write_text(
            "# LLM evaluation history\n\nOne row per `genesis eval llm` run.\n\n" + HEADER
        )
    headline = {
        k: v for k, v in row["metrics"].items() if not isinstance(v, dict) and v is not None
    }
    cells = [
        row["date"],
        row["task"],
        row["split"],
        row["model"],
        str(row["cases"]),
        ", ".join(f"{k} {v}" for k, v in headline.items()),
        f"{row['cost_usd']:.2f}",
        f"{row['seconds']:.0f}",
        row.get("label") or "",
    ]
    with path.open("a") as handle:
        handle.write("| " + " | ".join(cells) + " |\n")


def evaluate(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    state = args.root / "state"
    cases: list[dict[str, Any]] = []
    for species in args.species:
        if args.task == "triage":
            cases += triage_cases(args.gold, species)
        elif args.verified:
            cases += verified_cases(args.verified, species, args.workflow)
        else:
            cases += adjudicate_cases(args.gold, species, args.workflow)
    chosen = sample(cases, args.split, args.studies, args.per_study, args.seed)
    if not chosen:
        raise ValueError("No gold cases for this species, workflow and split")
    if args.dry_run:
        return {
            "cases": len(chosen),
            "studies": len({c["study"] for c in chosen}),
            "expected": dict(Counter(c["expected"] for c in chosen)),
        }, 0
    config = providers.select(
        args.root / "config/providers.json",
        args.provider or providers.default_id(args.root / "config/providers.json") or "",
    )
    if args.max_output:
        config = {**config, "max_tokens": args.max_output}
    started = time.monotonic()
    if args.task == "triage":
        results = run_triage(state, config, chosen)
        metrics = triage_metrics(results)
    else:
        results = run_adjudicate(state, config, chosen, args.cache or state / "sheets-cache")
        metrics = adjudicate_metrics(results)
    seconds = time.monotonic() - started
    spent = sum(cost(r["usage"], config) for r in results if r.get("usage"))
    report = {
        "date": datetime.date.today().isoformat(),
        "task": args.task,
        "split": args.split,
        "model": config["model"],
        "provider_mode": config["mode"],
        "cases": len(results),
        "seed": args.seed,
        "metrics": metrics,
        "cost_usd": round(spent, 4),
        "seconds": round(seconds, 1),
        "label": args.label,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    with (args.out / "cases.jsonl").open("w") as handle:
        for result in results:
            handle.write(json.dumps({k: v for k, v in result.items() if k != "usage"}) + "\n")
    if args.history:
        append_history(args.history, report)
    return report, 0


def configure(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="operation", required=True)
    llm = commands.add_parser(
        "llm",
        help="Score an LLM step against the gold sheets",
        description=__doc__.split("\n\n")[0] if __doc__ else None,
    )
    llm.set_defaults(curation_handler=evaluate)
    llm.add_argument("--task", required=True, choices=("triage", "adjudicate"))
    llm.add_argument("--root", type=Path, required=True, help="Runtime with the provider")
    llm.add_argument("--provider", help="Provider ID (default: the runtime's default)")
    llm.add_argument("--gold", type=Path, required=True, help="Folder from sheets gold-fetch")
    llm.add_argument(
        "--verified",
        type=Path,
        help="Adjudicate against this hand-verified table instead of the gold runs-qc tables",
    )
    llm.add_argument("--species", nargs="+", required=True)
    llm.add_argument("--workflow", default="rna-seq", choices=sorted(adjudication.CRITERIA))
    llm.add_argument("--split", default="dev", choices=("dev", "test"))
    llm.add_argument("--studies", type=int, default=10, help="Studies to sample")
    llm.add_argument("--per-study", type=int, default=10, help="Cases per study")
    llm.add_argument("--seed", type=int, default=1)
    llm.add_argument("--max-output", type=int, help="Override the provider's max_tokens")
    llm.add_argument("--cache", type=Path, help="ENA cache (default: RUNTIME/state/sheets-cache)")
    llm.add_argument("--out", type=Path, required=True, help="Folder for report and cases")
    llm.add_argument("--history", type=Path, help=f"Append a row to this file ({HISTORY})")
    llm.add_argument("--label", help="Note for the history row")
    llm.add_argument(
        "--dry-run", action="store_true", help="Count the cases; call no model or archive"
    )
