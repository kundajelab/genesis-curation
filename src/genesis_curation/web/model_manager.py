"""Durable, allowlisted requests for an optional host-owned local-model controller.

The web process never receives Docker access. Requests cannot specify commands,
images, files, endpoints, credentials, or arbitrary model identifiers.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..llm import local_models

BASELINE = "qwen38-q8"
PROVIDERS = {"qwen-baseline": BASELINE} | {
    p["id"]: "local-" + p["id"] for p in local_models.PRESETS
}


def initialize(root: Path) -> None:
    with sqlite3.connect(root / "app.sqlite", timeout=30) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS model_manager_state
                (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT);
            CREATE TABLE IF NOT EXISTS model_manager_requests
                (id TEXT PRIMARY KEY,preset TEXT,actor TEXT,created REAL,status TEXT,error TEXT);
            CREATE TABLE IF NOT EXISTS model_manager_leases
                (owner TEXT PRIMARY KEY,provider TEXT,expires REAL);
        """)


def state(root: Path) -> dict[str, Any]:
    with sqlite3.connect(root / "app.sqlite", timeout=30) as db:
        row = db.execute("SELECT body FROM model_manager_state WHERE id=1").fetchone()
        pending = db.execute(
            "SELECT id,preset,status,error FROM model_manager_requests "
            "ORDER BY created DESC LIMIT 1"
        ).fetchone()
        evaluation = bool(
            db.execute(
                "SELECT 1 FROM model_manager_leases WHERE expires>?", (time.time(),)
            ).fetchone()
        )
    value = json.loads(row[0]) if row else {}
    return {
        "available": time.time() - value.get("heartbeat", 0) < 30,
        "active_provider": value.get("active_provider"),
        "maintenance": bool(value.get("maintenance")) or evaluation,
        "evaluation": evaluation,
        "request": dict(zip(("id", "preset", "status", "error"), pending, strict=True))
        if pending
        else None,
    }


def request(root: Path, preset: str, actor: str) -> dict[str, str]:
    if not isinstance(preset, str) or preset not in PROVIDERS:
        raise ValueError("Choose a configured local model preset")
    key = str(uuid.uuid4())
    with sqlite3.connect(root / "app.sqlite", timeout=30) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT body FROM model_manager_state WHERE id=1").fetchone()
        if not row or time.time() - json.loads(row[0]).get("heartbeat", 0) >= 30:
            raise ValueError("The host model controller is not running")
        if db.execute(
            "SELECT 1 FROM model_manager_leases WHERE expires>?", (time.time(),)
        ).fetchone():
            raise ValueError("A model evaluation is running; switch after it finishes")
        if db.execute(
            "SELECT 1 FROM model_manager_requests WHERE status IN ('QUEUED','RUNNING')"
        ).fetchone():
            raise ValueError("A model switch is already queued or running")
        db.execute(
            "INSERT INTO model_manager_requests VALUES(?,?,?,?,?,?)",
            (key, preset, actor, time.time(), "QUEUED", None),
        )
    return {"id": key, "provider": PROVIDERS[preset]}


def lease(root: Path, owner: str, provider: str) -> None:
    with sqlite3.connect(root / "app.sqlite", timeout=30) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT body FROM model_manager_state WHERE id=1").fetchone()
        value = json.loads(row[0]) if row else {}
        if time.time() - value.get("heartbeat", 0) >= 30:
            raise ValueError("The host model controller is not running")
        if value.get("active_provider") != provider or value.get("maintenance"):
            raise ValueError("Activate the evaluation provider before reserving inference")
        if (
            db.execute("SELECT 1 FROM jobs WHERE status='RUNNING'").fetchone()
            or db.execute(
                "SELECT 1 FROM model_manager_requests WHERE status IN ('QUEUED','RUNNING')"
            ).fetchone()
            or db.execute(
                "SELECT 1 FROM model_manager_leases WHERE owner!=? AND expires>?",
                (owner, time.time()),
            ).fetchone()
        ):
            raise ValueError("Wait for current work or model switching before evaluation")
        db.execute(
            "INSERT OR REPLACE INTO model_manager_leases VALUES(?,?,?)",
            (owner, provider, time.time() + 120),
        )


@contextlib.contextmanager
def exclusive(root: Path, provider: str) -> Iterator[threading.Event]:
    """Reserve the active model for a benchmark; cancel inference if renewal fails."""
    owner = str(uuid.uuid4())
    lease(root, owner, provider)
    done, cancelled = threading.Event(), threading.Event()

    def renew() -> None:
        while not done.wait(10):
            try:
                lease(root, owner, provider)
            except (ValueError, sqlite3.Error) as _:
                cancelled.set()
                return

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    try:
        yield cancelled
        if cancelled.is_set():
            raise ValueError("Evaluation lease lost; inference was cancelled")
    finally:
        done.set()
        thread.join()
        with sqlite3.connect(root / "app.sqlite", timeout=30) as db:
            db.execute("DELETE FROM model_manager_leases WHERE owner=?", (owner,))
