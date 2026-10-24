"""Opt-in study workflow evaluation; never claims independent biological accuracy."""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

from genesis_curation.contracts.records import canonical, dump, fingerprint, load
from genesis_curation.llm import providers
from genesis_curation.registry import store
from genesis_curation.web import catalog, intake, preparation, studies


def evaluate(
    root: Path, config_path: Path, provider: str, manifest: Path | None = None
) -> dict[str, Any]:
    if root.exists():
        raise ValueError("Use a new evaluation directory")
    root.mkdir(parents=True, mode=0o700)
    registry = root / "catalog"
    store.initialize(registry)
    config = providers.select(config_path, provider)
    report: dict[str, Any] = {
        "provider": provider,
        "model": config["model"],
        "studies": [],
        "biological_accuracy": "NOT_INDEPENDENTLY_VALIDATED",
        "sequencing_downloads": "NOT_RUN",
    }
    if manifest:
        cohort = load(manifest)["studies"]
    else:
        cohort = [{"accession": None, "split": "synthetic", "assay": "RNA-seq"}]
    for item in cohort:
        started = time.monotonic()
        entry: dict[str, Any] = dict(item)
        try:
            if item["accession"]:
                key = intake.import_accession(
                    root, item["accession"], "evaluation-script", sample_limit=5
                )
            else:
                ids = catalog.import_rows(
                    registry,
                    canonical(
                        [
                            {
                                "id": "explicit",
                                "organism": "Arabidopsis thaliana",
                                "tissue": "leaf",
                                "assay": "RNA-seq",
                            },
                            {
                                "id": "missing",
                                "organism": "Oryza sativa",
                                "assay": "RNA-seq",
                                "description": "The tissue source is not documented.",
                            },
                        ]
                    ),
                    "synthetic.json",
                    "synthetic-live-study",
                    "id",
                    "public",
                    "evaluation-script",
                )
                key = studies.link(registry, ids, "Synthetic live acceptance", "evaluation-script")
            preparation.run(
                root,
                config_path,
                {
                    "actor": "evaluation-script",
                    "payload": {
                        "study": key,
                        "provider": provider,
                        "provider_hash": fingerprint(config),
                    },
                },
            )
            current = studies.get(registry, key)
            entry.update(
                study=key,
                records=current["member_count"],
                failures=current["preparation"]["failures"],
                pending_decisions=current["pending"],
                supported_fields=sum(
                    f["status"] == "known" for r in current["records"] for f in r["fields"]
                ),
                coverage=[
                    r.get("preparation_provenance", {}).get("coverage") for r in current["records"]
                ],
                selection=current["preparation"].get("selection"),
            )
            if not manifest:
                assert not current["preparation"]["failures"], entry
                assert all(not r["reviews"] for r in current["records"])
                missing = next(r for r in current["records"] if r["name"] == "missing")
                assert next(f for f in missing["fields"] if f["field"] == "tissue")["value"] is None
                # Scripted decisions exercise the software boundary only, in this isolated registry.
                for task in current["tasks"]:
                    if task["status"] != "done":
                        studies.resolve(
                            registry,
                            task["id"],
                            {
                                "study": key,
                                "version": task["version"],
                                "action": "unknown",
                                "reason": "Synthetic acceptance fixture: preserve missing evidence",
                            },
                            "evaluation-script",
                        )
                current = studies.get(registry, key)
                release = studies.approve(registry, key, current["version"], "evaluation-script")
                assert (
                    "tissue"
                    not in next(r for r in release["records"] if r["name"] == "missing")["metadata"]
                )
                dump(root / "synthetic-release.json", release)
                entry["workflow"] = "PASS"
            else:
                entry["workflow"] = "PREPARED_WITH_FAILURES" if entry["failures"] else "PREPARED"
        except Exception as exc:
            entry.update(workflow="FAIL", error=str(exc))
        entry["elapsed_seconds"] = round(time.monotonic() - started, 2)
        report["studies"].append(entry)
        dump(root / "evaluation.json", report)
        print(
            canonical({k: entry[k] for k in ("accession", "workflow", "elapsed_seconds")}),
            flush=True,
        )
    return report


def retry_failures(root: Path, config_path: Path, provider: str) -> dict[str, Any]:
    """Retry only failed studies, preserving the previous result and cached sources."""
    report = load(root / "evaluation.json")
    config = providers.select(config_path, provider)
    if report["provider"] != provider or report["model"] != config["model"]:
        raise ValueError("Retry requires the original provider and model")
    for entry in report["studies"]:
        if entry["workflow"] not in {"FAIL", "PREPARED_WITH_FAILURES"} or not entry.get("study"):
            continue
        started = time.monotonic()
        entry.setdefault("previous_attempts", []).append(
            {
                "workflow": entry["workflow"],
                "failures": entry.get("failures", {}),
                "elapsed_seconds": entry["elapsed_seconds"],
            }
        )
        preparation.run(
            root,
            config_path,
            {
                "actor": "evaluation-script",
                "payload": {
                    "study": entry["study"],
                    "provider": provider,
                    "provider_hash": fingerprint(config),
                },
            },
        )
        current = studies.get(root / "catalog", entry["study"])
        assert all(not record["reviews"] for record in current["records"])
        entry.update(
            failures=current["preparation"]["failures"],
            pending_decisions=current["pending"],
            supported_fields=sum(
                f["status"] == "known" for r in current["records"] for f in r["fields"]
            ),
            coverage=[
                r.get("preparation_provenance", {}).get("coverage") for r in current["records"]
            ],
            elapsed_seconds=round(time.monotonic() - started, 2),
        )
        entry["workflow"] = "PREPARED_WITH_FAILURES" if entry["failures"] else "PREPARED"
        dump(root / "evaluation.json", report)
        print(
            canonical({k: entry[k] for k in ("accession", "workflow", "elapsed_seconds")}),
            flush=True,
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--providers", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--public-manifest", type=Path)
    parser.add_argument("--retry-failures", action="store_true")
    args = parser.parse_args()
    result = (
        retry_failures(args.root, args.providers, args.provider)
        if args.retry_failures
        else evaluate(args.root, args.providers, args.provider, args.public_manifest)
    )
    if any(s["workflow"] in {"FAIL", "PREPARED_WITH_FAILURES"} for s in result["studies"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
