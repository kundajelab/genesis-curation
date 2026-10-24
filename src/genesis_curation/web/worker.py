"""Single durable worker; interrupted jobs are never automatically replayed."""

from __future__ import annotations

import contextlib
import fcntl
import json
import threading
import time
from pathlib import Path
from typing import Any

from ..contracts.records import canonical, fingerprint
from ..curation import harmonize
from ..llm import providers
from ..llm.security import redact
from . import catalog, intake, preparation, runtime, skills, studies


def execute(
    root: Path, config_path: Path, task: dict[str, Any], cancellation: threading.Event | None = None
) -> dict[str, Any]:
    data, kind = task["payload"], task["kind"]
    if kind == "study-intake":
        key = intake.import_accession(root, data["accession"], task["actor"], cancellation)
        task["payload"] = {**data, "study": key, "versions": {}}
        if task.get("id"):
            runtime.progress(root, task["id"], {"study": key, "phase": "preparing"})
        return preparation.run(root, config_path, task, cancellation)
    if kind == "study-prepare":
        return preparation.run(root, config_path, task, cancellation)
    if kind == "accession":
        text, url, media = harmonize.retrieve(data["accession"])
        if media == "application/json":
            rows = json.loads(text)
            if not isinstance(rows, list) or not rows:
                raise ValueError("Accession returned no metadata rows")
            id_column = next(
                (k for k in ("run_accession", "sample_accession") if all(r.get(k) for r in rows)),
                None,
            )
            if id_column is None:
                raise ValueError("Accession response lacks sample/run identifiers")
        else:
            rows, id_column = [{"accession": data["accession"]}], "accession"
        return {
            "text": canonical(rows),
            "filename": data["accession"] + ".json",
            "id_column": id_column,
            "source": {"text": text, "url": url, "media": media},
            "rows": rows,
        }
    config = providers.select(config_path, data["provider"])
    if data.get("provider_hash") and fingerprint(config) != data["provider_hash"]:
        raise ValueError("Provider configuration changed; submit a new job")
    if kind == "probe":
        return providers.probe(root / "catalog", config)
    if kind == "prepare":
        skill = skills.select(root / "skills", data["skill"])
        if skill["sha256"] != data["skill_hash"]:
            raise ValueError("Skill changed; submit a new preparation")
        result: dict[str, Any] = {"prepared": [], "failed": [], "remaining": len(data["records"])}
        runtime.progress(root, task["id"], result)
        for item in data["records"]:
            if cancellation and cancellation.is_set():
                result["cancelled"] = True
                break
            try:
                catalog.run_proposal(
                    root / "catalog",
                    item["subject"],
                    item["version"],
                    config,
                    skill,
                    task["actor"],
                    cancellation=cancellation,
                )
                result["prepared"].append(item["subject"])
            except Exception as error:
                if cancellation and cancellation.is_set():
                    result["cancelled"] = True
                    break
                result["failed"].append(
                    {"subject": item["subject"], "error": redact(str(error))[:1000]}
                )
            result["remaining"] -= 1
            runtime.progress(root, task["id"], result)
        return result
    if kind == "propose":
        skill = skills.select(root / "skills", data["skill"])
        if data.get("skill_hash") and skill["sha256"] != data["skill_hash"]:
            raise ValueError("Skill changed; submit a new job")
        return catalog.run_proposal(
            root / "catalog",
            data["subject"],
            data["version"],
            config,
            skill,
            task["actor"],
            data.get("feedback", ""),
            cancellation,
        )
    return catalog.chat(
        root / "catalog",
        data["subject"],
        data["version"],
        config,
        data["question"],
        task["actor"],
        cancellation,
    )


def once(root: Path, config_path: Path) -> bool:
    task = runtime.claim(root)
    if not task:
        return False
    cancellation, done = threading.Event(), threading.Event()

    def monitor() -> None:
        while not done.wait(0.1):
            if runtime.job(root, task["id"])["cancel"]:
                cancellation.set()
                return

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    try:
        if runtime.job(root, task["id"])["cancel"]:
            raise ValueError("Request cancelled")
        result = execute(root, config_path, task, cancellation)
        if result.get("pending"):
            if runtime.yield_job(root, task, result):
                studies.preparation(root / "catalog", result["study"], {"status": "INTERRUPTED"})
        else:
            runtime.finish(root, task["id"], result)
    except Exception as error:
        if task["kind"] in {"study-intake", "study-prepare"} and task["payload"].get("study"):
            with contextlib.suppress(ValueError):
                studies.preparation(
                    root / "catalog",
                    task["payload"]["study"],
                    {
                        "status": "INTERRUPTED" if cancellation.is_set() else "FAILED",
                    },
                )
        runtime.finish(root, task["id"], None, redact(str(error))[:2000])
    finally:
        done.set()
        watcher.join()
    return True


def run(root: Path, config_path: Path) -> None:
    with (root / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("A worker already owns this application") from None
        runtime.recover(root)
        with runtime.connection(root) as db:
            queued = {row[0] for row in db.execute("SELECT id FROM jobs WHERE status='QUEUED'")}
        studies.recover(root / "catalog", queued)
        while True:
            if not once(root, config_path):
                time.sleep(1)
