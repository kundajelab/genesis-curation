"""Optional live inference telemetry; never exposes generated reasoning or partial metadata."""

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

listener: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar(
    "inference_progress", default=None
)


def report(**values: object) -> None:
    callback = listener.get()
    if callback:
        callback(values)
