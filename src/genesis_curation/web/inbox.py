"""A review inbox derived from current evidence and durable human decisions."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from ..contracts.records import loads, now
from ..curation import harmonize
from ..registry import store
from . import catalog


def listing(root: Path) -> dict[str, Any]:
    tasks = []
    unprepared = []
    with contextlib.closing(store.connect(root)) as db:
        rows = db.execute("SELECT body FROM curation_subjects ORDER BY rowid DESC LIMIT 1000")
        for row in rows:
            state = loads(row[0])
            if not state["proposal"] and not state["reviews"]:
                unprepared.append(state["id"])
            groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for field in state["fields"]:
                review = state["reviews"].get(field["field"])
                status = (
                    "waiting"
                    if review and review["action"] == "request-info"
                    else "done"
                    if review
                    else "open"
                )
                kind = (
                    "conflict"
                    if field["status"] == "conflicting"
                    else "missing"
                    if field["status"] == "unknown"
                    else "suggestions"
                )
                groups.setdefault((kind, status), []).append({**field, "review": review})
            for (kind, status), fields in groups.items():
                tasks.append(
                    {
                        "id": f"{state['id']}:{kind}:{status}",
                        "subject": state["id"],
                        "name": state["name"],
                        "version": state["version"],
                        "prepared": bool(state["proposal"]),
                        "classification": state["classification"],
                        "kind": kind,
                        "status": status,
                        "fields": fields,
                    }
                )
    return {"tasks": tasks, "unprepared": unprepared}


def resolve(
    root: Path, subject: str, version: str, fields: list[str], action: str, reason: str, actor: str
) -> dict[str, Any]:
    """Validate the entire selected set before recording one atomic human decision."""
    if (
        not isinstance(fields, list)
        or not fields
        or len(fields) > len(harmonize.FIELDS)
        or any(not isinstance(field, str) for field in fields)
        or len(set(fields)) != len(fields)
        or action not in {"approve", "reject", "request-info"}
        or not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > 4000
    ):
        raise ValueError("Select fields, a review action and a reason")
    with store.write(root) as db:
        state = catalog.read(db, subject)
        catalog.expect(state, version)
        selected = {field["field"]: field for field in state["fields"]}
        decisions: list[dict[str, Any]] = []
        for name in fields:
            if name not in selected:
                raise ValueError("Unknown field")
            field = selected[name]
            harmonize.verify_fields({"data": {"sources": state["sources"]}}, [field])
            if action == "approve" and field["status"] not in {"known", "not_applicable"}:
                raise ValueError("Uncertain fields cannot be approved as a group")
            decisions.append(
                {
                    "action": action,
                    "reason": reason.strip(),
                    "actor": actor,
                    "created": now(),
                    "field": field,
                    "original_proposal": field,
                    "source_versions": [s["version"] for s in state["sources"]],
                }
            )
        for decision in decisions:
            name = decision["field"]["field"]
            state["reviews"][name] = decision
            if action == "approve":
                state["metadata"][name] = decision["field"]["value"]
            catalog.save(db, state, "decision", actor, decision)
        return state
