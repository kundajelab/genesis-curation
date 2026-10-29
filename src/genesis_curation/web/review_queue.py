"""Cross-study queue with compact summaries and on-demand sample evidence."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from ..registry import store
from . import mining, studies


def listing(root: Path, cursor: int = 0, limit: int = 25, status: str = "open") -> dict[str, Any]:
    if cursor < 0 or not 1 <= limit <= 100 or status not in {"open", "waiting"}:
        raise ValueError("Invalid queue page")
    rows = []
    ready = []
    waiting = []
    preparing = 0
    with contextlib.closing(store.connect(root)) as db:
        db.execute("BEGIN")
        for (key,) in db.execute("SELECT id FROM curation_studies ORDER BY id"):
            study = studies.view(db, key)
            if study["approval_current"]:
                continue
            if study["status"] == "preparing":
                preparing += 1
                continue
            if study["preparation"]["status"] != "COMPLETED":
                waiting.append(
                    {"id": key, "name": study["name"], "status": study["preparation"]["status"]}
                )
                continue
            if study["status"] == "ready":
                ready.append({"id": key, "name": study["name"], "version": study["version"]})
            for task in study["tasks"]:
                if task["status"] == status:
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
                            )
                        }
                        | {"study_name": study["name"], "affected": len(task["targets"])}
                    )
    if status == "open":
        rows.extend(mining.question_rows(root.parent))
    rows.sort(key=lambda t: (t["priority"], t.get("study", t.get("run", "")), t["id"]))
    return {
        "items": rows[cursor : cursor + limit],
        "total": len(rows),
        "next_cursor": cursor + limit if cursor + limit < len(rows) else None,
        "ready": ready,
        "waiting": waiting,
        "preparing": preparing,
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
