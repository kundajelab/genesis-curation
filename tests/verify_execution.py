"""Exercise two real local workers, retry bounds, isolation and unknown-state safety."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

from genesis_curation.execution.controller import initialize, reconcile, resolve, rows, tick
from genesis_curation.execution.worker import inspect, launch


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="genesis-execution-") as temporary:
        root = Path(temporary)
        checkout = root / "repo"
        checkout.mkdir()
        subprocess.run(["git", "init", "-q", str(checkout)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(checkout),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "--allow-empty",
                "-qm",
                "fixture",
            ],
            check=True,
        )
        sha = subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
        ).strip()
        source = root / "input"
        source.write_text("known input\n")
        inputs = [
            {
                "path": str(source),
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        ]
        workers = {
            name: {
                "transport": "local",
                "slots": 1,
                "repo": str(checkout),
                "python": sys.executable,
                "root": str(root / name),
            }
            for name in ("a", "b")
        }
        jobs: list[dict[str, Any]] = []
        for name, worker, code in [
            ("healthy", "a", 0),
            ("invalid", "b", 2),
            ("temporary", "a", 75),
            ("badinput", "b", 0),
        ]:
            jobs.append(
                {
                    "id": name,
                    "worker": worker,
                    "payload": {
                        "git_sha": sha,
                        "inputs": [{"path": str(source), "sha256": "0" * 64}]
                        if name == "badinput"
                        else inputs,
                        "argv": [sys.executable, "-c", f"raise SystemExit({code})"],
                    },
                }
            )
        manifest = root / "plan.json"
        manifest.write_text(json.dumps({"schema_version": 1, "workers": workers, "jobs": jobs}))
        folder = root / "campaign"
        initialize(folder, manifest)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            tick(folder)
            status = {row["id"]: row for row in rows(folder)}
            if all(row["state"] in {"SUCCEEDED", "NEEDS_REVIEW"} for row in status.values()):
                break
            time.sleep(0.2)
        assert status["healthy"]["state"] == "SUCCEEDED", status
        assert status["invalid"]["state"] == "NEEDS_REVIEW", status
        assert status["temporary"]["state"] == "NEEDS_REVIEW", status
        assert status["temporary"]["attempt"] == 3, status
        assert status["invalid"]["attempt"] == 1, status
        assert status["badinput"]["reason"] == "Worker input checksum mismatch", status
        resolve(folder, "invalid", "Input investigation completed; retry requested")
        with patch("genesis_curation.execution.controller.request", side_effect=ConnectionError):
            tick(folder)
            tick(folder)
        unknown = next(row for row in rows(folder) if row["id"] == "invalid")
        assert unknown["state"] == "UNKNOWN" and unknown["attempt"] == 2
        try:
            resolve(folder, "invalid", "unsafe duplicate submission")
        except ValueError:
            pass
        else:
            raise AssertionError("UNKNOWN was resubmitted")
        with patch(
            "genesis_curation.execution.controller.request",
            return_value={"state": "RUNNING", "updated": time.time()},
        ):
            try:
                reconcile(folder, "invalid", "Inspected process", True)
            except ValueError:
                pass
            else:
                raise AssertionError("Active worker was cleared")
        with patch(
            "genesis_curation.execution.controller.request", return_value={"state": "ABSENT"}
        ):
            reconcile(
                folder, "invalid", "Operator verified no process or scheduler job remains", True
            )
        assert next(r for r in rows(folder) if r["id"] == "invalid")["state"] == "NEEDS_REVIEW"
        attempt = root / "idempotent"
        payload = {
            **jobs[0]["payload"],
            "repo": str(checkout),
            "python": sys.executable,
        }
        launch(attempt, payload)
        launch(attempt, payload)
        deadline = time.monotonic() + 10
        while (
            inspect(attempt)["state"] not in {"SUCCEEDED", "FAILED"} and time.monotonic() < deadline
        ):
            time.sleep(0.1)
        assert inspect(attempt)["state"] == "SUCCEEDED"
        try:
            launch(attempt, {**payload, "git_sha": "a" * 40})
        except ValueError:
            pass
        else:
            raise AssertionError("Attempt payload collision accepted")
        changed = json.loads(manifest.read_text())
        changed["jobs"][0]["payload"]["argv"] += ["changed"]
        manifest.write_text(json.dumps(changed))
        try:
            initialize(folder, manifest)
        except ValueError:
            pass
        else:
            raise AssertionError("Campaign identity changed")
    print("PASS: local worker isolation, bounded retries, recovery and immutable job identity")


if __name__ == "__main__":
    main()
