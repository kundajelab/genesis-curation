"""Isolated, frozen reference audits. Labels are never curation examples or model input."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from ..contracts.records import canonical, fingerprint, loads, now
from ..curation import harmonize
from ..registry import store
from . import catalog, studies


def create(root: Path, samples: list[dict[str, Any]], title: str, actor: str) -> str:
    if not 1 <= len(samples) <= 100 or len({s["id"] for s in samples}) != len(samples):
        raise ValueError("An audit needs 1–100 distinct frozen samples")
    cards = []
    for sample in samples:
        state = sample["state"]
        explicit = sample.get("audit_fields")
        if explicit is not None and (
            not isinstance(explicit, list)
            or not explicit
            or any(not isinstance(f, str) or f not in harmonize.FIELDS for f in explicit)
        ):
            raise ValueError("Audit fields must name supported metadata fields")
        fields = set(explicit) if explicit is not None else studies.required_fields(state)
        context = (
            None
            if explicit is not None
            else next(
                (
                    f["field"]
                    for f in state["fields"]
                    if f["field"]
                    in {"genotype", "cultivar", "developmental_stage", "treatment", "environment"}
                    and f["evidence"]
                ),
                None,
            )
        )
        if context:
            fields.add(context)
        if sample.get("candidate") and fingerprint(sample["candidate"]["sources"]) != fingerprint(
            state["sources"]
        ):
            raise ValueError("Candidate predictions must use the identical frozen sources")
        cards.append(
            {**sample, "fields": sorted(fields), "source_hash": fingerprint(state["sources"])}
        )
    body = {"title": title, "samples": cards, "version": 1, "created": now(), "actor": actor}
    key = fingerprint(cards)
    body["id"] = key
    with store.write(root) as db:
        db.execute("INSERT OR IGNORE INTO curation_audits VALUES(?,?)", (key, canonical(body)))
    return key


def read(root: Path, key: str) -> dict[str, Any]:
    with contextlib.closing(store.connect(root)) as db:
        row = db.execute("SELECT body FROM curation_audits WHERE id=?", (key,)).fetchone()
    if not row:
        raise ValueError("Unknown audit")
    return loads(row[0])


def answers(root: Path, key: str) -> dict[str, Any]:
    with contextlib.closing(store.connect(root)) as db:
        return {
            r[0]: loads(r[1])
            for r in db.execute(
                "SELECT sample,body FROM curation_audit_answers WHERE audit=?", (key,)
            )
        }


def listing(root: Path) -> list[dict[str, Any]]:
    with contextlib.closing(store.connect(root)) as db:
        audits = [
            loads(r[0]) for r in db.execute("SELECT body FROM curation_audits ORDER BY rowid DESC")
        ]
    return [
        {
            "id": a["id"],
            "title": a["title"],
            "total": len(a["samples"]),
            "completed": len(answers(root, a["id"])),
        }
        for a in audits
    ]


def card(root: Path, key: str, sid: str) -> dict[str, Any]:
    sample = next((s for s in read(root, key)["samples"] if s["id"] == sid), None)
    if sample is None:
        raise ValueError("Unknown audit sample")
    return sample


def detail(root: Path, key: str) -> dict[str, Any]:
    value, saved = read(root, key), answers(root, key)
    return {
        "id": key,
        "title": value["title"],
        "samples": [
            {
                "id": s["id"],
                "name": s["state"]["name"],
                "study": s["study"],
                "split": s["split"],
                "completed": s["id"] in saved,
            }
            for s in value["samples"]
        ],
    }


def view(root: Path, key: str, sid: str, actor: str) -> dict[str, Any]:
    sample = card(root, key, sid)
    answer = answers(root, key).get(sid)
    with contextlib.closing(store.connect(root)) as db:
        row = db.execute(
            "SELECT body FROM curation_drafts WHERE actor=? AND task=?",
            (actor, f"audit:{key}:{sid}"),
        ).fetchone()
    state = sample["state"]
    entries = catalog.evidence_index(state["sources"])
    wanted = set(sample["fields"])
    # Evidence ordering must not reveal which passages the model selected.
    entries.sort(key=lambda e: (e["source_version"], e["locator"]))
    return {
        "id": sid,
        "name": state["name"],
        "fields": sample["fields"],
        "source_hash": sample["source_hash"],
        "evidence": entries,
        "direct_values": {
            f["field"]: f
            for f in harmonize.extract(
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
            if f["field"] in wanted and f["status"] == "known"
        },
        "candidate_prediction": [
            f for f in sample.get("candidate", {}).get("fields", []) if f["field"] in wanted
        ]
        if answer
        else None,
        "answer": answer,
        "draft": loads(row[0]) if row else {},
        "prediction": [f for f in state["fields"] if f["field"] in wanted] if answer else None,
    }


def save_draft(root: Path, key: str, sid: str, actor: str, body: dict[str, Any]) -> None:
    card(root, key, sid)
    if len(canonical(body)) > 32000:
        raise ValueError("Audit draft exceeds size limit")
    with store.write(root) as db:
        db.execute(
            "INSERT INTO curation_drafts VALUES(?,?,?) ON CONFLICT(actor,task) "
            "DO UPDATE SET body=excluded.body",
            (actor, f"audit:{key}:{sid}", canonical(body)),
        )


def save(root: Path, key: str, sid: str, actor: str, body: dict[str, Any]) -> dict[str, Any]:
    sample = card(root, key, sid)
    labels = body.get("labels", {})
    if body.get("source_hash") != sample["source_hash"] or set(labels) != set(sample["fields"]):
        raise ValueError("Audit requires judgments for the exact frozen fields and sources")
    entries = catalog.evidence_index(sample["state"]["sources"])
    for label in labels.values():
        if not isinstance(label, dict) or label.get("status") not in {
            "known",
            "unknown",
            "not_applicable",
            "cannot_determine",
        }:
            raise ValueError("Choose a judgment for each field")
        if not isinstance(label.get("value", ""), str) or len(label.get("value", "")) > 1000:
            raise ValueError("Audit value must be text of at most 1000 characters")
        if label["status"] in {"known", "not_applicable"}:
            if not label.get("value", "").strip() or label.get("evidence") not in entries:
                raise ValueError("A supported answer requires a value and an exact source passage")
        else:
            label["value"] = None
            label["evidence"] = None
    value = {
        "labels": labels,
        "source_hash": sample["source_hash"],
        "actor": actor,
        "created": now(),
    }
    value["version"] = fingerprint(value)
    with store.write(root) as db:
        old = db.execute(
            "SELECT body FROM curation_audit_answers WHERE audit=? AND sample=?", (key, sid)
        ).fetchone()
        expected = loads(old[0])["version"] if old else None
        if body.get("version") != expected:
            raise ValueError("Stale audit answer; reload before saving")
        db.execute(
            "INSERT INTO curation_audit_answers VALUES(?,?,?,?) ON CONFLICT(audit,sample) "
            "DO UPDATE SET actor=excluded.actor,body=excluded.body",
            (key, sid, actor, canonical(value)),
        )
        db.execute(
            "INSERT INTO curation_audit_events(audit,body) VALUES(?,?)",
            (key, canonical({"sample": sid, "answer": value, "previous": expected})),
        )
        db.execute(
            "DELETE FROM curation_drafts WHERE actor=? AND task=?", (actor, f"audit:{key}:{sid}")
        )
    return view(root, key, sid, actor)


def report(root: Path, key: str) -> dict[str, Any]:
    value, saved = read(root, key), answers(root, key)
    counts = {
        "judged_fields": 0,
        "cannot_determine": 0,
        "matching_values": 0,
        "unsupported_assertions": 0,
        "missed_supported_values": 0,
        "errors_without_task": 0,
    }
    differences = []
    for sample in value["samples"]:
        if sample["id"] not in saved:
            continue
        predicted = {f["field"]: f for f in sample["state"]["fields"]}
        for name, label in saved[sample["id"]]["labels"].items():
            if label["status"] == "cannot_determine":
                counts["cannot_determine"] += 1
                continue
            counts["judged_fields"] += 1
            model = predicted[name]
            equal = (
                model["status"] == label["status"]
                and str(model["value"] or "").casefold().strip()
                == str(label["value"] or "").casefold().strip()
            )
            counts["matching_values"] += int(equal)
            counts["unsupported_assertions"] += int(
                label["status"] == "unknown" and model["status"] == "known"
            )
            counts["missed_supported_values"] += int(
                label["status"] == "known" and model["status"] != "known"
            )
            counts["errors_without_task"] += int(
                not equal
                and name not in sample.get("task_fields", [])
                and "__preparation" not in sample.get("task_fields", [])
            )
            if not equal:
                differences.append(
                    {
                        "sample": sample["id"],
                        "field": name,
                        "split": sample["split"],
                        "reference": label,
                        "prediction": model,
                    }
                )
    candidate_counts = {name: 0 for name in counts}
    candidate_differences = []
    split_counts: dict[str, dict[str, int]] = {}
    for sample in value["samples"]:
        if sample["id"] not in saved or not sample.get("candidate"):
            continue
        predicted = {f["field"]: f for f in sample["candidate"]["fields"]}
        metrics = split_counts.setdefault(sample["split"], {name: 0 for name in counts})
        for name, label in saved[sample["id"]]["labels"].items():
            if label["status"] == "cannot_determine":
                candidate_counts["cannot_determine"] += 1
                metrics["cannot_determine"] += 1
                continue
            model = predicted[name]
            equal = (
                model["status"] == label["status"]
                and str(model["value"] or "").casefold().strip()
                == str(label["value"] or "").casefold().strip()
            )
            deltas = {
                "judged_fields": 1,
                "matching_values": int(equal),
                "unsupported_assertions": int(
                    label["status"] == "unknown" and model["status"] == "known"
                ),
                "missed_supported_values": int(
                    label["status"] == "known" and model["status"] != "known"
                ),
                "errors_without_task": int(
                    not equal
                    and name not in sample.get("candidate_task_fields", [])
                    and "__preparation" not in sample.get("candidate_task_fields", [])
                ),
            }
            for metric, amount in deltas.items():
                candidate_counts[metric] += amount
                metrics[metric] += amount
            if not equal:
                candidate_differences.append(
                    {
                        "sample": sample["id"],
                        "field": name,
                        "split": sample["split"],
                        "reference": label,
                        "prediction": model,
                    }
                )
    return {
        "id": key,
        "completed_samples": len(saved),
        "total_samples": len(value["samples"]),
        "metrics": counts,
        "candidate_metrics": candidate_counts,
        "candidate_splits": split_counts,
        "candidate_differences": candidate_differences,
        "differences": differences,
        "status": "PRELIMINARY_EXPERT_AUDIT" if saved else "AWAITING_HUMAN_LABELS",
        "comparison": (
            "Exact case-insensitive labels and status; inspect synonyms in differences. "
            "No semantic accuracy claim."
        ),
        "held_out_labels_used_for_training": False,
    }
