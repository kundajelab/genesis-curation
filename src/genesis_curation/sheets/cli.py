"""`genesis sheets`: gold sets and scoring of genesis-preprocessing sample sheets."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from . import score as scoring
from .tables import FIELDS, load_runs_qc, load_sheet

EVALS = Path(__file__).resolve().parents[3] / "evals"


def gold_entries(manifest: Path) -> list[dict[str, Any]]:
    value = yaml.safe_load(manifest.read_text())
    return value["gold"]


def fetch(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    manifest = yaml.safe_load(args.manifest.read_text())
    base = manifest["base_url"].rstrip("/")
    fetched, kept = [], []
    for entry in manifest["gold"]:
        for kind in ("sheet", "runs_qc"):
            name, digest = entry[kind], entry[f"{kind}_sha256"]
            target = args.to / name
            if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
                kept.append(name)
                continue
            with urllib.request.urlopen(f"{base}/{name}", timeout=120) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(f"{name}: checksum differs from {args.manifest.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            fetched.append(name)
    return {"directory": str(args.to), "fetched": fetched, "unchanged": len(kept)}, 0


def resolve_gold(args: argparse.Namespace) -> tuple[Path, Path | None]:
    if args.gold_sheet:
        return args.gold_sheet, args.gold_qc
    if args.gold_dir is None:
        raise ValueError("Give --gold-sheet, or --gold-dir with --species (see gold-fetch)")
    for entry in gold_entries(args.manifest):
        if entry["species"] == args.species and entry["workflow"] == args.workflow:
            return args.gold_dir / entry["sheet"], args.gold_dir / entry["runs_qc"]
    raise ValueError(f"No gold sheet for {args.species} {args.workflow} in {args.manifest}")


def run_score(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    gold_sheet, gold_qc = resolve_gold(args)
    result = scoring.score(
        args.workflow, gold_sheet, args.candidate_sheet, gold_qc, args.candidate_qc
    )
    result["species"] = args.species
    if args.traps.exists():
        candidate = load_sheet(args.candidate_sheet, args.workflow)
        qc = load_runs_qc(args.candidate_qc) if args.candidate_qc else {}
        traps = scoring.load_traps(args.traps)
        result["traps"] = scoring.check_traps(traps, args.workflow, args.species, candidate, qc)
    if args.history:
        append_history(args.history, args.label or args.candidate_sheet.name, result)
    return result, 0


def append_history(path: Path, label: str, result: dict[str, Any]) -> None:
    fields = result["fields"]
    accuracy = [item["accuracy"] for item in fields.values() if item["accuracy"] is not None]
    traps = result.get("traps") or {}
    row = [
        datetime.date.today().isoformat(),
        result["species"] or "-",
        result["workflow"],
        label,
        str(result["precision"]),
        str(result["recall"]),
        str(min(accuracy)) if accuracy else "-",
        f"{traps.get('passed', 0)}/{traps.get('checked', 0)}" if traps else "-",
    ]
    if "| Date |" not in (path.read_text() if path.exists() else ""):
        with path.open("a") as handle:
            handle.write(
                "| Date | Species | Workflow | Candidate | Precision | Recall | Min field accuracy "
                "| Traps |\n|---|---|---|---|---|---|---|---|\n"
            )
    with path.open("a") as handle:
        handle.write("| " + " | ".join(row) + " |\n")


def configure(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="operation", required=True)
    gold = commands.add_parser("gold-fetch", help="Download the gold sheets listed in a manifest")
    gold.add_argument("--manifest", type=Path, default=EVALS / "gold.yaml")
    gold.add_argument("--to", type=Path, required=True)
    gold.set_defaults(curation_handler=fetch)

    run = commands.add_parser("score", help="Score a candidate sheet against gold and traps")
    run.add_argument("--workflow", required=True, choices=sorted(FIELDS))
    run.add_argument("--species", default="", help="Species folder name, e.g. Sorghum_bicolor")
    run.add_argument("--candidate-sheet", type=Path, required=True)
    run.add_argument("--candidate-qc", type=Path)
    run.add_argument("--gold-sheet", type=Path, help="Overrides the manifest lookup")
    run.add_argument("--gold-qc", type=Path)
    run.add_argument("--gold-dir", type=Path, help="Directory filled by gold-fetch")
    run.add_argument("--manifest", type=Path, default=EVALS / "gold.yaml")
    run.add_argument("--traps", type=Path, default=EVALS / "traps.yaml")
    run.add_argument("--history", type=Path, help="Markdown table to append a summary row to")
    run.add_argument("--label", help="Candidate name for the history row")
    run.set_defaults(curation_handler=run_score)
