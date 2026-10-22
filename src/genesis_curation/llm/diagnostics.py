"""Bounded evidence and deterministic diagnosis; this module never retries a job."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from ..contracts.records import canonical, fingerprint, identity, now, record
from ..execution import transport
from ..registry import store
from . import providers
from .security import check, redact, schema

LOG_NAMES = {
    ".command.err",
    ".command.log",
    ".command.out",
    ".nextflow.log",
    "stdout.log",
    "stderr.log",
    "supervisor.log",
    "trace.txt",
    "trace.tsv",
    "status.json",
}
MAX_LOG = 65536
MAX_FILES = 16


def collect(root: Path, paths: list[str], worker: str) -> dict[str, Any]:
    base = root.resolve(strict=True)
    if not 1 <= len(paths) <= MAX_FILES:
        raise ValueError("Select 1–16 evidence files explicitly")
    logs = []
    for relative in paths:
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("Diagnostic paths must be relative to the approved run root")
        path = (base / relative).resolve(strict=True)
        if not path.is_relative_to(base) or path.name not in LOG_NAMES or not path.is_file():
            raise ValueError(
                "Diagnostic evidence must be an approved log under the declared run root"
            )
        with path.open("rb") as stream:
            size = path.stat().st_size
            stream.seek(max(0, size - MAX_LOG))
            content = stream.read(MAX_LOG)
        # Hash the exact retained redacted snapshot. Original byte sizes are observations, not
        # hashes
        # of uncollected full files, and process I/O is never reported as network traffic.
        text = redact(content.decode("utf-8", errors="replace"))
        digest = hashlib.sha256(text.encode()).hexdigest()
        logs.append(
            {
                "path": str(path),
                "worker": worker,
                "sha256": digest,
                "text": text,
                "bytes_total": size,
                "partial": size > MAX_LOG,
            }
        )
    return {"schema_version": 1, "root": str(base), "worker": worker, "logs": logs}


def classify(bundle: dict[str, Any], facts: dict[str, Any]) -> tuple[str, bool, str]:
    text = "\n".join(item["text"] for item in bundle["logs"]).lower()
    if facts.get("worker_state") in {"RUNNING", "UNKNOWN", "STARTING"}:
        return "unknown", False, "inspect"
    if facts.get("oom_kill") is True and facts.get("resource_evidence"):
        return "resource-exhaustion", True, "propose-resource-change"
    # Log signatures are evidence-backed classifications, not proof of the underlying cause.
    rules = (
        (r"checksum mismatch|sha256 mismatch", "checksum-mismatch", "reacquire-and-verify"),
        (
            r"permission denied|unauthorized|forbidden|authentication failed",
            "authentication-access",
            "renew-auth-manually",
        ),
        (r"no such file or directory|missing input", "missing-input", "inspect"),
        (
            r"malformed manifest|invalid samplesheet|invalid sample sheet",
            "malformed-manifest",
            "propose-metadata-correction",
        ),
        (r"reference mismatch|contig.*mismatch", "reference-inconsistency", "escalate"),
        (
            r"temporary failure in name resolution|connection timed out|temporarily unavailable",
            "transient-network",
            "retry-same-config",
        ),
    )
    for pattern, category, action in rules:
        if re.search(pattern, text):
            return category, True, action
    if facts.get("qc_concern") is True:
        return "biological-qc-concern", True, "escalate"
    if facts.get("exit_code") == 75:
        return "transient-exit-75", True, "retry-same-config"
    if facts.get("tool_exception") is True:
        return "tool-defect", True, "escalate"
    return "unknown", False, "inspect"


def diagnose(
    directory: Path,
    bundle: dict[str, Any],
    *,
    facts: dict[str, Any],
    config: dict[str, Any] | None = None,
    dataset: str | None = None,
    classification: str = "local-only",
) -> dict[str, Any]:
    check(facts, schema("diagnostic-facts-v1"))
    if set(facts) - {
        "worker_state",
        "exit_code",
        "oom_kill",
        "resource_evidence",
        "qc_concern",
        "tool_exception",
        "campaign_hash",
        "job",
        "attempt",
    }:
        raise ValueError("Unknown diagnostic facts")
    if facts.get("oom_kill") and facts.get("resource_evidence") not in {
        item["sha256"] for item in bundle["logs"]
    }:
        raise ValueError("Confirmed OOM requires a collected resource evidence hash")
    if facts.get("oom_kill") is True:
        evidence_log = next(
            item for item in bundle["logs"] if item["sha256"] == facts["resource_evidence"]
        )
        if not re.search(r"(?i)oom[_ -]?kill|out of memory|oomkilled", evidence_log["text"]):
            raise ValueError("Resource evidence does not contain a measured OOM event")
    category, confirmed, action = classify(bundle, facts)
    hashes = [item["sha256"] for item in bundle["logs"]]
    evidence = [
        {
            "location": item["path"],
            "sha256": item["sha256"],
            "worker": item["worker"],
            "locator": "$",
        }
        for item in bundle["logs"]
    ]
    explanation = (
        "Deterministic category: "
        + category
        + ". Inspect the retained evidence before taking action."
    )
    hypotheses: list[str] = []
    invocation = None
    if config:
        try:
            invocation = providers.invoke(
                directory,
                config,
                task="diagnose",
                data={"evidence": bundle, "facts": facts, "category": category},
                classification=classification,
                source_hashes=hashes,
                specification=schema("diagnosis-response-v1"),
                mock={"explanation": explanation, "hypotheses": [], "citations": hashes},
                dataset=dataset,
            )
            answer = invocation["data"]["response"]
            if not set(answer["citations"]).issubset(hashes):
                raise ValueError("Diagnosis cites uncollected evidence")
            explanation, hypotheses = answer["explanation"], answer["hypotheses"]
        except providers.InferenceFailure as error:
            invocation = error.invocation
            hypotheses = [
                "Optional model explanation unavailable; deterministic diagnosis retained"
            ]
    data = {
        "dataset_id": dataset,
        "target_version": fingerprint({"bundle": bundle, "facts": facts}),
        "category": category,
        "confirmed": confirmed,
        "evidence": evidence,
        "facts": facts,
        "explanation": explanation,
        "hypotheses": hypotheses,
        "action": action,
        "preconditions": [
            "Recollect evidence and compare target_version",
            "Reconcile UNKNOWN/running workers before any resubmission",
            "Use existing bounded retry policy or an explicitly approved replacement campaign",
        ],
        "expected_effect": "Provide a reviewable next step; no job or scientific "
        "configuration is changed",
        "human_review_required": True,
        "invocation": invocation["version"] if invocation else None,
        "created": now(),
    }
    result = record("diagnosis", data, identity("diagnosis", fingerprint(data)), version=2)
    with store.write(directory) as db:
        for item in bundle["logs"]:
            source = record(
                "source",
                {
                    "location": item["path"],
                    "worker": item["worker"],
                    "sha256": item["sha256"],
                    "snapshot": item["text"],
                    "media_type": "text/plain",
                },
                identity("diagnostic-log", item["worker"], item["path"], item["sha256"]),
            )
            store.put(db, source, dataset)
        store.put(db, result, dataset)
    return result


def remote(worker: dict[str, Any], root: Path, paths: list[str], worker_id: str) -> dict[str, Any]:
    # Explicit approved roots are node configuration, never supplied by model output.
    roots = worker.get("diagnostic_roots", [worker["root"]])
    if not any(root.is_relative_to(Path(p)) for p in roots):
        raise ValueError("Run root is outside approved node diagnostic roots")
    return transport.request(
        worker, __name__, "collect", root, {"paths": paths, "worker": worker_id}
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("collect",))
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    payload = json.load(sys.stdin)
    print(canonical(collect(args.root, payload["paths"], payload["worker"])))


if __name__ == "__main__":
    main()
