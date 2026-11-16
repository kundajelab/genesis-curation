"""Shared RNA-seq archive → annotation → targeted literature → review sequence."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from ..contracts.records import fingerprint, now
from ..llm import progress, providers
from . import assessments, constraints, papers, preparation, studies, taxonomy

VERSION = "rna-evidence/1"


def run(
    root: Path, config_path: Path, task: dict[str, Any], cancellation: threading.Event | None = None
) -> dict[str, Any]:
    while True:
        result = step(root, config_path, task, cancellation)
        if not result.get("pending") or task.get("id"):
            return result


def step(
    root: Path, config_path: Path, task: dict[str, Any], cancellation: threading.Event | None = None
) -> dict[str, Any]:
    data, registry = task["payload"], root / "catalog"
    current = studies.get(registry, data["study"])
    requested = {
        r["id"]: constraints.validate(
            data.get("constraints") or r["original"].get("_constraints", {})
        )
        for r in current["records"]
    }
    is_rna = any(
        "rna" in str(f["value"]).casefold()
        for r in current["records"]
        for f in r["fields"]
        if f["field"] == "assay"
    )
    if not is_rna or data.get("targets"):
        return preparation.annotate(root, config_path, task, cancellation)
    config = providers.select(config_path, data["provider"]) if data.get("provider") else None
    if not config or config["mode"] == "mock":
        result = preparation.annotate(root, config_path, task, cancellation)
        if not result.get("pending") and not result.get("cancelled"):
            snapshot = studies.get(registry, current["id"])
            summary = counts(snapshot["records"])
            studies.preparation(
                registry,
                current["id"],
                {
                    "workflow": {
                        "version": VERSION,
                        "stage": "complete",
                        "initial": summary,
                        "final": summary,
                        "papers": {"status": "NOT_RUN", "reason": "No live provider selected"},
                        "constraints": {
                            r["id"]: constraints.assess(r, requested[r["id"]])
                            for r in snapshot["records"]
                            if requested[r["id"]]
                        },
                    }
                },
            )
        return result
    key = fingerprint([VERSION, config, requested])
    flow: dict[str, Any] = current["preparation"].get("workflow", {})
    versions = {r["id"]: r["version"] for r in current["records"]}
    if flow.get("key") != key or (
        flow.get("stage") == "complete"
        and (flow.get("versions") != versions or data.get("restart"))
    ):
        flow = {
            "key": key,
            "version": VERSION,
            "stage": "archive-annotation",
            "started": now(),
            "initial": {},
            "papers": {},
            "events": [],
        }
    if cancellation and cancellation.is_set():
        return {"study": current["id"], "cancelled": True}
    studies.preparation(registry, current["id"], {"workflow": flow})
    phase = flow["stage"]
    progress.report(phase=phase.replace("-", " ").capitalize(), study=current["name"])
    if phase in {"archive-annotation", "paper-annotation"}:
        result = preparation.annotate(root, config_path, task, cancellation)
        if result.get("pending") or result.get("cancelled"):
            studies.preparation(registry, current["id"], {"workflow": flow})
            return result
        current = studies.get(registry, current["id"])
        if phase == "archive-annotation":
            flow["initial"] = counts(current["records"])
            flow["archive_versions"] = {r["id"]: r["version"] for r in current["records"]}
            flow["stage"] = "paper-retrieval"
        else:
            flow["stage"] = "validation"
    elif phase == "paper-retrieval":
        if not config or config["mode"] == "mock":
            flow["papers"] = {
                "status": "NOT_RUN",
                "reason": "No live provider selected",
                "changed": [],
            }
        else:
            flow["papers"] = papers.enrich(root, current, task["actor"], cancellation)
        # An interruption can occur after evidence attaches but before the checkpoint.
        refreshed = studies.get(registry, current["id"])
        changed = set(flow["papers"]["changed"])
        changed.update(
            r["id"]
            for r in refreshed["records"]
            if flow.get("archive_versions", {}).get(r["id"]) != r["version"]
        )
        flow["papers"]["changed"] = sorted(changed)
        flow["stage"] = "paper-annotation" if flow["papers"]["changed"] else "validation"
    elif phase == "validation":
        flow["taxonomy"] = taxonomy.resolve(root, current["records"], task["actor"], cancellation)
        current = studies.get(registry, current["id"])
        versions = {r["id"]: r["version"] for r in current["records"]}
        flow["final"] = counts(current["records"])
        flow["constraints"] = {
            r["id"]: constraints.assess(r, requested[r["id"]])
            for r in current["records"]
            if requested[r["id"]]
        }
        flow.update(stage="complete", finished=now(), versions=versions)
    elif phase == "complete":
        return {
            "study": current["id"],
            "prepared": len(current["preparation"]["completed"]),
            "failed": len(current["preparation"]["failures"]),
            "workflow": flow,
        }
    flow["events"].append({"stage": phase, "finished": now()})
    studies.preparation(
        registry,
        current["id"],
        {"workflow": flow, "status": "COMPLETED" if flow["stage"] == "complete" else "RUNNING"},
    )
    return {
        "study": current["id"],
        "pending": flow["stage"] != "complete",
        "workflow_stage": flow["stage"],
    }


def counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    wanted = {
        "organism",
        "tissue",
        "genotype",
        "treatment",
        "developmental_stage",
        "library_selection",
    }
    fields = [f for r in rows for f in r["fields"] if f["field"] in wanted]
    checks = [a for r in rows for k, a in assessments.assess(r, r["fields"]).items() if k in wanted]
    return {
        "records": len(rows),
        "fields": len(fields),
        "supported": sum(
            a["support"] == "quoted_value" and not a["needs_verification"] for a in checks
        ),
        "unresolved": sum(f["status"] in {"unknown", "conflicting"} for f in fields),
    }
