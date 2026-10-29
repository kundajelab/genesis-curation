"""Cross-study queue with compact summaries and on-demand sample evidence."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from ..contracts.records import canonical, loads
from ..registry import store
from . import mining, studies


def refresh(root: Path) -> None:
    """Recompute only owners changed by source, preparation or review transactions."""
    with contextlib.closing(store.connect(root)) as db:
        if not db.execute("SELECT 1 FROM curation_queue_owners WHERE dirty=1 LIMIT 1").fetchone():
            return
    with store.write(root) as db:
        for owner, kind in db.execute(
            "SELECT owner,kind FROM curation_queue_owners WHERE dirty=1 ORDER BY owner"
        ).fetchall():
            rows: list[dict[str, Any]] = []
            summary: dict[str, Any] = {}
            if kind == "study":
                study = studies.view(db, owner)
                if not study["approval_current"]:
                    summary = {
                        "id": owner,
                        "name": study["name"],
                        "version": study["version"],
                        "status": study["status"],
                        "preparation": study["preparation"]["status"],
                    }
                    if study["preparation"]["status"] == "COMPLETED":
                        for task in study["tasks"]:
                            if task["status"] in {"open", "waiting"}:
                                rows.append(
                                    {
                                        k: task[k]
                                        for k in (
                                            "id",
                                            "study",
                                            "version",
                                            "title",
                                            "reason",
                                            "kind",
                                            "field",
                                            "priority",
                                            "status",
                                        )
                                    }
                                    | {
                                        "study_name": study["name"],
                                        "affected": len(task["targets"]),
                                    }
                                )
            else:
                run = loads(
                    db.execute("SELECT body FROM mining_runs WHERE id=?", (owner,)).fetchone()[0]
                )
                rows = [{**row, "status": "open"} for row in mining.questions(run)]
            db.execute("DELETE FROM curation_queue_tasks WHERE owner=?", (owner,))
            db.executemany(
                "INSERT INTO curation_queue_tasks VALUES(?,?,?,?,?)",
                [
                    (owner, row["id"], row["status"], row["priority"], canonical(row))
                    for row in rows
                ],
            )
            db.execute(
                "UPDATE curation_queue_owners SET dirty=0,body=? WHERE owner=?",
                (canonical(summary), owner),
            )


def listing(root: Path, cursor: int = 0, limit: int = 25, status: str = "open") -> dict[str, Any]:
    if cursor < 0 or not 1 <= limit <= 100 or status not in {"open", "waiting"}:
        raise ValueError("Invalid queue page")
    refresh(root)
    with contextlib.closing(store.connect(root)) as db:
        db.execute("BEGIN")
        total = db.execute(
            "SELECT count(*) FROM curation_queue_tasks WHERE status=?", (status,)
        ).fetchone()[0]
        rows = [
            loads(row[0])
            for row in db.execute(
                "SELECT body FROM curation_queue_tasks WHERE status=? "
                "ORDER BY priority,owner,task LIMIT ? OFFSET ?",
                (status, limit, cursor),
            )
        ]
        owners = [
            loads(row[0])
            for row in db.execute(
                "SELECT body FROM curation_queue_owners WHERE kind='study' "
                "AND body!='{}' ORDER BY owner"
            )
        ]
    return {
        "items": rows,
        "total": total,
        "next_cursor": cursor + limit if cursor + limit < total else None,
        "ready": [
            {k: o[k] for k in ("id", "name", "version")} for o in owners if o["status"] == "ready"
        ],
        "waiting": [
            {"id": o["id"], "name": o["name"], "status": o["preparation"]}
            for o in owners
            if o["preparation"] != "COMPLETED" and o["status"] != "preparing"
        ],
        "preparing": sum(o["status"] == "preparing" for o in owners),
    }


def detail(root: Path, study: str, key: str) -> dict[str, Any]:
    value = studies.get(root, study)
    task = next((t for t in value["tasks"] if t["id"] == key), None)
    if task is None:
        raise ValueError("Task changed; refresh the queue")
    ids = {t["subject"] for t in task["targets"]}
    return {
        "task": task,
        "study": {
            **value,
            "tasks": [task],
            "records": [
                {
                    **r,
                    "sources": [
                        {**source, "data": {**source["data"], "snapshot": ""}}
                        for source in r["sources"]
                    ],
                }
                for r in value["records"]
                if r["id"] in ids
            ],
        },
    }
