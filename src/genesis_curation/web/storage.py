"""Local data filesystem headroom; never automatically delete research data."""

import shutil
from pathlib import Path
from typing import Any


def status(root: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(root)
    return {
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "warning": usage.free < 6 * 1024**3,
        "blocked": usage.free < 2 * 1024**3,
        "paused": (root / "storage-paused").exists(),
    }


def require_space(root: Path) -> None:
    if status(root)["blocked"]:
        raise ValueError("Less than 2 GiB free. Free disk space before starting or resuming work.")


def paused(root: Path) -> bool:
    """Latch a low-space pause so recovery never silently restarts ingestion."""
    marker = root / "storage-paused"
    if status(root)["blocked"]:
        marker.touch(exist_ok=True)
    return marker.exists()


def resume(root: Path) -> None:
    require_space(root)
    (root / "storage-paused").unlink(missing_ok=True)
