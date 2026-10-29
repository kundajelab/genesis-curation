"""Local application sessions and durable jobs; inference never holds a write lock."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import secrets
import sqlite3
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..contracts.records import canonical, loads, now
from ..registry import store


@contextlib.contextmanager
def connection(root: Path) -> Iterator[sqlite3.Connection]:
    with contextlib.closing(sqlite3.connect(root / "app.sqlite", timeout=30)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        with db:
            yield db


def initialize(root: Path, username: str, password: str) -> None:
    if len(password) < 14 or not username.strip():
        raise ValueError("Use a named curator and a password of at least 14 characters")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    store.initialize(root / "catalog")
    with connection(root) as db:
        db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS accounts (name TEXT PRIMARY KEY, salt TEXT, password TEXT);
            CREATE TABLE IF NOT EXISTS sessions
                (token TEXT PRIMARY KEY, csrf TEXT, actor TEXT, expires REAL);
            CREATE TABLE IF NOT EXISTS attempts
                (address TEXT PRIMARY KEY, count INTEGER, started REAL);
            CREATE TABLE IF NOT EXISTS jobs
                (id TEXT PRIMARY KEY, kind TEXT, actor TEXT, payload TEXT,
                status TEXT, created TEXT, updated TEXT, result TEXT, error TEXT,
                cancel INTEGER DEFAULT 0);
        """)
        if db.execute("SELECT 1 FROM accounts").fetchone():
            raise ValueError("Account exists; use reset-password over SSH")
        salt = secrets.token_hex(16)
        db.execute(
            "INSERT INTO accounts VALUES(?,?,?)", (username, salt, password_hash(password, salt))
        )
    (root / "app.sqlite").chmod(0o600)


def password_hash(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def reset(root: Path, username: str, password: str) -> None:
    if len(password) < 14:
        raise ValueError("Password must have at least 14 characters")
    salt = secrets.token_hex(16)
    with connection(root) as db:
        result = db.execute(
            "UPDATE accounts SET salt=?,password=? WHERE name=?",
            (salt, password_hash(password, salt), username),
        )
        if result.rowcount != 1:
            raise ValueError("Unknown curator")
        db.execute("DELETE FROM sessions")


def login(root: Path, username: str, password: str, address: str) -> tuple[str, str]:
    stamp = time.time()
    with connection(root) as db:
        db.execute("BEGIN IMMEDIATE")
        attempt = db.execute("SELECT * FROM attempts WHERE address=?", (address,)).fetchone()
        count = attempt["count"] if attempt and stamp - attempt["started"] < 900 else 0
        if count >= 10:
            raise ValueError("Too many login attempts; wait 15 minutes")
        db.execute(
            "INSERT INTO attempts VALUES(?,?,?) ON CONFLICT(address) DO UPDATE SET "
            "count=excluded.count,started=excluded.started",
            (address, count + 1, attempt["started"] if count else stamp),
        )
        account = db.execute("SELECT * FROM accounts WHERE name=?", (username,)).fetchone()
    calculated = password_hash(password, account["salt"] if account else "00" * 16)
    if not account or not hmac.compare_digest(calculated, account["password"]):
        raise ValueError("Invalid login")
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with connection(root) as db:
        db.execute("DELETE FROM attempts WHERE address=?", (address,))
        db.execute("DELETE FROM sessions WHERE expires<?", (stamp,))
        db.execute(
            "INSERT INTO sessions VALUES(?,?,?,?)",
            (hashlib.sha256(token.encode()).hexdigest(), csrf, username, stamp + 12 * 3600),
        )
    return token, csrf


def session(root: Path, token: str) -> dict[str, Any] | None:
    with connection(root) as db:
        row = db.execute(
            "SELECT * FROM sessions WHERE token=? AND expires>?",
            (hashlib.sha256(token.encode()).hexdigest(), time.time()),
        ).fetchone()
        return dict(row) if row else None


def logout(root: Path, token: str) -> None:
    with connection(root) as db:
        db.execute(
            "DELETE FROM sessions WHERE token=?", (hashlib.sha256(token.encode()).hexdigest(),)
        )


def enqueue(root: Path, kind: str, actor: str, payload: dict[str, Any]) -> str:
    if kind not in {
        "propose",
        "chat",
        "accession",
        "probe",
        "prepare",
        "study-prepare",
        "study-intake",
        "mining",
    }:
        raise ValueError("Unknown job type")
    key = str(uuid.uuid4())
    with connection(root) as db:
        db.execute("BEGIN IMMEDIATE")
        if (
            db.execute("SELECT count(*) FROM jobs WHERE status IN ('QUEUED','RUNNING')").fetchone()[
                0
            ]
            >= 32
        ):
            raise ValueError("Job queue is full")
        db.execute(
            "INSERT INTO jobs(id,kind,actor,payload,status,created,updated) VALUES(?,?,?,?,?,?,?)",
            (key, kind, actor, canonical(payload), "QUEUED", now(), now()),
        )
    return key


def jobs(root: Path) -> list[dict[str, Any]]:
    with connection(root) as db:
        return [
            dict(r)
            for r in db.execute(
                "SELECT id,kind,status,created,updated,error FROM jobs "
                "ORDER BY created DESC LIMIT 100"
            )
        ]


def job(root: Path, key: str) -> dict[str, Any]:
    with connection(root) as db:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (key,)).fetchone()
    if not row:
        raise ValueError("Unknown job")
    return {
        **dict(row),
        "payload": loads(row["payload"]),
        "result": loads(row["result"]) if row["result"] else None,
    }


def claim(root: Path) -> dict[str, Any] | None:
    with connection(root) as db:
        db.execute("BEGIN IMMEDIATE")
        condition = "1"
        parameters: list[str] = []
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='model_manager_state'").fetchone():
            if db.execute(
                "SELECT 1 FROM model_manager_leases WHERE expires>?", (time.time(),)
            ).fetchone():
                return None
            manager = db.execute("SELECT body FROM model_manager_state WHERE id=1").fetchone()
            if manager:
                status = loads(manager[0])
                if status.get("maintenance") or time.time() - status.get("heartbeat", 0) >= 30:
                    return None
                managed = status.get("managed_providers", [])
                if managed:
                    markers = ",".join("?" for _ in managed)
                    condition = (
                        f"(COALESCE(json_extract(payload,'$.provider'),'') NOT IN ({markers}) "
                        "OR json_extract(payload,'$.provider')=?) "
                        "AND NOT (kind='mining' AND json_extract(payload,'$.provider') IS NULL)"
                    )
                    parameters = [*managed, status["active_provider"]]
        row = db.execute(
            "SELECT * FROM jobs WHERE status='QUEUED' AND cancel=0 AND "
            + condition
            + " ORDER BY CASE WHEN kind IN ('propose','chat','probe') "
            "OR json_extract(payload,'$.field') IS NOT NULL THEN 0 ELSE 1 END,updated LIMIT 1",
            parameters,
        ).fetchone()
        if not row:
            return None
        db.execute("UPDATE jobs SET status='RUNNING',updated=? WHERE id=?", (now(), row["id"]))
    return {**dict(row), "payload": loads(row["payload"])}


def cancel(root: Path, key: str) -> None:
    with connection(root) as db:
        db.execute(
            "UPDATE jobs SET cancel=1,status=CASE WHEN status='QUEUED' THEN 'CANCELLED' "
            "ELSE status END,updated=? WHERE id=?",
            (now(), key),
        )


def progress(root: Path, key: str, result: dict[str, Any]) -> None:
    with connection(root) as db:
        db.execute(
            "UPDATE jobs SET result=?,updated=? WHERE id=? AND status='RUNNING'",
            (canonical(result), now(), key),
        )


def finish(root: Path, key: str, result: dict[str, Any] | None, error: str | None = None) -> None:
    with connection(root) as db:
        row = db.execute("SELECT cancel FROM jobs WHERE id=?", (key,)).fetchone()
        state = "SUCCEEDED" if result is not None else "CANCELLED" if row[0] else "FAILED"
        if result and result.get("cancelled"):
            state = "CANCELLED"
        db.execute(
            "UPDATE jobs SET status=?,updated=?,result=COALESCE(?,result),error=? WHERE id=?",
            (state, now(), canonical(result) if result is not None else None, error, key),
        )


def recover(root: Path) -> None:
    with connection(root) as db:
        db.execute(
            "UPDATE jobs SET status='INTERRUPTED',error='Worker restarted; inspect history "
            "before manually starting a new job',updated=? WHERE status='RUNNING' "
            "OR (status='QUEUED' AND json_extract(result,'$.pending')=1)",
            (now(),),
        )


def yield_job(root: Path, task: dict[str, Any], result: dict[str, Any]) -> bool:
    """Checkpoint a study job and release the worker between inference batches."""
    is_mining = task["kind"] == "mining"
    payload = task["payload"] if is_mining else {**task["payload"], "study": result["study"]}
    with connection(root) as db:
        db.execute(
            "UPDATE jobs SET status=CASE WHEN cancel=1 THEN 'CANCELLED' ELSE 'QUEUED' END,"
            "kind=?,payload=?,result=?,updated=? WHERE id=?",
            (
                "mining" if is_mining else "study-prepare",
                canonical(payload),
                canonical(result),
                now(),
                task["id"],
            ),
        )
        return bool(db.execute("SELECT cancel FROM jobs WHERE id=?", (task["id"],)).fetchone()[0])
