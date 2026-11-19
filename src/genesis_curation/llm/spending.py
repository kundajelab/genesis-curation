"""Conservative, cross-process request reservations for optional USD ceilings.

Each request reserves its worst case (request bytes as input tokens, plus max_tokens) before
it is sent. When the provider answers and reports token usage, the reservation is settled to
that usage at configured prices, billing every input token (cached or not) at the full input
price. Failed or interrupted requests keep their worst-case reservation, since they may still
be billed. This is an estimated ceiling at configured prices, not an account billing balance.
"""

from __future__ import annotations

import fcntl
import math
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..contracts.records import canonical, fingerprint, load, now


def reported_cost(usage: dict[str, Any], prices: Mapping[str, float]) -> float | None:
    """Cost of one response from its reported usage (Anthropic or OpenAI field names)."""
    names = (
        ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"),
        ("prompt_tokens",),
    )
    outputs = ("output_tokens", "completion_tokens")
    output = next((usage[k] for k in outputs if isinstance(usage.get(k), int)), None)
    for group in names:
        if isinstance(usage.get(group[0]), int):
            tokens = sum(usage.get(k) or 0 for k in group)
            break
    else:
        return None
    if output is None:
        return None
    return (
        tokens * prices["input_per_million"] + output * prices["output_per_million"]
    ) / 1_000_000


def write(policy: Path, value: dict[str, Any]) -> None:
    temporary = policy.with_suffix(".new")
    with temporary.open("w") as stream:
        stream.write(canonical(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    temporary.replace(policy)


def settle(
    directory: Path, config: dict[str, Any], payload: dict[str, Any], usage: dict[str, Any]
) -> None:
    """Replace the newest unsettled reservation for this request with its reported cost."""
    policy = directory.parent / "spending.json"
    if not config["external"] or config["mode"] != "live" or not policy.exists():
        return
    prices = config.get("pricing")
    amount = reported_cost(usage, prices) if prices else None
    if amount is None:
        return
    request = fingerprint(payload)
    with (directory.parent / "spending.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = load(policy)
        for item in reversed(value.get("reservations", [])):
            if item.get("request_hash") == request and "settled_usd" not in item:
                if amount < item["reserved_usd"]:
                    value["reserved_usd"] = max(
                        0.0, value["reserved_usd"] - item["reserved_usd"] + amount
                    )
                    item["settled_usd"] = amount
                    write(policy, value)
                return


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
        write(policy, value)
