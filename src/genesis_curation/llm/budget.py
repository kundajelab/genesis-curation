"""Token admission for complete requests, with explicit conservative fallback."""

from __future__ import annotations

import math
import time
from typing import Any
from urllib.request import ProxyHandler, Request, build_opener

from ..contracts.records import canonical, fingerprint, loads
from . import wire
from .security import credential, endpoint

_COUNTS: dict[str, tuple[int, str]] = {}
_CONTEXTS: dict[str, tuple[float, int]] = {}


def request(config: dict[str, Any], path: str, body: dict[str, Any] | None) -> dict[str, Any]:
    from .providers import NoRedirect, read_response

    base = endpoint(config["endpoint"], external=config["external"])
    if config.get("token_counter") == "llama" and base.endswith("/v1"):
        base = base[:-3]
    secret = credential(config["credential"])
    headers = {"Content-Type": "application/json"}
    if config.get("token_counter") == "anthropic-messages":
        headers.update({"x-api-key": secret, "anthropic-version": "2023-06-01"})
    elif secret:
        headers["Authorization"] = "Bearer " + secret
    timeout = min(30, config["timeout_seconds"])
    req = Request(
        base + path, data=canonical(body).encode() if body is not None else None, headers=headers
    )
    with build_opener(NoRedirect(), ProxyHandler({})).open(req, timeout=timeout) as response:
        result = loads(read_response(response, time.monotonic() + timeout).decode())
    if not isinstance(result, dict):
        raise ValueError("Invalid token counter response")
    return result


def context(config: dict[str, Any]) -> int:
    configured = config["context_window_tokens"]
    if config.get("token_counter") != "llama" or config["mode"] != "live":
        return configured
    key = fingerprint(config)
    cached = _CONTEXTS.get(key)
    if cached and time.monotonic() - cached[0] < 30:
        return min(configured, cached[1])
    # Never assume the configured window fits a local server whose capacity is unknown.
    props = request(config, "/props", None)
    actual = props.get("default_generation_settings", {}).get("n_ctx")
    if type(actual) is not int or actual <= 0:
        raise ValueError("Local server did not report its effective context window")
    _CONTEXTS[key] = (time.monotonic(), actual)
    return min(configured, actual)


def count(config: dict[str, Any], payload: dict[str, Any]) -> tuple[int, str]:
    key = fingerprint([config, payload])
    if key in _COUNTS:
        return _COUNTS[key]
    # UTF-8 bytes deliberately overestimate text tokens; include framing overhead.
    conservative = len(canonical(payload).encode()) + 256
    result = (conservative, "conservative-utf8")
    method = config.get("token_counter", "conservative")
    try:
        if config["mode"] == "live" and method == "llama":
            template = request(
                config,
                "/apply-template",
                {
                    "messages": payload["messages"],
                    "chat_template_kwargs": config.get("chat_template_kwargs", {}),
                },
            )
            tokens = request(
                config,
                "/tokenize",
                {
                    "content": template["prompt"],
                    "add_special": False,
                },
            )["tokens"]
            if not isinstance(tokens, list) or not all(type(t) is int for t in tokens):
                raise ValueError("Invalid tokenizer output")
            # Grammar is not prompt text in llama.cpp. The request already contains
            # schema text in messages when grammar support is disabled.
            result = (len(tokens), "llama-template")
        elif config["mode"] == "live" and method in {"openai-responses", "anthropic-messages"}:
            _, _, body = wire.request(config, payload, "")
            if method == "openai-responses":
                body = {k: v for k, v in body.items() if k not in {"store", "max_output_tokens"}}
                path = "/responses/input_tokens"
                extra = 0
            else:
                extra = len(canonical(body.get("output_config", {})).encode())
                body = {k: v for k, v in body.items() if k in {"model", "system", "messages"}}
                path = "/messages/count_tokens"
            measured = request(config, path, body)["input_tokens"]
            if type(measured) is not int or measured < 0:
                raise ValueError("Invalid token count")
            result = (measured + extra, method)
    except OSError, ValueError, KeyError, TypeError:
        pass  # No provider switch and no silent optimistic estimate.
    if len(_COUNTS) >= 2048:
        _COUNTS.clear()
    _COUNTS[key] = result
    return result


def inspect(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    window = context(config)
    incoming, method = count(config, payload)
    reserve = payload["max_tokens"]
    margin = max(512, math.ceil(window * 0.1))
    return {
        "input_tokens": incoming,
        "output_tokens_reserved": reserve,
        "context_window_tokens": window,
        "safety_tokens": margin,
        "counter": method,
        "fits": incoming <= config.get("max_input_tokens", window)
        and incoming + reserve + margin <= window,
    }


def require(config: dict[str, Any], payload: dict[str, Any]) -> None:
    if not inspect(config, payload)["fits"]:
        raise ValueError("Request exceeds configured token budget")
