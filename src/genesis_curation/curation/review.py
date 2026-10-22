"""Immutable decisions with exact evidence bindings and optimistic concurrency."""

from __future__ import annotations

import os
import pwd
import socket
import sqlite3
from pathlib import Path
from typing import Any

from ..contracts.records import (
    ReviewDecision,
    canonical,
    fingerprint,
    loads,
    now,
    validate,
)
from ..qc_policy import builtin
from ..registry import store

CATEGORIES = ("metadata", "qc", "eligibility")
DESCRIPTIVE = {
    "organism",
    "taxon",
    "genotype",
    "cultivar",
    "tissue",
    "developmental_stage",
    "treatment",
    "protocol",
    "biological_replicate",
    "notes",
}


def profile() -> dict[str, Any]:
    return builtin("reviewed-catalog-v1")


def latest(db: sqlite3.Connection, dataset: str, category: str) -> dict[str, Any] | None:
    row = db.execute(
        f"SELECT body FROM {store.decisions_table(db)} WHERE dataset=? AND category=? "
        "ORDER BY sequence DESC LIMIT 1",
        (dataset, category),
    ).fetchone()
    return loads(row[0]) if row else None


def bindings(
    db: sqlite3.Connection,
    dataset: str,
    category: str,
    export_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = store.current(db, dataset)
    result: dict[str, Any] = {
        "manifest": row["manifest"],
        "metadata": fingerprint(loads(row["metadata"])),
    }
    if db.execute("PRAGMA user_version").fetchone()[0] >= 3:
        source = db.execute(
            f"SELECT source_bundle FROM {store.heads_table(db)} WHERE dataset=?", (dataset,)
        ).fetchone()
        if source:
            result["source_bundle"] = source[0]
    if store.scope_of(db) == "inputs":
        result["scope"] = "inputs"
    if category in {"qc", "eligibility"}:
        result.update(validation=row["validation"], assessment=row["assessment"])
    if category == "eligibility":
        result["profile"] = (export_profile or profile())["version"]
        if inherited_input(db, dataset):
            with store.scoped(db, "inputs"):
                result["input_token"] = token(db, dataset)
        for name in ("metadata", "qc"):
            previous = latest(db, dataset, name)
            result[name + "_decision"] = previous["version"] if previous else None
    return result


def inherited_input(db: sqlite3.Connection, dataset: str) -> dict[str, Any] | None:
    if store.scope_of(db) != "results":
        return None
    head = store.input_state(db, dataset)
    row = store.current(db, dataset)
    if (
        not head
        or head["result_manifest"] != row["manifest"]
        or head["result_input"] != head["version"]
    ):
        return None
    metadata = loads(row["metadata"])
    if any(metadata.get(k) != v for k, v in loads(head["metadata"]).items()):
        return None
    return head


def effective(
    db: sqlite3.Connection,
    dataset: str,
    category: str,
    export_profile: dict[str, Any] | None = None,
) -> str:
    decision = latest(db, dataset, category)
    if not decision:
        if category == "metadata" and inherited_input(db, dataset):
            with store.scoped(db, "inputs"):
                return effective(db, dataset, category)
        return "UNREVIEWED"
    if (
        category == "metadata"
        and decision["data"]["target"]
        and decision["data"]["action"] == "approve"
    ):
        proposal = store.get(db, "proposal", decision["data"]["target"])
        if proposal["schema_version"] == 2:
            head = db.execute(
                f"SELECT source_bundle,canonical_revision FROM {store.heads_table(db)} "
                "WHERE dataset=?",
                (dataset,),
            ).fetchone()
            if head and head[1]:
                revision = store.get(db, "metadata_revision", head[1])["data"]
                row = store.current(db, dataset)
                if (
                    revision["decision"] == decision["version"]
                    and revision["source_bundle"] == head[0]
                    and revision["manifest_version"] == row["manifest"]
                    and revision["metadata_version"] == fingerprint(loads(row["metadata"]))
                ):
                    return "APPROVED"
            return (
                "PENDING_APPLY"
                if decision["data"]["bindings"] == bindings(db, dataset, category)
                else "STALE"
            )
    if decision["data"]["bindings"] != bindings(db, dataset, category, export_profile):
        return "STALE"
    return {"approve": "APPROVED", "reject": "REJECTED", "request-info": "REQUEST_INFO"}[
        decision["data"]["action"]
    ]


def token(db: sqlite3.Connection, dataset: str) -> str:
    row = store.current(db, dataset)
    decisions = [latest(db, dataset, category) for category in CATEGORIES]
    data = {
        "current": row,
        "decisions": [d["version"] if d else None for d in decisions],
        "evidence": bindings(db, dataset, "metadata"),
    }
    if store.scope_of(db) == "results" and inherited_input(db, dataset):
        with store.scoped(db, "inputs"):
            data["input_token"] = token(db, dataset)
    return fingerprint(data)


def status(db: sqlite3.Connection, dataset: str) -> dict[str, Any]:
    row = store.current(db, dataset)
    structural = "NOT_EVALUATED"
    if row["validation"]:
        checked = store.get(db, "validation", row["validation"])["data"]
        structural = (
            "ERROR"
            if any(f["data"]["result"] == "ERROR" for f in checked["findings"])
            else ("PASS" if checked["complete"] else "NOT_EVALUATED")
        )
    scientific = "NOT_EVALUATED"
    if row["assessment"]:
        assessment = store.get(db, "assessment", row["assessment"])["data"]
        scientific = (
            assessment["result"]
            if (
                assessment["manifest_version"] == row["manifest"]
                and assessment["validation_version"] == row["validation"]
                and assessment.get("metadata_version") == fingerprint(loads(row["metadata"]))
            )
            else "STALE"
        )
    execution = store.get(db, "manifest", row["manifest"])["data"]["run"]["execution_state"]
    head = store.input_state(db, dataset)
    if store.scope_of(db) == "inputs" or head and not head["result_manifest"]:
        execution = "NOT_RUN"
    return {
        "dataset_id": dataset,
        "manifest_version": row["manifest"],
        "execution": execution,
        "structural": structural,
        "qc": scientific,
        "reviews": {category: effective(db, dataset, category) for category in CATEGORIES},
        "token": token(db, dataset),
    }


def check_evidence(db: sqlite3.Connection, dataset: str, evidence: list[dict[str, Any]]) -> None:
    if not evidence:
        raise ValueError(
            "Approval requires evidence from this dataset's stored sources or artifacts"
        )
    row = store.current(db, dataset)
    manifest = store.get(db, "manifest", row["manifest"])["data"]
    sources = list(manifest["sources"])
    if db.execute("PRAGMA user_version").fetchone()[0] >= 3:
        head = db.execute(
            f"SELECT source_bundle FROM {store.heads_table(db)} WHERE dataset=?", (dataset,)
        ).fetchone()
        if head and head[0]:
            bundle = store.get(db, "source_bundle", head[0])["data"]
            if bundle["manifest_version"] == row["manifest"]:
                sources.extend(bundle["sources"])
    permitted = {
        (source["data"]["worker"], source["data"]["location"], source["data"]["sha256"])
        for source in sources
    }
    permitted.update(
        (a["worker"], a["path"], a["sha256"]) for a in manifest["artifacts"] if a["sha256"]
    )
    for item in evidence:
        if (item["worker"], item["location"], item["sha256"]) not in permitted:
            raise ValueError("Evidence is not bound to a stored dataset source/artifact")
        if not item["locator"].strip():
            raise ValueError("An evidence locator is required")
        if item["locator"] != "$":
            source = next(
                (
                    s["data"]
                    for s in sources
                    if (
                        s["data"]["location"] == item["location"]
                        and s["data"]["worker"] == item["worker"]
                        and s["data"]["sha256"] == item["sha256"]
                    )
                ),
                None,
            )
            if source is None:
                raise ValueError("Use '$' for an artifact without a stored source snapshot")
            try:
                if item["locator"].startswith("/"):
                    value: Any = loads(source["snapshot"])
                    for key in item["locator"][1:].split("/"):
                        key = key.replace("~1", "/").replace("~0", "~")
                        value = value[int(key)] if isinstance(value, list) else value[key]
                elif item["locator"].startswith("line:"):
                    line = int(item["locator"][5:])
                    if not 1 <= line <= len(source["snapshot"].splitlines()):
                        raise ValueError("Line outside snapshot")
                else:
                    raise ValueError("Use '$', a JSON pointer, or line:N")
            except (ValueError, TypeError, KeyError, IndexError) as error:
                raise ValueError(
                    "Evidence locator does not resolve in the stored source"
                ) from error


def submit(directory: Path, proposal: dict[str, Any], *, scope: str = "results") -> dict[str, Any]:
    validate(proposal, "proposal")
    data = proposal["data"]
    if proposal["schema_version"] == 1 and set(data["changes"]) - DESCRIPTIVE:
        raise ValueError(
            "Only descriptive metadata can be revised; scientific inputs remain immutable"
        )
    with store.write(directory, scope=scope) as db:
        row = store.current(db, data["dataset_id"])
        if row["manifest"] != data["manifest_version"]:
            raise ValueError("Proposal targets a stale manifest")
        if proposal["schema_version"] == 2:
            from .harmonize import check_proposal

            check_proposal(db, proposal)
        check_evidence(db, row["id"], data["evidence"])
        store.put(db, proposal, row["id"])
    return proposal


def decide(
    directory: Path,
    dataset: str,
    category: str,
    action: str,
    expected_token: str,
    reason: str,
    evidence: list[dict[str, Any]],
    target: str | None = None,
    export_profile: dict[str, Any] | None = None,
    *,
    scope: str = "results",
) -> dict[str, Any]:
    if scope == "inputs" and category != "metadata":
        raise ValueError("Input review supports metadata only; QC needs execution results")
    if category not in CATEGORIES or action not in {"approve", "reject", "request-info"}:
        raise ValueError("Unsupported review decision")
    if not reason.strip():
        raise ValueError("A nonempty accountable reason is required")
    export_profile = validate(export_profile, "profile") if export_profile else profile()
    with store.write(directory, scope=scope) as db:
        head = store.input_state(db, dataset)
        if scope == "results" and head and not head["result_manifest"]:
            raise ValueError("Results do not exist; review metadata with --scope inputs")
        if token(db, dataset) != expected_token:
            raise ValueError("Stale review token; inspect current evidence before deciding")
        row = store.current(db, dataset)
        proposal = None
        if target:
            if category != "metadata":
                raise ValueError("Only metadata review accepts a proposal target")
            proposed_record = store.get(db, "proposal", target)
            proposal = proposed_record["data"]
            if proposal["dataset_id"] != dataset or proposal["manifest_version"] != row["manifest"]:
                raise ValueError("Proposal targets a different/stale dataset")
            if proposed_record["schema_version"] == 2:
                from .harmonize import check_proposal

                check_proposal(db, proposed_record)
        if action == "approve":
            check_evidence(db, dataset, evidence)
            if category == "qc":
                if not row["assessment"] or status(db, dataset)["qc"] == "STALE":
                    raise ValueError("QC approval requires a current assessment")
            if category == "eligibility":
                issues = exclusions(db, dataset, export_profile, require_eligibility=False)
                if issues:
                    raise ValueError("Dataset is not eligible: " + "; ".join(issues))
            if proposal and proposed_record["schema_version"] == 1:
                metadata = {**loads(row["metadata"]), **proposal["changes"]}
                store.update_metadata(db, dataset, metadata)
        bound = bindings(db, dataset, category, export_profile)
        decision = ReviewDecision(
            dataset,
            category,
            action,
            target,
            expected_token,
            bound,
            {
                "uid": os.getuid(),
                "user": pwd.getpwuid(os.getuid()).pw_name,
                "host": socket.gethostname(),
                "project_identity": None,
            },
            reason,
            evidence,
            now(),
        ).export()
        store.put(db, decision, dataset)
        if category == "eligibility":
            store.put(db, export_profile, dataset)
        db.execute(
            f"INSERT INTO {store.decisions_table(db)}"
            "(dataset,category,action,version,body) VALUES(?,?,?,?,?)",
            (dataset, category, action, decision["version"], canonical(decision)),
        )
    return decision


def exclusions(
    db: sqlite3.Connection,
    dataset: str,
    export_profile: dict[str, Any],
    *,
    require_eligibility: bool = True,
) -> list[str]:
    config = validate(export_profile, "profile")["data"]
    state = status(db, dataset)
    row = store.current(db, dataset)
    manifest = store.get(db, "manifest", row["manifest"])["data"]
    reasons = []
    if not any(a["availability"] == "available" and a["sha256"] for a in manifest["artifacts"]):
        reasons.append("at least one available artifact with a recorded digest is required")
    if state["structural"] == "ERROR" or (
        config["require_full_validation"] and state["structural"] != "PASS"
    ):
        reasons.append("complete structural validation required")
    for role in config["required_roles"]:
        if not any(
            a["role"] == role and a["availability"] == "available" and a["sha256"]
            for a in manifest["artifacts"]
        ):
            reasons.append("missing required artifact role: " + role)
    if config["require_approval"]:
        for category in CATEGORIES if require_eligibility else ("metadata", "qc"):
            if effective(db, dataset, category, export_profile) != "APPROVED":
                reasons.append(category + " approval missing or stale")
    return reasons


def reviewed_export(
    db: sqlite3.Connection, selection: dict[str, Any], export_profile: dict[str, Any]
) -> dict[str, Any]:
    validate(selection, "selection")
    validate(export_profile, "profile")
    if (
        selection["data"]["registry_id"]
        != db.execute("SELECT value FROM meta WHERE key='registry_id'").fetchone()[0]
    ):
        raise ValueError("Selection belongs to a different registry")
    candidates = selection["data"]["candidates"]
    if len({c["id"] for c in candidates}) != len(candidates):
        raise ValueError("Duplicate export candidates")
    included, excluded, exceptions, decisions, curation = [], [], [], [], []
    for candidate in candidates:
        dataset = candidate["id"]
        row = store.current(db, dataset)
        reasons = exclusions(db, dataset, export_profile)
        if candidate["manifest_version"] != row["manifest"]:
            reasons.append("selection targets a stale manifest")
        if reasons:
            excluded.append({**candidate, "reasons": reasons})
        else:
            included.append(dataset)
            proposals = []
            for category in CATEGORIES:
                decision = latest(db, dataset, category)
                if decision:
                    decisions.append(decision)
                    if decision["data"]["target"]:
                        proposals.append(store.get(db, "proposal", decision["data"]["target"]))
            metadata = loads(row["metadata"])
            metadata_head = (
                db.execute(
                    f"SELECT source_bundle,canonical_revision FROM {store.heads_table(db)} "
                    "WHERE dataset=?",
                    (dataset,),
                ).fetchone()
                if db.execute("PRAGMA user_version").fetchone()[0] >= 3
                else None
            )
            curation.append(
                {
                    "dataset_id": dataset,
                    "manifest_version": row["manifest"],
                    "metadata": metadata,
                    "metadata_version": fingerprint(metadata),
                    "assessment": store.get(db, "assessment", row["assessment"])
                    if row["assessment"]
                    else None,
                    "proposals": proposals,
                    "source_bundle": store.get(db, "source_bundle", metadata_head[0])
                    if metadata_head and metadata_head[0]
                    else None,
                    "metadata_revision": store.get(db, "metadata_revision", metadata_head[1])
                    if metadata_head and metadata_head[1]
                    else None,
                    "input_curation": input_evidence(db, dataset),
                    "invocations": [
                        store.get(db, "invocation", p["data"]["invocation"])
                        for p in proposals
                        if p["data"].get("invocation")
                    ],
                }
            )
            state = status(db, dataset)
            if state["qc"] != "PASS":
                exceptions.append(
                    {
                        "dataset_id": dataset,
                        "qc": state["qc"],
                        "decision": latest(db, dataset, "qc"),
                    }
                )
    return {
        "schema_version": 1,
        "export_kind": "reviewed-manifest-catalog",
        "profile": export_profile,
        "selection": selection,
        "included": included,
        "excluded": excluded,
        "exceptions": exceptions,
        "decisions": decisions,
        "curation": curation,
        "denominators": {
            "candidates": len(candidates),
            "included": len(included),
            "excluded": len(excluded),
        },
        "bundle": store.export_bundle(db, included),
        "location_semantics": (
            "worker-qualified observations; consumers must recheck bytes before use"
        ),
    }


def input_evidence(db: sqlite3.Connection, dataset: str) -> dict[str, Any] | None:
    head = inherited_input(db, dataset)
    if not head:
        return None
    with store.scoped(db, "inputs"):
        decision = latest(db, dataset, "metadata")
    return {
        "input_manifest": store.get(db, "input_manifest", head["version"]),
        "metadata": loads(head["metadata"]),
        "decision": decision,
        "proposal": store.get(db, "proposal", decision["data"]["target"])
        if decision and decision["data"]["target"]
        else None,
        "source_bundle": store.get(db, "source_bundle", head["source_bundle"])
        if head["source_bundle"]
        else None,
        "metadata_revision": store.get(db, "metadata_revision", head["canonical_revision"])
        if head["canonical_revision"]
        else None,
    }
