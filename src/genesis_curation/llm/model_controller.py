#!/usr/bin/env python3
"""Host-owned controller for the fixed local-model catalog. Never run inside the web app."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sqlite3
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from ..web import model_manager
from . import local_models


class Controller:
    def __init__(self, web: Path) -> None:
        self.web, self.root = web, web / "state"
        if not (self.root / "app.sqlite").is_file():
            raise ValueError("Initialize the curation runtime first")
        model_manager.initialize(self.root)
        self.db_path = self.root / "app.sqlite"
        self.lock = threading.Lock()
        self.name = "genesis-model-" + hashlib.sha256(str(web.resolve()).encode()).hexdigest()[:12]
        self.baseline = json.loads((web / "config/model-runtime.json").read_text())
        if "@sha256:" not in self.baseline["image"]:
            raise ValueError("Pin the inference container by digest")
        self.baseline["models"] = str((web / "models").resolve())
        self.port = int(self.baseline.get("port", 19091))
        if not 1024 <= self.port <= 65535:
            raise ValueError("Use an unprivileged inference port")
        filename = self.baseline["filename"]
        if Path(filename).name != filename:
            raise ValueError("The model filename must be inside runtime/models")
        with (web / "models" / filename).open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != self.baseline["sha256"]:
                raise ValueError("Model checksum does not match configuration")
        secret = web / "config/secrets/local-model.key"
        if not secret.exists():
            import secrets

            descriptor = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as output:
                output.write(secrets.token_urlsafe(32) + "\n")
        with sqlite3.connect(self.db_path, timeout=30) as db:
            row = db.execute("SELECT body FROM model_manager_state WHERE id=1").fetchone()
        self.value: dict[str, Any] = (
            json.loads(row[0])
            if row
            else {"active_provider": model_manager.BASELINE, "maintenance": True}
        )
        self.value["managed_providers"] = list(model_manager.PROVIDERS.values())
        self.configure_providers()
        status_path = web / "config/local-model-status.json"
        statuses = json.loads(status_path.read_text()) if status_path.exists() else {}
        for item in local_models.PRESETS:
            if local_models.verify(web / "models" / item["filename"], item):
                statuses[item["id"]] = {**statuses.get(item["id"], {}), "sha256": item["sha256"]}
            else:
                statuses.pop(item["id"], None)
        self.atomic(status_path, statuses)

    @staticmethod
    def docker(*args: str) -> str:
        return subprocess.check_output(
            ["docker", *args], text=True, stderr=subprocess.STDOUT
        ).strip()

    @staticmethod
    def atomic(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".new")
        temporary.write_text(json.dumps(value, indent=2) + "\n")
        temporary.chmod(0o600)
        temporary.replace(path)

    def configure_providers(self) -> None:
        path = self.web / "config/providers.json"
        configs = json.loads(path.read_text())
        baseline = next(
            (p for p in configs["providers"] if p["id"] == model_manager.BASELINE), None
        )
        if baseline is None:
            baseline = {
                "id": model_manager.BASELINE,
                "mode": "live",
                "external": False,
                "endpoint": f"http://127.0.0.1:{self.port}/v1",
                "protocol": "openai-chat",
                "model": self.baseline["alias"],
                "model_revision": self.baseline["sha256"],
                "serving_revision": self.baseline["image"],
                "credential": {
                    "file": str(self.web / "config/secrets/local-model.key"),
                },
                "allowed_tasks": ["probe", "metadata", "diagnose", "ask"],
                "allowed_classifications": ["public", "internal", "local-only"],
                "context_chars": 131072,
                "context_window_tokens": self.baseline.get("context", 8192),
                "max_input_tokens": self.baseline.get("context", 8192),
                "max_tokens": 2048,
                "timeout_seconds": 180,
                "max_retries": 0,
                "concurrency": 1,
                "format_repair": False,
                "token_counter": "llama",
                "mock_response": None,
                "capabilities": {"json_schema": True, "tools": False, "streaming": False},
            }
            configs["providers"].append(baseline)
        ids = {p["id"] for p in configs["providers"]}
        for preset in local_models.PRESETS:
            key = model_manager.PROVIDERS[preset["id"]]
            if key not in ids:
                configs["providers"].append(
                    {
                        **baseline,
                        "id": key,
                        "model": preset["id"],
                        "model_revision": preset["revision"],
                        "context_window_tokens": self.baseline.get("context", 8192),
                        "max_input_tokens": self.baseline.get("context", 8192),
                        "chat_template_kwargs": {"enable_thinking": False},
                    }
                )
        self.atomic(path, configs)

    def publish(self, **changes: object) -> None:
        with self.lock:
            self.value.update(changes, heartbeat=time.time())
            with sqlite3.connect(self.db_path, timeout=30) as db:
                db.execute(
                    "INSERT INTO model_manager_state VALUES(1,?) "
                    "ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                    (json.dumps(self.value),),
                )

    def http(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        key = (self.web / "config/secrets/local-model.key").read_text().strip()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            value = json.load(response)
            return {"slots": value} if isinstance(value, list) else value

    def probe(self, alias: str) -> dict[str, Any]:
        models = self.http("/v1/models")
        if alias not in [m["id"] for m in models["data"]]:
            raise ValueError("The server is running a different model")
        context = self.http("/props")["default_generation_settings"]["n_ctx"]
        if context < self.baseline.get("context", 8192):
            raise ValueError("The server did not provide the requested context")
        if not self.http("/tokenize", {"content": "Arabidopsis root"}).get("tokens"):
            raise ValueError("Tokenizer probe failed")
        start = time.monotonic()
        response = self.http(
            "/v1/chat/completions",
            {
                "model": alias,
                "messages": [
                    {
                        "role": "user",
                        "content": 'Extract tissue from the exact attribute "tissue: root". '
                        'Return only {"tissue":"root"}.',
                    }
                ],
                "temperature": 0,
                "max_tokens": 128,
                "chat_template_kwargs": {"enable_thinking": False},
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "metadata",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {"tissue": {"type": "string"}},
                            "required": ["tissue"],
                            "additionalProperties": False,
                        },
                    },
                },
            },
        )
        if json.loads(response["choices"][0]["message"]["content"]) != {"tissue": "root"}:
            raise ValueError("Schema-constrained extraction probe failed")
        return {
            "tested_context": context,
            "json_probe": True,
            "probe_seconds": time.monotonic() - start,
            "biological_accuracy": "NOT_EVALUATED",
        }

    def start(self, filename: str, alias: str) -> None:
        gpu = bool(self.baseline.get("gpu", False))
        self.docker(
            "run",
            "-d",
            "--name",
            self.name,
            "--label",
            "com.docker.compose.project=genesis-curation-web",
            "--label",
            "genesis.runtime=" + str(self.web.resolve()),
            "--restart",
            "unless-stopped",
            "--log-driver",
            "json-file",
            "--log-opt",
            "max-size=10m",
            "--log-opt",
            "max-file=3",
            "--network",
            "host",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            str(self.baseline.get("memory", "16g")),
            "--cpus",
            str(self.baseline.get("cpus", 4)),
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            *(["--gpus", "all"] if gpu else []),
            "-e",
            "TMPDIR=/scratch",
            "-e",
            "CUDA_CACHE_PATH=/scratch/cuda",
            "-v",
            self.baseline["models"] + ":/models:ro",
            "-v",
            str(self.web / "config") + ":/config:ro",
            "-v",
            str(self.web / "tmp") + ":/scratch",
            self.baseline["image"],
            "--model",
            "/models/" + filename,
            "--alias",
            alias,
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--api-key-file",
            "/config/secrets/local-model.key",
            "--ctx-size",
            str(self.baseline.get("context", 8192)),
            "--parallel",
            "1",
            "--threads",
            str(self.baseline.get("cpus", 4)),
            "--n-gpu-layers",
            "99" if gpu else "0",
            "--jinja",
            "--no-webui",
        )

    def owned(self, container: dict[str, Any]) -> bool:
        labels = container.get("Config", {}).get("Labels") or {}
        return labels.get("com.docker.compose.project") == "genesis-curation-web" and labels.get(
            "genesis.runtime"
        ) == str(self.web.resolve())

    def rollback(self) -> None:
        backup = self.value.get("rollback_container")
        if not backup:
            return
        if not backup.startswith("genesis-model-rollback-"):
            raise ValueError("Invalid rollback container identity")
        exists = self.docker(
            "ps", "-a", "--filter", "name=^/" + backup + "$", "--format", "{{.ID}}"
        )
        if exists:
            owner = json.loads(self.docker("inspect", backup))[0]
            if not self.owned(owner):
                raise ValueError("Rollback container is not owned by this deployment")
            current = self.docker(
                "ps", "-a", "--filter", "name=^/" + self.name + "$", "--format", "{{.ID}}"
            )
            if current:
                owner = json.loads(self.docker("inspect", self.name))[0]
                if not self.owned(owner):
                    raise ValueError("Current container is not owned by this deployment")
                self.docker("rm", "-f", self.name)
            self.docker("rename", backup, self.name)
            self.docker("start", self.name)
            self.publish(active_provider=self.value["previous_provider"])
        # If rename never happened, or a successful switch already removed its backup,
        # only resume the queue after confirming the persisted active model identity.
        active = self.value["active_provider"]
        alias = (
            self.baseline["alias"]
            if active == model_manager.BASELINE
            else active.removeprefix("local-")
        )
        for _ in range(180):
            try:
                if alias in [m["id"] for m in self.http("/v1/models")["data"]]:
                    self.publish(rollback_container=None)
                    return
            except (OSError, ValueError, KeyError) as _:
                pass
            time.sleep(1)
        raise ValueError("Rollback model is not healthy; queue remains paused")

    def switch(self, key: str, preset_id: str) -> None:
        previous = self.value["active_provider"]
        self.publish(maintenance=True)
        for _ in range(360):
            with sqlite3.connect(self.db_path, timeout=30) as db:
                running = db.execute("SELECT count(*) FROM jobs WHERE status='RUNNING'").fetchone()[
                    0
                ]
            slots = self.http("/slots").get("slots")
            if not isinstance(slots, list) or not all(
                isinstance(slot, dict) and type(slot.get("is_processing")) is bool for slot in slots
            ):
                raise ValueError("Cannot verify inference is idle; model unchanged")
            if not running and not any(slot["is_processing"] for slot in slots):
                break
            time.sleep(1)
        else:
            raise ValueError("Running work did not drain; model unchanged")
        if preset_id == "default":
            item = self.baseline
        else:
            item = local_models.preset(preset_id)
        model = Path(self.baseline["models"]) / item["filename"]
        with model.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != item["sha256"]:
                raise ValueError("Model checksum differs from the approved artifact")
        alias = item.get("alias", preset_id)
        current = json.loads(self.docker("inspect", self.name))[0]
        if not self.owned(current):
            raise ValueError("Refusing to replace a container owned by another deployment")
        backup = "genesis-model-rollback-" + key
        self.publish(rollback_container=backup, previous_provider=previous)
        try:
            self.docker("rename", self.name, backup)
            self.docker("stop", backup)
            self.start(item["filename"], alias)
            for _ in range(360):
                try:
                    if self.http("/health").get("status") == "ok":
                        break
                except (OSError, ValueError) as _:
                    pass
                time.sleep(1)
            else:
                raise ValueError("Model startup timed out")
            receipt = self.probe(alias)
            path = self.web / "config/local-model-status.json"
            statuses = json.loads(path.read_text()) if path.exists() else {}
            statuses[preset_id] = {"sha256": item["sha256"], **receipt}
            self.atomic(path, statuses)
            self.publish(active_provider=model_manager.PROVIDERS[preset_id])
            self.docker("rm", backup)
            self.publish(rollback_container=None)
        except BaseException:
            try:
                self.rollback()
            except Exception:
                self.publish(faulted=True, maintenance=True)
                raise
            raise

    def run(self) -> None:
        existing = self.docker(
            "ps", "-a", "--filter", "name=^/" + self.name + "$", "--format", "{{.ID}}"
        )
        if existing and not self.owned(json.loads(self.docker("inspect", self.name))[0]):
            raise ValueError("Existing container belongs to another runtime")
        if not existing:
            self.start(self.baseline["filename"], self.baseline["alias"])
            for _ in range(180):
                try:
                    self.probe(self.baseline["alias"])
                    self.publish(active_provider=model_manager.BASELINE)
                    break
                except OSError, ValueError, KeyError:
                    time.sleep(1)
            else:
                raise ValueError("Model did not pass startup probe")
        # Recover a process interrupted while replacing the owned model container.
        self.rollback()
        active = self.value["active_provider"]
        alias = (
            self.baseline["alias"]
            if active == model_manager.BASELINE
            else active.removeprefix("local-")
        )
        self.probe(alias)
        with sqlite3.connect(self.db_path, timeout=30) as db:
            db.execute(
                "UPDATE model_manager_requests SET status='FAILED',"
                "error='Controller restarted; model recovered' WHERE status='RUNNING'"
            )
        self.publish(maintenance=False, faulted=False)
        done = threading.Event()

        def heartbeat() -> None:
            while not done.wait(3):
                self.publish()

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            while True:
                if self.value.get("faulted"):
                    time.sleep(1)
                    continue
                with sqlite3.connect(self.db_path, timeout=30) as db:
                    db.execute("BEGIN IMMEDIATE")
                    row = db.execute(
                        "SELECT id,preset FROM model_manager_requests "
                        "WHERE status='QUEUED' ORDER BY created LIMIT 1"
                    ).fetchone()
                    if row:
                        db.execute(
                            "UPDATE model_manager_requests SET status='RUNNING' WHERE id=?",
                            (row[0],),
                        )
                if not row:
                    time.sleep(1)
                    continue
                error = None
                try:
                    self.switch(*row)
                except Exception as exc:
                    error = str(exc)[:500]
                self.publish(maintenance=bool(self.value.get("faulted")))
                with sqlite3.connect(self.db_path, timeout=30) as db:
                    db.execute(
                        "UPDATE model_manager_requests SET status=?,error=? WHERE id=?",
                        ("FAILED" if error else "SUCCEEDED", error, row[0]),
                    )
        finally:
            done.set()
            thread.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-root", type=Path, required=True)
    args = parser.parse_args()
    with (args.web_root / "state/model-controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        Controller(args.web_root).run()
