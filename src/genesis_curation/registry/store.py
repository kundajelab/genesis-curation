"""Transactional SQLite storage and immutable scientific evidence."""

from __future__ import annotations

import contextlib
import fcntl
import os
import sqlite3
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..contracts.records import canonical, loads, now, validate

SCHEMA_VERSION = 1
APPLICATION_ID = 1195595057
SCHEMA = [
    ("CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)"),
    (
        "CREATE TABLE records (kind TEXT,id TEXT,version TEXT,body TEXT NOT NULL,crea"
        "ted TEXT NOT NULL,PRIMARY KEY(kind,id,version))"
    ),
    (
        "CREATE TRIGGER immutable_records_update BEFORE UPDATE ON records BEGIN SELEC"
        "T RAISE(ABORT,'immutable record'); END"
    ),
    (
        "CREATE TRIGGER immutable_records_delete BEFORE DELETE ON records BEGIN SELEC"
        "T RAISE(ABORT,'immutable record'); END"
    ),
    ("CREATE INDEX records_kind_version ON records(kind,version)"),
    ("CREATE TABLE service_heads (id TEXT PRIMARY KEY,plan TEXT NOT NULL,state TEXT NOT NULL)"),
    (
        "CREATE TABLE curation_subjects (id TEXT PRIMARY KEY, version TEXT NOT NULL, "
        "body TEXT NOT NULL)"
    ),
    (
        "CREATE TABLE curation_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, su"
        "bject TEXT NOT NULL REFERENCES curation_subjects(id), version TEXT UNIQUE NO"
        "T NULL)"
    ),
    (
        "CREATE TRIGGER immutable_curation_events_update BEFORE UPDATE ON curation_ev"
        "ents BEGIN SELECT RAISE(ABORT,'immutable curation event'); END"
    ),
    (
        "CREATE TRIGGER immutable_curation_events_delete BEFORE DELETE ON curation_ev"
        "ents BEGIN SELECT RAISE(ABORT,'immutable curation event'); END"
    ),
    ("CREATE TABLE curation_studies (id TEXT PRIMARY KEY, body TEXT NOT NULL)"),
    (
        "CREATE TABLE curation_members (study TEXT REFERENCES curation_studies(id), s"
        "ubject TEXT REFERENCES curation_subjects(id), PRIMARY KEY(study,subject))"
    ),
    ("CREATE INDEX curation_members_subject ON curation_members(subject)"),
    (
        "CREATE TABLE curation_drafts (actor TEXT, task TEXT, body TEXT NOT NULL, PRI"
        "MARY KEY(actor,task))"
    ),
    (
        "CREATE TABLE curation_study_events (sequence INTEGER PRIMARY KEY AUTOINCREME"
        "NT, study TEXT REFERENCES curation_studies(id), body TEXT NOT NULL)"
    ),
    (
        "CREATE TABLE curation_releases (version TEXT PRIMARY KEY, study TEXT REFEREN"
        "CES curation_studies(id), body TEXT NOT NULL)"
    ),
    (
        "CREATE TRIGGER immutable_study_events_update BEFORE UPDATE ON curation_study"
        "_events BEGIN SELECT RAISE(ABORT,'immutable study event'); END"
    ),
    (
        "CREATE TRIGGER immutable_study_events_delete BEFORE DELETE ON curation_study"
        "_events BEGIN SELECT RAISE(ABORT,'immutable study event'); END"
    ),
    (
        "CREATE TRIGGER immutable_releases_update BEFORE UPDATE ON curation_releases "
        "BEGIN SELECT RAISE(ABORT,'immutable study release'); END"
    ),
    (
        "CREATE TRIGGER immutable_releases_delete BEFORE DELETE ON curation_releases "
        "BEGIN SELECT RAISE(ABORT,'immutable study release'); END"
    ),
    ("CREATE TABLE curation_audits (id TEXT PRIMARY KEY, body TEXT NOT NULL)"),
    (
        "CREATE TABLE curation_audit_answers (audit TEXT REFERENCES curation_audits(i"
        "d), sample TEXT, actor TEXT, body TEXT NOT NULL, PRIMARY KEY(audit,sample))"
    ),
    (
        "CREATE TABLE curation_audit_events (sequence INTEGER PRIMARY KEY AUTOINCREME"
        "NT, audit TEXT REFERENCES curation_audits(id), body TEXT NOT NULL)"
    ),
    (
        "CREATE TRIGGER immutable_audit_events_update BEFORE UPDATE ON curation_audit"
        "_events BEGIN SELECT RAISE(ABORT,'immutable audit event'); END"
    ),
    (
        "CREATE TRIGGER immutable_audit_events_delete BEFORE DELETE ON curation_audit"
        "_events BEGIN SELECT RAISE(ABORT,'immutable audit event'); END"
    ),
    ("CREATE TABLE mining_runs (id TEXT PRIMARY KEY, body TEXT NOT NULL)"),
    (
        "CREATE TABLE mining_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, run "
        "TEXT REFERENCES mining_runs(id), body TEXT NOT NULL)"
    ),
    (
        "CREATE TRIGGER immutable_mining_events_update BEFORE UPDATE ON mining_events"
        " BEGIN SELECT RAISE(ABORT,'immutable mining event'); END"
    ),
    (
        "CREATE TRIGGER immutable_mining_events_delete BEFORE DELETE ON mining_events"
        " BEGIN SELECT RAISE(ABORT,'immutable mining event'); END"
    ),
    (
        "CREATE TABLE curation_result_rows (subject TEXT PRIMARY KEY REFERENCES curat"
        "ion_subjects(id), modality TEXT NOT NULL,species TEXT NOT NULL,eligibility T"
        "EXT NOT NULL,study TEXT NOT NULL,body TEXT NOT NULL)"
    ),
    ("CREATE INDEX result_modality ON curation_result_rows(modality,subject)"),
    ("CREATE INDEX result_species ON curation_result_rows(modality,species,subject)"),
    ("CREATE INDEX result_eligibility ON curation_result_rows(modality,eligibility,subject)"),
    ("CREATE INDEX result_study ON curation_result_rows(modality,study,subject)"),
    (
        "CREATE TABLE curation_queue_owners (owner TEXT PRIMARY KEY,kind TEXT NOT NUL"
        "L,dirty INTEGER NOT NULL DEFAULT 1,body TEXT NOT NULL DEFAULT '{}')"
    ),
    (
        "CREATE TABLE curation_queue_tasks (owner TEXT,task TEXT,status TEXT,priority"
        " INTEGER,body TEXT NOT NULL,PRIMARY KEY(owner,task))"
    ),
    ("CREATE INDEX queue_page ON curation_queue_tasks(status,priority,owner,task)"),
    ("CREATE INDEX queue_dirty ON curation_queue_owners(dirty,owner)"),
    (
        "CREATE TRIGGER queue_study_insert AFTER INSERT ON curation_studies BEGIN INS"
        "ERT INTO curation_queue_owners(owner,kind) VALUES(new.id,'study'); END"
    ),
    (
        "CREATE TRIGGER queue_study_update AFTER UPDATE ON curation_studies BEGIN UPD"
        "ATE curation_queue_owners SET dirty=1 WHERE owner=new.id; END"
    ),
    (
        "CREATE TRIGGER queue_subject_update AFTER UPDATE ON curation_subjects BEGIN "
        "UPDATE curation_queue_owners SET dirty=1 WHERE owner IN (SELECT study FROM c"
        "uration_members WHERE subject=new.id); END"
    ),
    (
        "CREATE TRIGGER queue_member_insert AFTER INSERT ON curation_members BEGIN UP"
        "DATE curation_queue_owners SET dirty=1 WHERE owner=new.study; END"
    ),
    (
        "CREATE TRIGGER queue_mining_insert AFTER INSERT ON mining_runs BEGIN INSERT "
        "INTO curation_queue_owners(owner,kind) VALUES(new.id,'mining'); END"
    ),
    (
        "CREATE TRIGGER queue_mining_update AFTER UPDATE ON mining_runs BEGIN UPDATE "
        "curation_queue_owners SET dirty=1 WHERE owner=new.id; END"
    ),
    ("CREATE TABLE workspace_imports (id TEXT PRIMARY KEY,body TEXT NOT NULL)"),
    (
        "CREATE TABLE workspace_rows (id TEXT PRIMARY KEY,batch TEXT NOT NULL,tab TEX"
        "T NOT NULL,row_number INTEGER NOT NULL,modality TEXT NOT NULL,species TEXT N"
        "OT NULL,study TEXT NOT NULL,entity TEXT NOT NULL,subject TEXT NOT NULL DEFAU"
        "LT '',status TEXT NOT NULL,body TEXT NOT NULL)"
    ),
    ("CREATE INDEX workspace_modality ON workspace_rows(modality,id)"),
    ("CREATE INDEX workspace_species ON workspace_rows(modality,species,id)"),
    ("CREATE INDEX workspace_entity ON workspace_rows(entity,id)"),
    ("CREATE INDEX workspace_subject ON workspace_rows(subject)"),
    ("CREATE INDEX workspace_status ON workspace_rows(status,modality,id)"),
    (
        "CREATE TABLE workspace_campaigns (id TEXT PRIMARY KEY,status TEXT NOT NULL,b"
        "ody TEXT NOT NULL)"
    ),
    (
        "CREATE TABLE workspace_items (campaign TEXT,entity TEXT,status TEXT NOT NULL"
        ",body TEXT NOT NULL,PRIMARY KEY(campaign,entity))"
    ),
    ("CREATE INDEX workspace_pending ON workspace_items(campaign,status,entity)"),
    (
        "CREATE TABLE workspace_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT,ca"
        "mpaign TEXT NOT NULL,entity TEXT NOT NULL,created TEXT NOT NULL,body TEXT NO"
        "T NULL)"
    ),
    ("CREATE INDEX workspace_event_page ON workspace_events(campaign,sequence)"),
    ("CREATE INDEX workspace_event_entity ON workspace_events(entity,sequence)"),
    (
        "CREATE TRIGGER immutable_workspace_events_update BEFORE UPDATE ON workspace_"
        "events BEGIN SELECT RAISE(ABORT,'immutable workspace event'); END"
    ),
    (
        "CREATE TRIGGER immutable_workspace_events_delete BEFORE DELETE ON workspace_"
        "events BEGIN SELECT RAISE(ABORT,'immutable workspace event'); END"
    ),
]


def connect(directory: Path, *, readonly: bool = True) -> sqlite3.Connection:
    path = directory.resolve() / "registry.sqlite"
    db = sqlite3.connect(
        path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"), uri=True, timeout=30
    )
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    if (
        db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
        or db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
    ):
        db.close()
        raise ValueError("Unsupported metadata database; initialize a fresh v1 workspace")
    if readonly:
        db.execute("PRAGMA query_only=ON")
    return db


@contextlib.contextmanager
def lock(directory: Path) -> Iterator[None]:
    with (directory / "writer.lock").open("a") as stream:
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError(
                        "Registry writer is busy; retry after the current writer finishes"
                    ) from None
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


@contextlib.contextmanager
def write(directory: Path) -> Iterator[sqlite3.Connection]:
    with lock(directory), contextlib.closing(connect(directory, readonly=False)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        yield db


def initialize(directory: Path) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    with lock(directory), contextlib.closing(sqlite3.connect(directory / "registry.sqlite")) as db:
        if (
            db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone()
            or db.execute("PRAGMA user_version").fetchone()[0]
            or db.execute("PRAGMA application_id").fetchone()[0]
        ):
            if (
                db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
                or db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
            ):
                raise ValueError("Unsupported metadata database; initialize a fresh v1 workspace")
        else:
            with db:
                for sql in SCHEMA:
                    db.execute(sql)
                db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                db.execute(f"PRAGMA application_id={APPLICATION_ID}")
                db.execute("INSERT INTO meta VALUES('registry_id',?)", (str(uuid.uuid4()),))
        registry_id = db.execute("SELECT value FROM meta WHERE key='registry_id'").fetchone()[0]
    return {
        "registry_id": registry_id,
        "schema_version": SCHEMA_VERSION,
        "directory": str(directory),
    }


def put(db: sqlite3.Connection, value: dict[str, Any]) -> None:
    validate(value)
    db.execute(
        "INSERT OR IGNORE INTO records VALUES(?,?,?,?,?)",
        (value["kind"], value["id"], value["version"], canonical(value), now()),
    )


def get(db: sqlite3.Connection, kind: str, version: str) -> dict[str, Any]:
    row = db.execute(
        "SELECT body FROM records WHERE kind=? AND version=?", (kind, version)
    ).fetchone()
    if not row:
        raise ValueError(f"Missing {kind} revision: {version}")
    return loads(row[0])


def backup(directory: Path, destination: Path) -> None:
    if destination.exists():
        raise ValueError("Backup destination already exists")
    temporary = destination.with_name(destination.name + ".partial-" + uuid.uuid4().hex)
    try:
        with (
            contextlib.closing(connect(directory)) as source,
            contextlib.closing(sqlite3.connect(temporary)) as target,
        ):
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Backup integrity check failed")
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def restore(source: Path, directory: Path) -> None:
    if directory.exists():
        raise ValueError("Restore requires a new destination directory")
    with contextlib.closing(
        sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    ) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if (
            version != SCHEMA_VERSION
            or db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
            or db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or db.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise ValueError("Backup has an unsupported schema or failed integrity checks")
        directory.mkdir(parents=True)
        with contextlib.closing(sqlite3.connect(directory / "registry.sqlite")) as target:
            db.backup(target)
