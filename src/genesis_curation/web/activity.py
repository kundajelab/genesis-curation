"""Small replaceable job telemetry, separate from immutable scientific history."""

from __future__ import annotations

import contextlib
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..contracts.records import dump, load
from ..llm import progress


@contextlib.contextmanager
def track(root: Path, task: dict[str, Any]) -> Iterator[None]:
    path = root / "progress" / (task["id"] + ".json")
    lock = threading.Lock()
    state: dict[str, Any] = {"phase": "Starting", "started": time.time(), "kind": task["kind"]}
    done = threading.Event()
    last_write = 0.0

    def update(values: dict[str, Any]) -> None:
        nonlocal last_write
        with lock:
            changed = values.get("phase") != state.get("phase") if "phase" in values else False
            state.update(values)
            stamp = time.time()
            if changed or stamp - last_write >= 1 or not values:
                state["heartbeat"] = stamp
                dump(path, state)
                last_write = stamp

    def heartbeat() -> None:
        while not done.wait(2):
            update({})

    token = progress.listener.set(update)
    update({})
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
        thread.join()
        progress.listener.reset(token)
        update({"phase": "Batch finished"})


def read(root: Path, key: str) -> dict[str, Any]:
    try:
        uuid.UUID(key)
        data = load(root / "progress" / (key + ".json"))
        return {
            **data,
            "elapsed_seconds": round(time.time() - data["started"], 1),
            "stale": time.time() - data["heartbeat"] > 10,
        }
    except ValueError, OSError, KeyError:
        return {}
