"""Resume the existing checksummed downloader after bounded transport failures."""

from __future__ import annotations

import argparse
import datetime
import time
from pathlib import Path

from genesis_curation.benchmark.acquisition import acquire
from genesis_curation.benchmark.spec import write_json


def retry(manifest: Path, out: Path, attempts: int, connections: int) -> Path:
    if not 1 <= attempts <= 16 or not 1 <= connections <= 4:
        raise ValueError("Use 1–16 attempts and 1–4 connections")
    out.mkdir(parents=True, exist_ok=True)
    receipt = out / (
        "transport-attempts-"
        + datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S")
        + ".json"
    )
    records = []
    for number in range(1, attempts + 1):
        start = time.monotonic()
        try:
            result = acquire(manifest, out, connections=connections)
        except (OSError, TimeoutError) as error:
            records.append(
                {
                    "attempt": number,
                    "status": "TRANSPORT_FAILURE",
                    "error_type": type(error).__name__,
                    "seconds": time.monotonic() - start,
                }
            )
            write_json(receipt, {"attempts": records})
            print("Transport attempt", number, "failed:", type(error).__name__, flush=True)
            if number == attempts:
                raise
            time.sleep(30)
        else:
            records.append(
                {"attempt": number, "status": "VERIFIED", "seconds": time.monotonic() - start}
            )
            write_json(receipt, {"attempts": records})
            return result
    raise RuntimeError("Unreachable retry state")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--attempts", type=int, default=8)
    parser.add_argument("--connections", type=int, default=2)
    args = parser.parse_args()
    print(retry(args.manifest, args.out, args.attempts, args.connections))
