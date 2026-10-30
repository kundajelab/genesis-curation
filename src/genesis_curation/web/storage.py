"""Local data filesystem headroom; never automatically delete research data."""

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any


def status(root: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(root)
    filesystems: list[dict[str, Any]] = [
        {"name": "Local data", "free_bytes": usage.free, "total_bytes": usage.total}
    ]
    unavailable: list[str] = []
    if shared := os.environ.get("GENESIS_SHARED_ROOT"):
        path = Path(shared)
        try:
            if (
                not (path / ".genesis-storage").is_file()
                or path.stat().st_dev == root.stat().st_dev
            ):
                raise OSError("Shared mount unavailable")
            shared_usage = shutil.disk_usage(path)
            filesystems.append(
                {
                    "name": "Shared evidence",
                    "free_bytes": shared_usage.free,
                    "total_bytes": shared_usage.total,
                }
            )
        except OSError:
            unavailable.append("Shared evidence")
    return {
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "filesystems": filesystems,
        "unavailable": unavailable,
        "warning": bool(unavailable) or any(fs["free_bytes"] < 6 * 1024**3 for fs in filesystems),
        "blocked": bool(unavailable) or any(fs["free_bytes"] < 2 * 1024**3 for fs in filesystems),
        "paused": (root / "storage-paused").exists(),
    }


def require_space(root: Path) -> None:
    value = status(root)
    if value["unavailable"]:
        raise ValueError(
            "Shared evidence storage is unavailable; restore the mount before resuming"
        )
    if value["blocked"]:
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


_last_maintenance: dict[Path, float] = {}


def maintain(root: Path) -> None:
    """Only evict replaceable URL caches; never evidence, databases, or model files."""
    if time.monotonic() - _last_maintenance.get(root, -600) < 600:
        return
    _last_maintenance[root] = time.monotonic()
    for name in ("source-cache", "search-cache"):
        directory = root / name
        if not directory.exists():
            continue
        files = [
            (p.stat().st_mtime, p.stat().st_size, p)
            for p in directory.iterdir()
            if re.fullmatch(r"[a-f0-9]{64}\.json", p.name) and p.is_file() and not p.is_symlink()
        ]
        total = sum(size for _, size, _ in files)
        for stamp, size, path in sorted(files):
            if total <= 128 * 1024**2 and time.time() - stamp < 7 * 86400:
                break
            path.unlink(missing_ok=True)
            total -= size
