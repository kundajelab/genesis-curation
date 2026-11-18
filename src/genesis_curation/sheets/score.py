"""Score a candidate sample sheet against a gold sheet and the known-trap fixtures."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from .tables import Sheet, load_runs_qc, load_sheet

# Ordered: the first matching class wins. Reasons are free text in gold runs-qc tables.
REASON_CLASSES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "duplicate",
        re.compile(r"duplicate|in repository sheet|same reads|already in|another run of", re.I),
    ),
    ("control", re.compile(r"control", re.I)),
    ("species", re.compile(r"species|taxon", re.I)),
    (
        "files",
        re.compile(
            r"served as one file|layout|trimmed|read lengths?|uploaded as its own run|"
            r"mates?|file|interleave|orientation|barcode read|no 10x|^single-end|"
            r"under 1 ?MB|download|not public|no reads",
            re.I,
        ),
    ),
    (
        "assay",
        re.compile(
            r"not (?:standard )?[a-z0-9 -]*(?:seq|atac|rna|dap|tss)|not a supported method|"
            r"another assay|single-cell|capture|nanopare|means days|not a (?:plant )?TF|polysome",
            re.I,
        ),
    ),
    ("genotype", re.compile(r"genotype|mutant|accession|cultivar|transgenic", re.I)),
    ("treatment", re.compile(r"treat|stress|infect|hormone", re.I)),
    ("tissue", re.compile(r"tissue|organ|cell type|culture", re.I)),
    (
        "qc",
        re.compile(
            r"too few|frip|enrichment|mapped|unclear|depth|reads? on genes|peaks|low|signal|"
            r"control can'?t|no usable control|< ?1M",
            re.I,
        ),
    ),
)


def reason_class(reason: str) -> str:
    if not reason:
        return ""
    for name, pattern in REASON_CLASSES:
        if pattern.search(reason):
            return name
    return "other"


def ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def score(
    workflow: str,
    gold_sheet: Path,
    candidate_sheet: Path,
    gold_qc: Path | None = None,
    candidate_qc: Path | None = None,
) -> dict[str, Any]:
    gold = load_sheet(gold_sheet, workflow)
    candidate = load_sheet(candidate_sheet, workflow)
    gold_reasons = load_runs_qc(gold_qc) if gold_qc else {}
    candidate_reasons = load_runs_qc(candidate_qc) if candidate_qc else {}
    gold_in, candidate_in = set(gold.by_run), set(candidate.by_run)
    known = gold_in | set(gold_reasons)
    both = gold_in & candidate_in
    unseen = sorted(candidate_in - known) if gold_reasons else []
    result: dict[str, Any] = {
        "workflow": workflow,
        "gold_runs": len(gold_in),
        "candidate_runs": len(candidate_in),
        "true_positive": len(both),
        "precision": ratio(len(both), len(candidate_in)),
        "precision_known": ratio(len(both), len(candidate_in - set(unseen))),
        "recall": ratio(len(both), len(gold_in)),
        "unseen_by_gold": len(unseen),
        "false_positive_examples": sorted(candidate_in - gold_in - set(unseen))[:20],
        "false_negative_examples": sorted(gold_in - candidate_in)[:20],
    }
    fields: dict[str, dict[str, Any]] = {}
    for run in sorted(both):
        expected, got = gold.fields(run), candidate.fields(run)
        for name, value in expected.items():
            item = fields.setdefault(name, {"compared": 0, "agree": 0, "examples": []})
            item["compared"] += 1
            if got.get(name) == value:
                item["agree"] += 1
            elif len(item["examples"]) < 10:
                item["examples"].append({"run": run, "gold": value, "candidate": got.get(name)})
    for item in fields.values():
        item["accuracy"] = ratio(item["agree"], item["compared"])
    result["fields"] = fields
    if gold_reasons and candidate_reasons:
        pairs: Counter[str] = Counter()
        for run in sorted((known - gold_in) & (set(candidate_reasons) - candidate_in)):
            gold_class = reason_class(gold_reasons[run].get("reason_left_out", ""))
            candidate_class = reason_class(candidate_reasons[run].get("reason_left_out", ""))
            pairs[f"{gold_class or '?'} -> {candidate_class or '?'}"] += 1
        agree = sum(n for key, n in pairs.items() if key.split(" -> ")[0] == key.split(" -> ")[1])
        result["reasons"] = {
            "compared": sum(pairs.values()),
            "agreement": ratio(agree, sum(pairs.values())),
            "pairs": dict(pairs.most_common()),
        }
    return result


def load_traps(path: Path) -> list[dict[str, Any]]:
    value = yaml.safe_load(path.read_text())
    traps = value.get("traps", []) if isinstance(value, dict) else []
    for trap in traps:
        missing = {"id", "species", "workflow", "runs", "expect"} - set(trap)
        if missing:
            raise ValueError(f"Trap {trap.get('id')!r} is missing {sorted(missing)}")
        if trap["expect"] not in ("included", "excluded"):
            raise ValueError(f"Trap {trap['id']!r}: expect must be included or excluded")
    return traps


def check_traps(
    traps: list[dict[str, Any]],
    workflow: str,
    species: str,
    candidate: Sheet,
    candidate_qc: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """A trap passes when every listed run has the expected outcome (and reason class or
    field values, when given). Runs absent from the candidate's runs-qc are "not seen"."""
    results = []
    for trap in traps:
        if trap["workflow"] != workflow or trap["species"] != species:
            continue
        problems = []
        for run in trap["runs"]:
            included = run in candidate.by_run
            if not included and run not in candidate_qc:
                problems.append(f"{run}: not seen")
                continue
            if trap["expect"] == "excluded":
                if included:
                    problems.append(f"{run}: included")
                    continue
                want = trap.get("reason_class")
                got = reason_class(candidate_qc[run].get("reason_left_out", ""))
                if want and got != want:
                    problems.append(f"{run}: reason class {got or '?'}, expected {want}")
            elif not included:
                problems.append(f"{run}: excluded")
            else:
                values = candidate.fields(run)
                for name, want in (trap.get("fields") or {}).items():
                    if values.get(name) != str(want):
                        problems.append(f"{run}: {name}={values.get(name)!r}, expected {want!r}")
        results.append({"id": trap["id"], "passed": not problems, "problems": problems})
    return {
        "checked": len(results),
        "passed": sum(1 for item in results if item["passed"]),
        "results": results,
    }
