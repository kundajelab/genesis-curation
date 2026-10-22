"""Report synthetic held-out software metrics separately from biological curation accuracy."""

from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path
from typing import Any

from verify_ai import configuration

from genesis_curation.contracts.records import canonical, dump, load, now, record
from genesis_curation.curation.harmonize import extract, source_record, verify_fields
from genesis_curation.llm.providers import invoke
from genesis_curation.llm.security import schema
from genesis_curation.registry import store


def evaluate(path: Path) -> dict[str, Any]:
    gold = load(path)
    results = []
    with tempfile.TemporaryDirectory(prefix="genesis-gold-") as temporary:
        registry = Path(temporary)
        store.initialize(registry)
        for provider in ("deterministic", "mock"):
            correct = total = conflicts = abstentions = evidence_ok = 0
            started = time.monotonic()
            for case in gold["held_out"]:
                sources = [
                    source_record(
                        canonical(value), f"synthetic:{case['id']}:{i}", "application/json"
                    )
                    for i, value in enumerate(case["sources"])
                ]
                bundle = record(
                    "source_bundle",
                    {
                        "dataset_id": case["id"],
                        "manifest_version": "0" * 64,
                        "classification": "public",
                        "sources": sources,
                        "retrieved_at": now(),
                        "accessions": [],
                    },
                    case["id"],
                    version=2,
                )
                fields = extract(bundle, {})
                if provider == "mock":
                    invocation = invoke(
                        registry,
                        configuration(),
                        task="metadata",
                        data={"sources": sources},
                        classification="public",
                        source_hashes=[s["data"]["sha256"] for s in sources],
                        specification=schema("metadata-response-v1"),
                        mock={"fields": fields},
                    )
                    fields = invocation["data"]["response"]["fields"]
                verify_fields(bundle, fields)
                actual = {f["field"]: f for f in fields}
                for name, expected in case["expected"].items():
                    total += 1
                    correct += all(actual[name][key] == value for key, value in expected.items())
                    conflicts += actual[name]["status"] == "conflicting"
                    abstentions += actual[name]["status"] == "unknown"
                evidence_ok += 1
            results.append(
                {
                    "provider": provider,
                    "cases": len(gold["held_out"]),
                    "fields": total,
                    "correct_fields": correct,
                    "field_accuracy": correct / total,
                    "evidence_valid_cases": evidence_ok,
                    "detected_conflicts": conflicts,
                    "abstentions": abstentions,
                    "seconds": time.monotonic() - started,
                    "reported_tokens": None,
                    "human_review_corrections": None,
                }
            )
            if correct != total:
                raise ValueError("Synthetic held-out metadata expectations failed")
    return {
        "status": "PASS",
        "evaluation": "synthetic software contracts",
        "gold_provenance": gold["provenance"],
        "development_cases": len(gold["development"]),
        "results": results,
        "biological_accuracy": "NOT EVALUATED",
        "real_provider_comparison": "NOT RUN",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--gold", type=Path, default=Path(__file__).parent / "fixtures/curation/metadata-gold.json"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate(args.gold)
    if args.output:
        dump(args.output, result)
    print(canonical(result))


if __name__ == "__main__":
    main()
