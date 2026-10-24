"""Opt-in local table benchmark from frozen metadata; never writes the source registry."""

from __future__ import annotations

import argparse
import contextlib
import copy
import shutil
import time
from pathlib import Path
from typing import Any

from genesis_curation.contracts.records import dump, fingerprint, load
from genesis_curation.curation import harmonize
from genesis_curation.llm import providers
from genesis_curation.registry import store
from genesis_curation.web import catalog, preparation, studies


def evaluate(
    root: Path, snapshot: Path, configs: Path, skills: Path, provider: str, context: int, limit: int
) -> dict[str, Any]:
    if root.exists():
        raise ValueError("Evaluation requires a new isolated directory")
    root.mkdir(parents=True, mode=0o700)
    shutil.copytree(skills, root / "skills")
    config = providers.select(configs, provider)
    config.update(
        context_window_tokens=context,
        max_input_tokens=context,
        max_tokens=min(8192, max(4096, context // 4)),
        token_counter="llama",
    )
    config_path = root / "providers.json"
    dump(config_path, {"schema_version": 1, "providers": [config]})
    registry = root / "catalog"
    store.initialize(registry)
    frozen = load(snapshot)
    rows = frozen["records"][:limit] if limit else frozen["records"]
    baseline = {r["id"]: r for r in rows}
    with store.write(registry) as db:
        for original in rows:
            state = copy.deepcopy(original)
            state.update(metadata={}, reviews={}, recommendations={}, proposal=None, messages=[])
            state.pop("preparation_provenance", None)
            state["fields"] = harmonize.extract(
                {
                    "data": {
                        "sources": [
                            s
                            for s in state["sources"]
                            if state.get("source_scopes", {}).get(s["version"]) != "study_context"
                        ]
                    }
                },
                {},
            )
            for source in state["sources"]:
                store.put(db, source)
            catalog.save(db, state, "import", "table-evaluation", {})
    key = studies.link(registry, list(baseline), frozen["name"], "table-evaluation")
    started = time.monotonic()
    result = preparation.run(
        root,
        config_path,
        {
            "actor": "table-evaluation",
            "payload": {"study": key, "provider": provider, "provider_hash": fingerprint(config)},
        },
    )
    elapsed = time.monotonic() - started
    study = studies.get(registry, key)
    differences = []
    required_values_lost = []
    for state in study["records"]:
        before = {
            f["field"]: f["value"]
            for f in baseline[state["id"]]["fields"]
            if f["status"] == "known"
        }
        after = {f["field"]: f["value"] for f in state["fields"] if f["status"] == "known"}
        required_values_lost.extend(
            {"sample": state["name"], "field": field}
            for field in studies.required_fields(baseline[state["id"]])
            if before.get(field) is not None and after.get(field) is None
        )
        for field in sorted(before.keys() | after.keys()):
            if before.get(field) != after.get(field):
                differences.append(
                    {
                        "sample": state["name"],
                        "field": field,
                        "before": before.get(field),
                        "after": after.get(field),
                    }
                )
    with contextlib.closing(store.connect(registry)) as db:
        from genesis_curation.contracts.records import loads

        invocations = [
            loads(r[0])["data"]
            for r in db.execute("SELECT body FROM records WHERE kind='invocation'")
        ]
    measured = {
        "invocations": len(invocations),
        "http_attempts": sum(len(v["attempts"]) for v in invocations),
        "failed_invocations": sum(v["status"] != "SUCCEEDED" for v in invocations),
        "usage": {
            name: sum(v["usage"].get(name) or 0 for v in invocations)
            for name in ["prompt_tokens", "completion_tokens", "total_tokens"]
        },
    }
    report = {
        "measured": measured,
        "context": context,
        "rows": len(rows),
        "elapsed_seconds": elapsed,
        "result": result,
        "progress": studies.progress(registry, key),
        "failures": study["preparation"]["failures"],
        "differences": differences,
        "required_values_lost_vs_baseline": required_values_lost,
        "pending_decisions": study["pending"],
        "baseline": frozen.get("baseline"),
        "accuracy": "NOT_INDEPENDENTLY_VALIDATED",
        "sequencing_downloads": "NOT_RUN",
    }
    dump(root / "report.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["root", "snapshot", "configs", "skills"]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--context", type=int, default=8192)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    outcome = evaluate(
        args.root, args.snapshot, args.configs, args.skills, args.provider, args.context, args.limit
    )
    print(
        {
            k: outcome[k]
            for k in [
                "context",
                "rows",
                "elapsed_seconds",
                "result",
                "failures",
                "pending_decisions",
            ]
        }
    )
