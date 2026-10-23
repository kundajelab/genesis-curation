"""Metadata-only subjects with immutable field decisions in the Genesis registry."""

from __future__ import annotations

import contextlib
import csv
import io
import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from ..contracts.records import (
    canonical,
    identity,
    loads,
    now,
    record,
    reject_constant,
    unique_object,
)
from ..curation import harmonize
from ..llm import providers
from ..llm.security import schema
from ..registry import store

MAX_ROWS = 1000


def read(db: sqlite3.Connection, subject: str) -> dict[str, Any]:
    row = db.execute("SELECT body FROM curation_subjects WHERE id=?", (subject,)).fetchone()
    if not row:
        raise ValueError("Unknown metadata subject")
    return loads(row[0])


def get(root: Path, subject: str) -> dict[str, Any]:
    with contextlib.closing(store.connect(root)) as db:
        return read(db, subject)


def listing(root: Path) -> list[dict[str, Any]]:
    with contextlib.closing(store.connect(root)) as db:
        rows = db.execute(
            "SELECT body FROM curation_subjects ORDER BY rowid DESC LIMIT 1000"
        ).fetchall()
    return [
        {k: s[k] for k in ("id", "name", "kind", "classification", "version", "updated")}
        | {
            "pending": sum(
                s["reviews"].get(f["field"], {}).get("action") != "approve" for f in s["fields"]
            ),
            "uncertain": sum(f["status"] in {"conflicting", "unknown"} for f in s["fields"]),
        }
        for row in rows
        for s in [loads(row[0])]
    ]


def save(
    db: sqlite3.Connection,
    state: dict[str, Any],
    operation: str,
    actor: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    event = record(
        "curation_event",
        {
            "subject": state["id"],
            "operation": operation,
            "parent": state.get("version"),
            "actor": actor,
            "created": now(),
            "payload": payload,
        },
        str(uuid.uuid4()),
        version=5,
    )
    state.update(version=event["version"], updated=event["data"]["created"])
    store.put(db, event)
    db.execute(
        "INSERT INTO curation_subjects VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "version=excluded.version,body=excluded.body",
        (state["id"], state["version"], canonical(state)),
    )
    db.execute(
        "INSERT INTO curation_events(subject,version) VALUES(?,?)", (state["id"], event["version"])
    )
    return state


def expect(state: dict[str, Any], version: str) -> None:
    if state["version"] != version:
        raise ValueError("Stale review: reload the current record and evidence")


def history(root: Path, subject: str) -> list[dict[str, Any]]:
    with contextlib.closing(store.connect(root)) as db:
        return [
            store.get(db, "curation_event", row[0])
            for row in db.execute(
                "SELECT version FROM curation_events WHERE subject=? ORDER BY sequence", (subject,)
            )
        ]


def preview(text: str, filename: str) -> list[dict[str, Any]]:
    if len(text.encode()) > harmonize.MAX_SOURCE:
        raise ValueError("Upload exceeds 2 MiB; split the metadata table")
    if filename.lower().endswith(".json"):
        raw = json.loads(text, parse_constant=reject_constant, object_pairs_hook=unique_object)
        rows = raw if isinstance(raw, list) else [raw]
    elif filename.lower().endswith((".csv", ".tsv")):
        reader = csv.DictReader(
            io.StringIO(text), delimiter="\t" if filename.lower().endswith(".tsv") else ","
        )
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("Upload needs unique column headers")
        rows = list(reader)
    else:
        raise ValueError("Import metadata as CSV, TSV or JSON; attach supporting text to a record")
    if not 1 <= len(rows) <= MAX_ROWS:
        raise ValueError("Import requires 1–1000 records")
    if any(not isinstance(row, dict) or None in row for row in rows):
        raise ValueError("Every metadata row must be an object with named fields")
    # Canonical roundtrip rejects NaN and normalizes JSON-compatible values.
    canonical(rows)
    return rows


def import_rows(
    root: Path,
    text: str,
    filename: str,
    namespace: str,
    id_column: str,
    classification: str,
    actor: str,
) -> list[str]:
    rows = preview(text, filename)
    if classification not in harmonize.CLASSIFICATIONS or not namespace.strip():
        raise ValueError("Specify a source namespace and classification")
    identifiers = [str(row.get(id_column) or "").strip() for row in rows]
    if not all(identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError("Choose an identifier column with unique nonempty values")
    source = harmonize.source_record(
        text,
        "upload:" + Path(filename).name,
        "application/json" if filename.endswith(".json") else "text/plain",
    )
    result = []
    with store.write(root) as db:
        store.put(db, source)
        for row, name in zip(rows, identifiers, strict=True):
            key = identity("metadata-subject", namespace, name)
            existing = db.execute(
                "SELECT body FROM curation_subjects WHERE id=?", (key,)
            ).fetchone()
            if existing:
                old = loads(existing[0])
                if (
                    old["import_source"] != source["version"]
                    or old["classification"] != classification
                ):
                    raise ValueError(
                        "Subject already exists with different input; "
                        "attach new evidence or use a new namespace"
                    )
                result.append(key)
                continue
            snapshot = harmonize.source_record(
                canonical(row), f"upload-row:{source['version']}:{name}", "application/json"
            )
            store.put(db, snapshot)
            fields = harmonize.extract({"data": {"sources": [snapshot]}}, {})
            state = {
                "id": key,
                "name": name,
                "kind": "study" if name.startswith(("GSE", "SRP", "ERP", "PRJ")) else "sample",
                "namespace": namespace,
                "classification": classification,
                "import_source": source["version"],
                "original": row,
                "metadata": {},
                "sources": [snapshot],
                "fields": fields,
                "reviews": {},
                "proposal": None,
                "messages": [],
            }
            save(db, state, "import", actor, {"source": source["version"], "row": snapshot})
            result.append(key)
    return result


def attach(
    root: Path,
    subject: str,
    version: str,
    text: str,
    label: str,
    classification: str,
    actor: str,
    media: str = "text/plain",
) -> dict[str, Any]:
    if not text.strip() or len(text.encode()) > harmonize.MAX_SOURCE:
        raise ValueError("Evidence must be nonempty and at most 2 MiB")
    if classification not in harmonize.CLASSIFICATIONS:
        raise ValueError("Invalid classification")
    source = harmonize.source_record(text, label[:512], media)
    with store.write(root) as db:
        state = read(db, subject)
        expect(state, version)
        sources = {s["version"]: s for s in state["sources"]}
        sources[source["version"]] = source
        if (
            len(sources) > 64
            or sum(len(s["data"]["snapshot"].encode()) for s in sources.values()) > 16 * 1024**2
        ):
            raise ValueError("Evidence bundle exceeds its source budget")
        state["classification"] = max(
            (classification, state["classification"]), key=harmonize.CLASSIFICATIONS.__getitem__
        )
        state.update(sources=list(sources.values()), reviews={}, proposal=None)
        state["fields"] = harmonize.extract(
            {"data": {"sources": state["sources"]}}, state["metadata"]
        )
        store.put(db, source)
        return save(
            db,
            state,
            "source",
            actor,
            {"source": source, "classification": state["classification"]},
        )


def decide(
    root: Path,
    subject: str,
    version: str,
    field: str,
    action: str,
    reason: str,
    actor: str,
    correction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if (
        action not in {"approve", "reject", "request-info"}
        or not reason.strip()
        or len(reason) > 4000
    ):
        raise ValueError("Choose a review action and provide a reason (1–4000 characters)")
    with store.write(root) as db:
        state = read(db, subject)
        expect(state, version)
        found = next((f for f in state["fields"] if f["field"] == field), None)
        if found is None:
            raise ValueError("Unknown field")
        candidate = {**found, **correction} if correction else dict(found)
        candidate.update(field=field, original=state["metadata"].get(field))
        harmonize.verify_fields({"data": {"sources": state["sources"]}}, [candidate])
        if action == "approve" and candidate["status"] not in {"known", "not_applicable"}:
            raise ValueError("Uncertain fields need a supported correction before approval")
        decision = {
            "action": action,
            "reason": reason.strip(),
            "actor": actor,
            "created": now(),
            "field": candidate,
            "original_proposal": found,
            "source_versions": [s["version"] for s in state["sources"]],
        }
        state["reviews"][field] = decision
        if action == "approve":
            state["metadata"][field] = candidate["value"]
        state["fields"] = [candidate if f["field"] == field else f for f in state["fields"]]
        return save(db, state, "decision", actor, decision)


def export(root: Path) -> dict[str, Any]:
    records = []
    with contextlib.closing(store.connect(root)) as db:
        for row in db.execute("SELECT body FROM curation_subjects ORDER BY id"):
            state = loads(row[0])
            accepted = {
                k: d["field"]["value"]
                for k, d in state["reviews"].items()
                if d["action"] == "approve"
            }
            records.append(
                {
                    "id": state["id"],
                    "name": state["name"],
                    "version": state["version"],
                    "metadata": accepted,
                    "review_status": "APPROVED"
                    if all(f["field"] in accepted for f in state["fields"])
                    else "PARTIAL",
                    "unresolved": [
                        f["field"] for f in state["fields"] if f["field"] not in accepted
                    ],
                    "decisions": state["reviews"],
                    "sources": state["sources"],
                }
            )
    return {"schema_version": 1, "kind": "reviewed-metadata", "records": records}


def promote(root: Path, subject: str, version: str, field: str, actor: str) -> dict[str, Any]:
    with store.write(root) as db:
        state = read(db, subject)
        expect(state, version)
        decision = state["reviews"].get(field)
        if not decision or decision["action"] != "approve":
            raise ValueError("Only an approved correction can become an example")
        context = {k: state["metadata"].get(k) for k in ("organism", "tissue")}
        if not all(context.values()):
            raise ValueError("Approve organism and tissue before promoting a scoped example")
        payload = {
            "context": context,
            "classification": state["classification"],
            "decision": decision,
        }
        return save(db, state, "example", actor, payload)


def examples(root: Path, state: dict[str, Any]) -> list[dict[str, Any]]:
    context = {
        k: state["metadata"].get(k) or state["original"].get(k) for k in ("organism", "tissue")
    }
    with contextlib.closing(store.connect(root)) as db:
        rows = db.execute(
            "SELECT body FROM records WHERE kind='curation_event' ORDER BY created DESC"
        ).fetchall()
    result = []
    for row in rows:
        item = loads(row[0])
        data = item["data"]
        if data["operation"] == "example" and data["payload"]["context"] == context:
            if (
                harmonize.CLASSIFICATIONS[data["payload"]["classification"]]
                <= harmonize.CLASSIFICATIONS[state["classification"]]
            ):
                current = get(root, data["subject"])
                decision = data["payload"]["decision"]
                if current["reviews"].get(decision["field"]["field"]) == decision:
                    result.append({"version": item["version"], **data["payload"]})
        if len(result) == 5:
            break
    return result


def run_proposal(
    root: Path,
    subject: str,
    version: str,
    config: dict[str, Any],
    skill: dict[str, Any],
    actor: str,
    feedback: str = "",
    cancellation: threading.Event | None = None,
) -> dict[str, Any]:
    state = get(root, subject)
    expect(state, version)
    bundle = {"data": {"sources": state["sources"]}}
    extracted = harmonize.extract(bundle, state["metadata"])
    guidance = {
        "skill": skill,
        "reviewed_examples": examples(root, state) if skill["id"] != "none" else [],
        "feedback": feedback,
        "rules": "Propose only source-supported values; "
        "examples are not evidence about this dataset.",
    }
    specification = schema("metadata-response-v1")
    properties = specification["properties"]["fields"]["items"]["properties"]
    properties["original"] = {"type": ["string", "integer", "null"]}
    properties["field"] = {"enum": sorted(harmonize.FIELDS)}
    properties["vocabulary_version"] = {"enum": [harmonize.vocabulary()["version"]]}
    invocation = providers.invoke(
        root,
        config,
        task="metadata",
        cancellation=cancellation,
        classification=state["classification"],
        data={
            "sources": state["sources"],
            "current": state["metadata"],
            "guidance": guidance,
            "fields": sorted(harmonize.FIELDS),
            "vocabulary": harmonize.vocabulary(),
            "draft": extracted,
            "output_rules": "Return one entry per field. Use current[field] or null as original. "
            "Unknown fields have null value and empty evidence. Use exact source versions and "
            "JSON pointer or line:N locators. Never cite guidance or examples as source evidence.",
        },
        source_hashes=[s["version"] for s in state["sources"]],
        specification=specification,
        mock={"fields": extracted},
    )
    fields = invocation["data"]["response"]["fields"]
    harmonize.verify_fields(bundle, fields)
    if any(f["original"] != state["metadata"].get(f["field"]) for f in fields):
        raise ValueError("Model returned stale original values")
    # Preserve authoritative observations and conflicts; only a person may resolve them.
    authoritative = {f["field"]: f for f in extracted if f["status"] in {"known", "conflicting"}}
    merged = {f["field"]: f for f in extracted} | {f["field"]: f for f in fields} | authoritative
    with store.write(root) as db:
        if cancellation and cancellation.is_set():
            raise ValueError("Request cancelled before publication")
        current = read(db, subject)
        expect(current, version)
        current.update(fields=list(merged.values()), reviews={}, proposal=invocation["version"])
        return save(
            db,
            current,
            "proposal",
            actor,
            {
                "invocation": invocation["version"],
                "guidance": guidance,
                "fields": list(merged.values()),
            },
        )


def chat(
    root: Path,
    subject: str,
    version: str,
    config: dict[str, Any],
    question: str,
    actor: str,
    cancellation: threading.Event | None = None,
) -> dict[str, Any]:
    state = get(root, subject)
    expect(state, version)
    if not question.strip() or len(question) > 4000:
        raise ValueError("Question must contain 1–4000 characters")
    specification = {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "citations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source_version": {"type": "string"},
                        "locator": {"type": "string"},
                        "quote": {"type": "string"},
                    },
                    "required": ["source_version", "locator", "quote"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["answer", "citations"],
        "additionalProperties": False,
    }
    call = providers.invoke(
        root,
        config,
        task="ask",
        cancellation=cancellation,
        classification=state["classification"],
        data={
            "question": question,
            "sources": state["sources"],
            "fields": state["fields"],
            "conversation": state["messages"][-6:],
        },
        source_hashes=[s["version"] for s in state["sources"]],
        specification=specification,
        mock={
            "answer": "Synthetic response: inspect the source evidence before approving.",
            "citations": [],
        },
    )
    answer = call["data"]["response"]
    sources = {s["version"]: s for s in state["sources"]}
    for citation in answer["citations"]:
        if (
            citation["source_version"] not in sources
            or not citation["quote"]
            or citation["quote"]
            not in harmonize.locate(sources[citation["source_version"]], citation["locator"])
        ):
            raise ValueError("Invalid conversation citation")
    with store.write(root) as db:
        if cancellation and cancellation.is_set():
            raise ValueError("Request cancelled before publication")
        current = read(db, subject)
        expect(current, version)
        message = {"question": question, **answer, "invocation": call["version"], "actor": actor}
        current["messages"] = (current["messages"] + [message])[-20:]
        return save(db, current, "message", actor, message)
