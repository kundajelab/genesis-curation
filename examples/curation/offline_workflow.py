"""Run the README's full offline CLI recipe on two new synthetic datasets only."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

from create_demo import create

from genesis_curation.contracts.records import dump, load, loads
from genesis_curation.llm.providers import select


def cli(*arguments: str) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "genesis_curation.execution.controller", *arguments],
        text=True,
        capture_output=True,
        check=True,
    )
    return loads(result.stdout)


def run(root: Path, providers: Path) -> dict[str, Any]:
    if select(providers, "mock")["mode"] != "mock":
        raise ValueError("The offline tutorial accepts only a deterministic mock provider")
    create(root)
    registry = str(root / "catalog")
    cli("registry", "init", registry)
    cli("registry", "import", registry, "--bundle", str(root / "bundle.json"))
    ids = load(root / "datasets.json")["datasets"]
    decisions = []
    for dataset in ids:
        cli(
            "metadata",
            "ingest",
            registry,
            dataset,
            "--source",
            str(root / "source.json"),
            "--classification",
            "local-only",
        )
        proposal = cli(
            "metadata",
            "propose",
            registry,
            dataset,
            "--config",
            str(providers),
            "--provider",
            "mock",
        )
        evidence = root / (dataset + "-evidence.json")
        dump(evidence, {"evidence": proposal["data"]["evidence"]})
        token = cli("review", "show", registry, dataset)["status"]["token"]
        decisions.append(
            cli(
                "review",
                "approve",
                registry,
                dataset,
                "--category",
                "metadata",
                "--target",
                proposal["version"],
                "--token",
                token,
                "--reason",
                "Synthetic tutorial fixture only; automated demonstration "
                "is not scientific acceptance",
                "--evidence",
                str(evidence),
            )
        )
        cli(
            "metadata",
            "apply",
            registry,
            dataset,
            "--target",
            proposal["version"],
            "--output",
            str(root / (dataset + "-metadata.json")),
        )
        cli("qc", "evaluate", registry, dataset)
        for category in ("qc", "eligibility"):
            token = cli("review", "show", registry, dataset)["status"]["token"]
            decisions.append(
                cli(
                    "review",
                    "approve",
                    registry,
                    dataset,
                    "--category",
                    category,
                    "--token",
                    token,
                    "--reason",
                    "Explicit synthetic tutorial selection; biological QC is not established",
                    "--evidence",
                    str(evidence),
                )
            )
    cli(
        "registry",
        "search",
        registry,
        "--filter",
        "study=synthetic-study",
        "--selection-output",
        str(root / "selection.json"),
    )
    cli(
        "registry",
        "export",
        registry,
        "--selection",
        str(root / "selection.json"),
        "--reviewed",
        "--output",
        str(root / "reviewed.json"),
    )
    cli("ai", "providers", "check", registry, "--config", str(providers), "--provider", "mock")
    cli(
        "ai",
        "ask",
        registry,
        "Summarize recorded metadata",
        "--dataset",
        ids[0],
        "--config",
        str(providers),
        "--provider",
        "mock",
    )
    cli(
        "ai",
        "diagnose",
        registry,
        "--root",
        str(root),
        "--log",
        "stderr.log",
        "--dataset",
        ids[0],
        "--output",
        str(root / "diagnosis.json"),
    )
    cli("registry", "backup", registry, "--output", str(root / "backup.sqlite"))
    cli("registry", "restore", str(root / "restored"), "--backup", str(root / "backup.sqlite"))
    exported = load(root / "reviewed.json")
    if exported["denominators"] != {"candidates": 2, "included": 2, "excluded": 0}:
        raise ValueError("Synthetic reviewed export did not contain exactly the selected fixtures")
    result = {
        "status": "PASS",
        "datasets": ids,
        "denominators": exported["denominators"],
        "decisions": len(decisions),
        "pipeline_execution": "NOT RUN",
        "live_inference": "NOT RUN",
    }
    dump(root / "acceptance.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path, help="New empty synthetic fixture directory")
    parser.add_argument(
        "--providers", type=Path, default=Path(__file__).with_name("providers.mock.json")
    )
    args = parser.parse_args()
    print(run(args.directory.resolve(), args.providers.resolve()))


if __name__ == "__main__":
    main()
