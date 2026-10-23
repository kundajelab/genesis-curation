"""Opt-in synthetic gold evaluation of a live provider with and without curation guidance."""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

from genesis_curation.contracts.records import canonical, dump
from genesis_curation.llm import providers
from genesis_curation.registry import store
from genesis_curation.web import catalog, skills

CASES: list[dict[str, Any]] = [
    {
        "id": "explicit",
        "row": {"organism": "Arabidopsis thaliana", "tissue": "leaf"},
        "gold": {"organism": ("known", "Arabidopsis thaliana"), "tissue": ("known", "leaf")},
    },
    {
        "id": "prose",
        "row": {
            "description": "Arabidopsis thaliana plants were grown for 14 days. "
            "Roots were collected for this sample."
        },
        "gold": {"organism": ("known", "Arabidopsis thaliana"), "tissue": ("known", "root")},
    },
    {
        "id": "missing",
        "row": {"title": "Pilot sequencing library; biological source was not recorded."},
        "gold": {"organism": ("unknown", None), "tissue": ("unknown", None)},
    },
    {
        "id": "conflict",
        "row": {"organism": "Arabidopsis thaliana", "tissue": "leaf"},
        "evidence": "tissue: root",
        "gold": {"organism": ("known", "Arabidopsis thaliana"), "tissue": ("conflicting", None)},
    },
]


def evaluate(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    if root.exists():
        raise ValueError("Use a new evaluation directory to keep runs independent")
    store.initialize(root)
    results = []
    for guidance in ("none", "plant-metadata"):
        for case in CASES:
            subject = catalog.import_rows(
                root,
                canonical([{"id": case["id"], **case["row"]}]),
                "synthetic.json",
                guidance,
                "id",
                "public",
                "evaluation",
            )[0]
            state = catalog.get(root, subject)
            if case.get("evidence"):
                state = catalog.attach(
                    root,
                    subject,
                    state["version"],
                    case["evidence"],
                    "synthetic:conflict",
                    "public",
                    "evaluation",
                )
            start = time.monotonic()
            result: dict[str, Any] = {"case": case["id"], "skill": guidance, "gold": case["gold"]}
            try:
                state = catalog.run_proposal(
                    root,
                    subject,
                    state["version"],
                    config,
                    skills.select(root / "skills", guidance),
                    "evaluation",
                )
                fields = {f["field"]: f for f in state["fields"]}
                matches = {
                    k: fields[k]["status"] == status and fields[k]["value"] == value
                    for k, (status, value) in case["gold"].items()
                }
                result.update(
                    status="SUCCEEDED",
                    matches=matches,
                    fields=state["fields"],
                    invocation=state["proposal"],
                    approved_fields=len(state["reviews"]),
                )
                assert not state["reviews"], "Inference must never approve metadata"
            except ValueError as error:
                result.update(status="FAILED", error=str(error))
            result["seconds"] = round(time.monotonic() - start, 3)
            results.append(result)
            print(canonical({k: v for k, v in result.items() if k != "fields"}), flush=True)
    return {
        "kind": "synthetic-metadata-comparison",
        "provider": config["id"],
        "model": config["model"],
        "model_revision": config["model_revision"],
        "results": results,
        "limits": "Four synthetic cases, no biological accuracy or human correction-rate claim.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--providers", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.root, providers.select(args.providers, args.provider))
    dump(args.output, result, immutable=True)


if __name__ == "__main__":
    main()
