"""Conservative, cross-process request reservations for optional USD ceilings.

Reservations are never refunded: failed or interrupted requests may still be billed.
This is an estimated ceiling at configured prices, not an account billing balance.
"""

from __future__ import annotations

import fcntl
import math
import os
from pathlib import Path
from typing import Any

from ..contracts.records import canonical, fingerprint, load, now


def reserve(directory: Path, config: dict[str, Any], payload: dict[str, Any]) -> None:
    policy = directory.parent / "spending.json"
    if not config["external"] or config["mode"] != "live" or not policy.exists():
        return
    with (directory.parent / "spending.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = load(policy)
        limit = value.get("limit_usd")
        if (
            not isinstance(limit, (float, int))
            or isinstance(limit, bool)
            or not math.isfinite(limit)
            or limit <= 0
        ):
            raise ValueError("Invalid spending limit")
        prices = config.get("pricing")
        if not prices:
            raise ValueError("Budgeted API requests require configured input/output prices")
        # Complete serialized request bytes plus framing is deliberately conservative.
        incoming = len(canonical(payload).encode()) + 4096
        outgoing = payload["max_tokens"]
        amount = (
            incoming * prices["input_per_million"] + outgoing * prices["output_per_million"]
        ) / 1_000_000
        spent = value.get("reserved_usd", 0)
        if (
            not isinstance(spent, (float, int))
            or isinstance(spent, bool)
            or not math.isfinite(spent)
            or spent < 0
        ):
            raise ValueError("Invalid spending ledger")
        if spent + amount > limit:
            raise ValueError(
                "Estimated API spending limit reached; review the ledger before increasing it"
            )
        value["reserved_usd"] = spent + amount
        value.setdefault("reservations", []).append(
            {
                "time": now(),
                "provider": config["id"],
                "model": config["model"],
                "request_hash": fingerprint(payload),
                "input_bound": incoming,
                "output_bound": outgoing,
                "pricing": prices,
                "reserved_usd": amount,
            }
        )
        temporary = policy.with_suffix(".new")
        with temporary.open("w") as stream:
            stream.write(canonical(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        temporary.replace(policy)
