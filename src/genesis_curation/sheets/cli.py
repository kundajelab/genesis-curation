"""`genesis sheets`: gold sets and scoring of genesis-preprocessing sample sheets."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from . import adjudicate, atac, dap, rna, scatac, species, tss
from . import score as scoring
from .archives import Archive
from .measure import atac as atac_checks
from .measure.strand import Tools
from .tables import FIELDS, load_runs_qc, load_sheet

# The repository's evals folder; installed copies (the web image) set GENESIS_SHEETS_EVALS.
EVALS = Path(
    os.environ.get("GENESIS_SHEETS_EVALS") or Path(__file__).resolve().parents[3] / "evals"
)


def gold_entries(manifest: Path, section: str = "gold") -> list[dict[str, Any]]:
    value = yaml.safe_load(manifest.read_text())
    return value.get(section, [])


def fetch(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    manifest = yaml.safe_load(args.manifest.read_text())
    base = manifest["base_url"].rstrip("/")
    fetched, kept = [], []
    for entry in manifest["gold"] + manifest.get("repository_sheets", []):
        for kind in ("sheet", "runs_qc"):
            if kind not in entry:
                continue
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


BUILDERS = {
    "rna-seq": rna,
    "scatac-reads": scatac,
    "tss-seq": tss,
    "dap-seq": dap,
    "atac-seq": atac,
}


def atac_options(args: argparse.Namespace, cache: Path) -> atac.Options:
    tools = None
    if not args.no_measure:
        try:
            tools = atac_checks.Tools.find(args.chromap)
        except ValueError:
            if not args.measure_from:  # without chromap, only runs not reused fail later
                raise
    return atac.Options(
        cache=cache,
        measure=not args.no_measure,
        measure_from=args.measure_from,
        extra_runs=args.extra_runs,
        references=args.references,
        chromap_index=args.chromap_index,
        tools=tools,
        pairs=args.pairs,
    )


def run_build(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    chosen = species.load(args.species)
    cache = args.cache or args.out / "cache"
    archive = Archive(cache / "ena", offline=args.offline)
    if args.workflow == "tss-seq":
        summary = tss.build(chosen, archive, args.out, tss.options(args, cache), log=log)
        (args.out / "build.json").write_text(json.dumps(summary, indent=2) + "\n")
        return summary, 0
    if args.workflow == "scatac-reads":
        found = scatac.build(
            chosen,
            archive,
            args.out,
            scatac.Options(
                cache=cache,
                probe=not args.no_probe,
                vdb_dump=args.vdb_dump,
                extra_runs=args.extra_runs,
            ),
            log=log,
        )
        (args.out / "build.json").write_text(json.dumps(found, indent=2) + "\n")
        return found, 0
    if args.workflow == "dap-seq":
        options = dap.Options(cache, probe=not args.no_probe, skip_runs_in=args.skip_runs_in)
        summary = dap.build(chosen, archive, args.out, options, log=log)
        (args.out / "build.json").write_text(json.dumps(summary, indent=2) + "\n")
        return summary, 0
    if args.workflow == "atac-seq":
        summary = atac.build(chosen, archive, args.out, atac_options(args, cache), log=log)
        (args.out / "build.json").write_text(json.dumps(summary, indent=2) + "\n")
        return summary, 0
    tools = None
    if not args.no_measure:
        tools = Tools.find(args.star, args.featurecounts, args.gffread, args.threads)
    options = rna.Options(
        cache=cache,
        measure=not args.no_measure,
        strandedness_from=args.strandedness_from,
        references=args.references,
        star_index=args.star_index,
        tools=tools,
        probe_duplicates=not args.no_probe,
        adjudicate=adjudication_settings(args),
    )
    summary = rna.build(chosen, archive, args.out, options, log=log)
    (args.out / "build.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary, 0


def adjudication_settings(args: argparse.Namespace) -> adjudicate.Settings | None:
    runtime = getattr(args, "adjudicate", None)
    if runtime is None:
        return None
    if args.workflow != rna.WORKFLOW:
        # criteria exist for more workflows (for `genesis eval llm`), but only the RNA-seq
        # builder applies the review so far
        raise ValueError("--adjudicate supports rna-seq only")
    return adjudicate.Settings(
        runtime,
        getattr(args, "adjudicate_provider", None),
        args.adjudicate_limit,
        getattr(args, "adjudicate_scope", "title"),
    )


def build_namespace(**values: object) -> argparse.Namespace:
    """`build` arguments with their defaults, overridden by `values`."""
    defaults: dict[str, object] = {
        "cache": None,
        "strandedness_from": None,
        "measure_from": None,
        "no_measure": True,
        "no_probe": False,
        "adjudicate": None,
        "adjudicate_provider": None,
        "adjudicate_limit": 2000,
        "adjudicate_scope": "title",
        "vdb_dump": None,
        "skip_runs_in": [],
        "offline": False,
        "references": None,
        "star_index": None,
        "star": None,
        "featurecounts": None,
        "gffread": None,
        "threads": 16,
        "extra_runs": None,
        "chromap": None,
        "chromap_index": None,
        "pairs": atac_checks.PAIRS,
    }
    return argparse.Namespace(**(defaults | values))


def run_campaign(
    args: argparse.Namespace, step: Callable[[int, str], None] | None = None
) -> tuple[dict[str, Any], int]:
    """Metadata-only builds of several workflows for one species, each scored against gold
    when the manifest has a gold set for it. Gold runs-qc tables, when given with
    --gold-dir, supply the read measurements a metadata-only build cannot make."""
    gold = (
        {
            entry["workflow"]: entry
            for entry in gold_entries(args.manifest)
            if entry["species"] == args.species
        }
        if args.gold_dir
        else {}
    )
    # Without --dap-skip-runs-in, skip the repository DAP sheets fetched with the gold.
    skip_runs_in = args.dap_skip_runs_in or [
        args.gold_dir / entry["sheet"]
        for entry in (gold_entries(args.manifest, "repository_sheets") if args.gold_dir else [])
        if entry["species"] == args.species and entry["workflow"] == "dap-seq"
    ]
    cache = args.cache or args.out / "cache"
    results: dict[str, Any] = {}
    failed = 0
    for number, workflow in enumerate(args.workflows):
        if step:
            step(number, workflow)
        out = args.out / workflow
        entry = gold.get(workflow) if args.gold_dir else None
        reuse = args.gold_dir / entry["runs_qc"] if entry else None
        build_args = build_namespace(
            workflow=workflow,
            species=args.species,
            out=out,
            cache=cache / workflow,
            offline=args.offline,
            no_probe=args.no_probe,
            strandedness_from=reuse if workflow == "rna-seq" else None,
            measure_from=reuse if workflow in ("tss-seq", "atac-seq") else None,
            extra_runs=getattr(args, workflow.replace("-seq", "_extra_runs"), None),
            skip_runs_in=skip_runs_in if workflow == "dap-seq" else [],
        )
        log(f"campaign: {args.species} {workflow}")
        try:
            summary, _ = run_build(build_args)
        except Exception as error:  # one workflow failing must not stop the others
            failed += 1
            results[workflow] = {"error": f"{type(error).__name__}: {error}"}
            log(f"campaign: {workflow} failed: {error}")
            continue
        item: dict[str, Any] = {
            "in_sheet": summary.get("in_sheet"),
            "runs": summary.get("runs"),
            "sheet": str(out / f"{workflow}.tsv") if (out / f"{workflow}.tsv").exists() else None,
        }
        if entry:
            score_args = argparse.Namespace(
                workflow=workflow,
                species=args.species,
                candidate_sheet=out / f"{workflow}.tsv",
                candidate_qc=out / f"{workflow}.runs-qc.tsv",
                gold_sheet=None,
                gold_qc=None,
                gold_dir=args.gold_dir,
                manifest=args.manifest,
                traps=args.traps,
                history=args.history,
                label=f"{args.label} ({workflow})" if args.label else None,
            )
            result, _ = run_score(score_args)
            (out / "score.json").write_text(json.dumps(result, indent=2) + "\n")
            traps = result.get("traps") or {}
            item |= {
                "precision": result["precision"],
                "recall": result["recall"],
                "traps": f"{traps.get('passed', 0)}/{traps.get('checked', 0)}",
            }
        results[workflow] = item
    report = {"species": args.species, "workflows": results}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "campaign.json").write_text(json.dumps(report, indent=2) + "\n")
    return report, 1 if failed else 0


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def configure(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="operation", required=True)
    gold = commands.add_parser("gold-fetch", help="Download the gold sheets listed in a manifest")
    gold.add_argument(
        "--manifest", type=Path, default=EVALS / "gold.yaml", help="Default: evals/gold.yaml"
    )
    gold.add_argument("--to", type=Path, required=True, help="Directory to download into")
    gold.set_defaults(curation_handler=fetch)

    build = commands.add_parser(
        "build", help="Build a sample sheet and runs-qc table for one species from ENA/SRA"
    )
    build.add_argument("--workflow", required=True, choices=sorted(BUILDERS))
    build.add_argument("--species", required=True, help="Folder name, e.g. Sorghum_bicolor")
    build.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output folder for the sheet, runs-qc and build.json",
    )
    build.add_argument("--cache", type=Path, help="ENA and measurement cache (default OUT/cache)")
    build.add_argument(
        "--strandedness-from", type=Path, help="Reuse calls from a TSV with run, strandedness"
    )
    build.add_argument(
        "--measure-from",
        type=Path,
        help="tss-seq, atac-seq: reuse read measurements from a runs-qc TSV",
    )
    build.add_argument(
        "--no-measure", action="store_true", help="Leave runs without a call out, unmeasured"
    )
    build.add_argument(
        "--no-probe",
        action="store_true",
        help="Skip read probes (rna-seq duplicate reads; scatac-reads instruments; "
        "dap-seq read lengths)",
    )
    build.add_argument("--vdb-dump", help="vdb-dump executable (default: on PATH)")
    build.add_argument(
        "--skip-runs-in",
        type=Path,
        action="append",
        default=[],
        help="dap-seq: a sheet whose studies are left out (every run of a study with a run "
        "in it: 'in repository sheet NAME'); repeatable",
    )
    build.add_argument("--offline", action="store_true", help="Use only cached ENA responses")
    build.add_argument("--references", type=Path, help="Folder with the species' references")
    build.add_argument("--star-index", type=Path, help="Default: REFERENCES/<fasta>.<gff>.star")
    build.add_argument("--star", help="STAR executable (default: on PATH)")
    build.add_argument("--featurecounts", help="featureCounts executable (default: on PATH)")
    build.add_argument("--gffread", help="gffread executable (default: on PATH)")
    build.add_argument("--threads", type=int, default=16, help="Threads for read measurements")
    review = build.add_argument_group("metadata review (rna-seq)")
    review.add_argument(
        "--adjudicate",
        type=Path,
        metavar="RUNTIME",
        help="Ask the runtime's model provider to decide runs that rest on free-text metadata",
    )
    review.add_argument("--adjudicate-provider", help="Provider ID (default: the runtime's)")
    review.add_argument(
        "--adjudicate-limit",
        type=int,
        default=2000,
        help="Refuse to start if more runs than this would be sent (default 2000)",
    )
    review.add_argument(
        "--adjudicate-scope",
        choices=("title", "alias"),
        default="title",
        help="title: every kept run and every run the title rule left out; alias: only kept "
        "runs whose sample alias lacks the wild-type marker other aliases of the study carry",
    )
    build.add_argument(
        "--extra-runs",
        type=Path,
        help="atac-seq, tss-seq, scatac-seq: more candidate runs, from a metadata-sheet tab "
        "export (TSV with species and run_accessions, sra_runs or experiment_accession)",
    )
    atac_group = build.add_argument_group("atac-seq")
    atac_group.add_argument("--chromap", help="chromap executable (default: on PATH)")
    atac_group.add_argument("--chromap-index", type=Path, help="Default: built into the cache")
    atac_group.add_argument(
        "--pairs", type=int, default=atac_checks.PAIRS, help="Read pairs sampled per run"
    )
    build.set_defaults(curation_handler=run_build)

    campaign = commands.add_parser(
        "campaign",
        help="Metadata-only builds of several workflows for one species, scored against gold",
    )
    campaign.add_argument("--species", required=True, help="Folder name, e.g. Sorghum_bicolor")
    campaign.add_argument(
        "--out", type=Path, required=True, help="Output folder; one subfolder per workflow"
    )
    campaign.add_argument(
        "--workflows",
        nargs="+",
        choices=sorted(BUILDERS),
        default=sorted(BUILDERS),
        help="Default: all",
    )
    campaign.add_argument(
        "--gold-dir",
        type=Path,
        help="Directory filled by gold-fetch: scores each workflow that has gold, and reuses "
        "its read measurements",
    )
    campaign.add_argument("--cache", type=Path, help="Default: OUT/cache")
    campaign.add_argument("--offline", action="store_true", help="Use only cached responses")
    campaign.add_argument("--no-probe", action="store_true", help="Skip read probes")
    for workflow in ("atac", "tss", "scatac"):
        campaign.add_argument(
            f"--{workflow}-extra-runs",
            type=Path,
            help=f"{workflow}-seq: more candidate runs (TSV, see build --extra-runs)",
        )
    campaign.add_argument(
        "--dap-skip-runs-in",
        type=Path,
        action="append",
        default=[],
        help="dap-seq: leave out studies in this sheet; repeatable. Default with --gold-dir: "
        "the repository sheets listed in the manifest",
    )
    campaign.add_argument(
        "--manifest", type=Path, default=EVALS / "gold.yaml", help="Default: evals/gold.yaml"
    )
    campaign.add_argument(
        "--traps", type=Path, default=EVALS / "traps.yaml", help="Default: evals/traps.yaml"
    )
    campaign.add_argument("--history", type=Path, help="Markdown table to append rows to")
    campaign.add_argument("--label", help="Candidate name for the history rows")
    campaign.set_defaults(curation_handler=run_campaign)

    run = commands.add_parser("score", help="Score a candidate sheet against gold and traps")
    run.add_argument("--workflow", required=True, choices=sorted(FIELDS))
    run.add_argument("--species", default="", help="Species folder name, e.g. Sorghum_bicolor")
    run.add_argument("--candidate-sheet", type=Path, required=True, help="The built sheet (TSV)")
    run.add_argument("--candidate-qc", type=Path, help="The built runs-qc table (TSV)")
    run.add_argument("--gold-sheet", type=Path, help="Overrides the manifest lookup")
    run.add_argument("--gold-qc", type=Path, help="Overrides the manifest lookup")
    run.add_argument("--gold-dir", type=Path, help="Directory filled by gold-fetch")
    run.add_argument(
        "--manifest", type=Path, default=EVALS / "gold.yaml", help="Default: evals/gold.yaml"
    )
    run.add_argument(
        "--traps", type=Path, default=EVALS / "traps.yaml", help="Default: evals/traps.yaml"
    )
    run.add_argument("--history", type=Path, help="Markdown table to append a summary row to")
    run.add_argument("--label", help="Candidate name for the history row")
    run.set_defaults(curation_handler=run_score)
