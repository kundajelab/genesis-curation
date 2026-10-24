"""Study membership, exception review and immutable, human-signed releases."""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any

from ..contracts.records import canonical, fingerprint, identity, loads, now
from ..curation import harmonize
from ..registry import store
from . import catalog

PAGE_SIZE = 50


def read(db: sqlite3.Connection, key: str) -> dict[str, Any]:
    row = db.execute("SELECT body FROM curation_studies WHERE id=?", (key,)).fetchone()
    if not row:
        raise ValueError("Unknown study")
    return loads(row[0])


def members(db: sqlite3.Connection, key: str) -> list[dict[str, Any]]:
    return [
        loads(r[0])
        for r in db.execute(
            "SELECT s.body FROM curation_subjects s JOIN curation_members m ON s.id=m.subject "
            "WHERE m.study=? ORDER BY s.id",
            (key,),
        )
    ]


def revision(study: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    return fingerprint(
        {
            "study": {k: v for k, v in study.items() if k not in {"approval", "updated"}},
            "members": [[s["id"], s["version"]] for s in rows],
        }
    )


def save(
    db: sqlite3.Connection, study: dict[str, Any], action: str, actor: str, details: dict[str, Any]
) -> None:
    study["updated"] = now()
    db.execute(
        "INSERT INTO curation_studies VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
        (study["id"], canonical(study)),
    )
    db.execute(
        "INSERT INTO curation_study_events(study,body) VALUES(?,?)",
        (
            study["id"],
            canonical({"action": action, "actor": actor, "created": now(), "details": details}),
        ),
    )


def link(
    root: Path,
    subjects: list[str],
    name: str,
    actor: str,
    accession: str | None = None,
    group: str | None = None,
) -> str:
    if not subjects or not name.strip() or len(name) > 200:
        raise ValueError("A study needs a name and at least one record")
    key = identity("curation-study", accession or group or fingerprint(sorted(subjects)))
    with store.write(root) as db:
        existing = db.execute("SELECT body FROM curation_studies WHERE id=?", (key,)).fetchone()
        study = (
            loads(existing[0])
            if existing
            else {
                "id": key,
                "name": name.strip(),
                "accession": accession,
                "created": now(),
                "approval": None,
                "preparation": {
                    "status": "NOT_STARTED",
                    "completed": {},
                    "failures": {},
                    "acknowledged": {},
                    "retrieval": [],
                },
            }
        )
        if not existing:
            save(db, study, "create", actor, {})
        for subject in subjects:
            catalog.read(db, subject)
            db.execute("INSERT OR IGNORE INTO curation_members VALUES(?,?)", (key, subject))
        save(db, study, "membership", actor, {"subjects": subjects})
    return key


def bootstrap(root: Path) -> None:
    """Upgrade legacy imports without guessing biological study relationships."""
    groups: dict[str, list[dict[str, Any]]] = {}
    with contextlib.closing(store.connect(root)) as db:
        for row in db.execute(
            "SELECT body FROM curation_subjects WHERE id NOT IN "
            "(SELECT subject FROM curation_members)"
        ):
            state = loads(row[0])
            group = state["original"].get("study_accession") or state["import_source"]
            groups.setdefault(str(group), []).append(state)
    for group, rows in groups.items():
        accession = rows[0]["original"].get("study_accession")
        link(
            root,
            [r["id"] for r in rows],
            str(accession or rows[0]["namespace"]),
            "migration",
            str(accession) if accession else None,
            group,
        )


def required_fields(state: dict[str, Any]) -> set[str]:
    values = {f["field"]: str(f["value"] or "").lower() for f in state["fields"]}
    assay = values.get("assay", "") or str(state["original"].get("assay", "")).lower()
    required = {"organism", "assay"}
    if "dap" not in assay:
        required.add("tissue")
    if "chip" in assay or "dap" in assay:
        required |= {"tf_identity", "control_assignment_proposal"}
    if "dap" in assay:
        required |= {"dna_organism", "tf_source_organism"}
    return required


def tasks(study: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    preparation = study["preparation"]
    for state in rows:
        failure = preparation["failures"].get(state["id"])
        if failure:
            acknowledged = preparation["acknowledged"].get(state["id"]) == fingerprint(failure)
            key = identity("study-task", study["id"], state["id"], "preparation")
            grouped[key] = {
                "id": key,
                "study": study["id"],
                "field": "__preparation",
                "kind": "failure",
                "status": "done" if acknowledged else "open",
                "priority": 0,
                "title": "Resolve preparation failure",
                "reason": failure,
                "targets": [
                    {
                        "subject": state["id"],
                        "name": state["name"],
                        "version": state["version"],
                        "field": None,
                        "recommendation": None,
                    }
                ],
            }
        required = required_fields(state)
        for field in state["fields"]:
            name = field["field"]
            decision = state["reviews"].get(name)
            prior_issue = decision and decision["original_proposal"]["status"] in {
                "unknown",
                "conflicting",
            }
            if (
                field["status"] != "conflicting"
                and not (name in required and field["status"] == "unknown")
                and not prior_issue
            ):
                continue
            status = (
                "waiting"
                if decision and decision["action"] == "request-info"
                else "done"
                if decision
                else "open"
            )
            kind = "conflict" if field["status"] == "conflicting" else "missing"
            recommendation = state.get("recommendations", {}).get(name)
            # Conflicts with distinct evidence stay separate; missing fields share a question,
            # but every target still needs its own supported correction.
            scope = fingerprint(field["evidence"]) if kind == "conflict" else "missing"
            key = identity("study-task", study["id"], name, kind, scope, status)
            task = grouped.setdefault(
                key,
                {
                    "id": key,
                    "study": study["id"],
                    "field": name,
                    "kind": kind,
                    "status": status,
                    "priority": 1
                    if name in {"organism", "assay", "control_assignment_proposal"}
                    else 2
                    if kind == "conflict"
                    else 3,
                    "title": ("Resolve conflicting " if kind == "conflict" else "Confirm ")
                    + name.replace("_", " "),
                    "reason": field["rationale"],
                    "targets": [],
                },
            )
            task["targets"].append(
                {
                    "subject": state["id"],
                    "name": state["name"],
                    "version": state["version"],
                    "field": field,
                    "recommendation": recommendation,
                    "review": decision,
                }
            )
    result = list(grouped.values())
    for task in result:
        task["version"] = fingerprint(task)
    return sorted(result, key=lambda t: (t["priority"], t["title"], t["id"]))


def view(db: sqlite3.Connection, key: str, *, detail: bool = True) -> dict[str, Any]:
    study, rows = read(db, key), members(db, key)
    current = revision(study, rows)
    issues = tasks(study, rows)
    pending = sum(t["status"] != "done" for t in issues)
    signed = False
    if study["approval"]:
        release = db.execute(
            "SELECT body FROM curation_releases WHERE version=?", (study["approval"],)
        ).fetchone()
        signed = bool(release and loads(release[0])["study_revision"] == current)
    prep = study["preparation"]["status"]
    status = (
        "signed"
        if signed
        else "preparing"
        if prep in {"RUNNING", "QUEUED"}
        else "needs_decisions"
        if pending
        else "ready"
        if prep == "COMPLETED"
        else "not_prepared"
    )
    result = {
        **study,
        "version": current,
        "status": status,
        "member_count": len(rows),
        "pending": pending,
        "completed_tasks": sum(t["status"] == "done" for t in issues),
        "approval_current": signed,
    }
    if detail:
        result.update(tasks=issues, records=rows)
    return result


def get(root: Path, key: str) -> dict[str, Any]:
    with contextlib.closing(store.connect(root)) as db:
        db.execute("BEGIN")
        return view(db, key)


def listing(root: Path, cursor: str = "", limit: int = PAGE_SIZE) -> dict[str, Any]:
    if not 1 <= limit <= 200:
        raise ValueError("Page size must be 1–200")
    with contextlib.closing(store.connect(root)) as db:
        db.execute("BEGIN")
        ids = [
            r[0]
            for r in db.execute(
                "SELECT id FROM curation_studies WHERE id>? ORDER BY id LIMIT ?",
                (cursor, limit + 1),
            )
        ]
        return {
            "items": [view(db, key, detail=False) for key in ids[:limit]],
            "next_cursor": ids[limit - 1] if len(ids) > limit else None,
            "total": db.execute("SELECT count(*) FROM curation_studies").fetchone()[0],
        }


def find_task(
    db: sqlite3.Connection, study_id: str, task_id: str, version: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    study = read(db, study_id)
    task = next((t for t in tasks(study, members(db, study_id)) if t["id"] == task_id), None)
    if task is None or task["version"] != version:
        raise ValueError("Stale task: reload the current evidence")
    return study, task


def draft(
    root: Path, actor: str, task_id: str, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    with store.write(root) as db:
        if body is not None:
            if len(canonical(body)) > 32000:
                raise ValueError("Draft exceeds 32,000 characters")
            if task_id != "workspace":
                find_task(db, body["study"], task_id, body["version"])
            db.execute(
                "INSERT INTO curation_drafts VALUES(?,?,?) ON CONFLICT(actor,task) "
                "DO UPDATE SET body=excluded.body",
                (actor, task_id, canonical(body)),
            )
        row = db.execute(
            "SELECT body FROM curation_drafts WHERE actor=? AND task=?", (actor, task_id)
        ).fetchone()
        return loads(row[0]) if row else {}


def resolve(root: Path, task_id: str, body: dict[str, Any], actor: str) -> dict[str, Any]:
    action, reason = body["action"], body.get("reason", "").strip()
    if action not in {"accept", "correct", "unknown", "defer"} or not 1 <= len(reason) <= 4000:
        raise ValueError("Choose a decision and a reason (1–4000 characters)")
    with store.write(root) as db:
        study, task = find_task(db, body["study"], task_id, body["version"])
        if study["preparation"]["status"] in {"RUNNING", "QUEUED"}:
            raise ValueError("Wait for preparation or cancel it before changing decisions")
        if task["kind"] == "failure":
            if action != "unknown":
                raise ValueError("Retry preparation, or explicitly acknowledge its failure")
            for target in task["targets"]:
                sid = target["subject"]
                study["preparation"]["acknowledged"][sid] = fingerprint(
                    study["preparation"]["failures"][sid]
                )
        else:
            for target in task["targets"]:
                state = catalog.read(db, target["subject"])
                field = dict(target["field"])
                if action == "accept":
                    field = dict(target["recommendation"] or field)
                elif action == "correct":
                    correction = body.get("corrections", {}).get(state["id"])
                    if not isinstance(correction, dict):
                        raise ValueError("Provide a supported correction for every listed sample")
                    field.update(correction)
                    field.update(field=task["field"], original=state["metadata"].get(task["field"]))
                harmonize.verify_fields({"data": {"sources": state["sources"]}}, [field])
                if action in {"accept", "correct"} and field["status"] not in {
                    "known",
                    "not_applicable",
                }:
                    raise ValueError(
                        "Acceptance needs a supported value; use Keep unknown otherwise"
                    )
                decision = {
                    "action": "approve"
                    if action in {"accept", "correct"}
                    else "request-info"
                    if action == "defer"
                    else "reject",
                    "reason": reason,
                    "actor": actor,
                    "created": now(),
                    "field": field,
                    "original_proposal": target["field"],
                    "remember": bool(body.get("remember")) and action == "correct",
                    "source_versions": [s["version"] for s in state["sources"]],
                }
                state["reviews"][task["field"]] = decision
                state["fields"] = [
                    field if f["field"] == task["field"] else f for f in state["fields"]
                ]
                if decision["action"] == "approve":
                    state["metadata"][task["field"]] = field["value"]
                catalog.save(db, state, "decision", actor, decision)
        save(db, study, "task-decision", actor, {"task": task, "action": action, "reason": reason})
        db.execute("DELETE FROM curation_drafts WHERE actor=? AND task=?", (actor, task_id))
        return view(db, study["id"])


def preparation(root: Path, key: str, updates: dict[str, Any], actor: str = "worker") -> None:
    with store.write(root) as db:
        study = read(db, key)
        study["preparation"].update(updates)
        save(db, study, "preparation", actor, updates)


def recover(root: Path) -> None:
    with store.write(root) as db:
        for row in db.execute("SELECT body FROM curation_studies").fetchall():
            study = loads(row[0])
            if study["preparation"]["status"] == "RUNNING":
                study["preparation"]["status"] = "INTERRUPTED"
                save(db, study, "interrupted", "worker", {})


def approve(root: Path, key: str, version: str, actor: str) -> dict[str, Any]:
    with store.write(root) as db:
        study, rows = read(db, key), members(db, key)
        if revision(study, rows) != version:
            raise ValueError("Stale study: reload the summary before signing")
        if study["preparation"]["status"] != "COMPLETED":
            raise ValueError("Finish preparation before signing")
        if any(t["status"] != "done" for t in tasks(study, rows)):
            raise ValueError("Resolve or acknowledge every outstanding task before signing")
        exported = []
        for state in rows:
            accepted: dict[str, Any] = {}
            decisions = state["reviews"]
            for field in state["fields"]:
                name = field["field"]
                if name in decisions:
                    if decisions[name]["action"] == "approve":
                        accepted[name] = decisions[name]["field"]
                    continue
                if field["status"] in {"known", "not_applicable"}:
                    harmonize.verify_fields({"data": {"sources": state["sources"]}}, [field])
                    decision = {
                        "action": "approve",
                        "reason": "Approved in study summary",
                        "actor": actor,
                        "created": now(),
                        "field": field,
                        "original_proposal": field,
                        "source_versions": [s["version"] for s in state["sources"]],
                    }
                    state["reviews"][name] = decision
                    state["metadata"][name] = field["value"]
                    catalog.save(db, state, "decision", actor, decision)
                    accepted[name] = field
            context = {name: accepted.get(name, {}).get("value") for name in ("organism", "tissue")}
            if all(context.values()):
                for decision in state["reviews"].values():
                    if decision.get("remember") and decision["action"] == "approve":
                        payload = {
                            "context": context,
                            "classification": state["classification"],
                            "decision": decision,
                        }
                        catalog.save(db, state, "example", actor, payload)
            exported.append(
                {
                    "id": state["id"],
                    "name": state["name"],
                    "version": state["version"],
                    "metadata": {k: v["value"] for k, v in accepted.items()},
                    "annotations": accepted,
                    "unresolved": [
                        f["field"] for f in state["fields"] if f["field"] not in accepted
                    ],
                    "decisions": state["reviews"],
                    "sources": state["sources"],
                    "proposal": state["proposal"],
                    "provenance": state.get("preparation_provenance", {}),
                }
            )
        release = {
            "schema_version": 1,
            "kind": "signed-study-metadata",
            "study": key,
            "study_name": study["name"],
            "study_revision": revision(study, rows),
            "reviewer": actor,
            "signed_at": now(),
            "records": exported,
            "preparation": study["preparation"],
            "scientific_qc": "NOT_EVALUATED",
            "biological_accuracy": "NOT_INDEPENDENTLY_VALIDATED",
        }
        digest = fingerprint(release)
        release["integrity"] = {
            "algorithm": "sha256-canonical-json",
            "scope": "document excluding integrity",
            "sha256": digest,
        }
        db.execute("INSERT INTO curation_releases VALUES(?,?,?)", (digest, key, canonical(release)))
        study["approval"] = digest
        save(db, study, "sign-off", actor, {"release": digest})
        return release


def export(root: Path, key: str, version: str = "") -> dict[str, Any]:
    with contextlib.closing(store.connect(root)) as db:
        db.execute("BEGIN")
        current = view(db, key, detail=False)
        if not version and not current["approval_current"]:
            raise ValueError("The current study needs sign-off before export")
        row = db.execute(
            "SELECT body FROM curation_releases WHERE study=? AND version=?",
            (key, version or current["approval"]),
        ).fetchone()
        if not row:
            raise ValueError("Unknown signed release")
        return loads(row[0])
