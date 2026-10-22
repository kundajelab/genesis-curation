"""Explicit OpenAI-compatible inference with bounded, auditable failure handling."""

from __future__ import annotations

import contextlib
import fcntl
import threading
import time
import uuid
from collections.abc import Iterator
from http.client import HTTPResponse
from importlib.resources import files
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from ..contracts.records import canonical, dump, fingerprint, load, loads, now, record
from ..registry import store
from .security import check, credential, endpoint, redact, response_text, schema

MAX_RESPONSE = 1024 * 1024
PROMPT_VERSION = "genesis-evidence/1"
SYSTEM = files(__package__).joinpath("prompts/evidence-v1.txt").read_text().strip()


class ResponseStream(Protocol):
    def read1(self, size: int = -1, /) -> bytes: ...


def read_response(stream: ResponseStream, deadline: float) -> bytes:
    chunks = []
    count = 0
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError("Provider response deadline exceeded")
        chunk = stream.read1(min(65536, MAX_RESPONSE + 1 - count))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        count += len(chunk)
        if count > MAX_RESPONSE:
            raise ValueError("Provider response exceeds 1 MiB")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


class InferenceFailure(ValueError):
    def __init__(self, invocation: dict[str, Any]) -> None:
        self.invocation = invocation
        super().__init__(
            "Inference " + invocation["data"]["status"] + ": " + str(invocation["data"]["error"])
        )


def configurations(path: Path) -> list[dict[str, Any]]:
    value = load(path)
    check(value, schema("providers-v1"))
    configs = value["providers"]
    if len({p["id"] for p in configs}) != len(configs):
        raise ValueError("Duplicate provider IDs")
    for config in configs:
        if config["external"] and "local-only" in config["allowed_classifications"]:
            raise ValueError("External provider cannot allow local-only data")
        if config["endpoint"] is not None or config["mode"] == "live":
            endpoint(config["endpoint"] or "", external=config["external"])
    return configs


def select(path: Path, provider_id: str) -> dict[str, Any]:
    for config in configurations(path):
        if config["id"] == provider_id:
            return config
    raise ValueError("Unknown provider ID; no fallback is configured")


@contextlib.contextmanager
def admission(directory: Path, config: dict[str, Any]) -> Iterator[None]:
    """Cross-process local limit without holding a database transaction during inference."""
    folder = directory / "provider-locks" / fingerprint(config["id"])
    folder.mkdir(parents=True, exist_ok=True)
    handle = None
    for slot in range(config["concurrency"]):
        candidate = (folder / str(slot)).open("a")
        try:
            fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
            handle = candidate
            break
        except BlockingIOError:
            candidate.close()
    if handle is None:
        raise ValueError("Provider concurrency budget exhausted")
    try:
        if config["endpoint"] is not None:
            endpoint(config["endpoint"], external=config["external"])
        yield
    finally:
        handle.close()


def post(config: dict[str, Any], payload: dict[str, Any], secret: str) -> tuple[int, str]:
    deadline = time.monotonic() + config["timeout_seconds"]
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = "Bearer " + secret
    request = Request(
        endpoint(config["endpoint"], external=config["external"]) + "/chat/completions",
        data=canonical(payload).encode(),
        headers=headers,
        method="POST",
    )
    opener = build_opener(NoRedirect(), ProxyHandler({}))
    try:
        with opener.open(request, timeout=config["timeout_seconds"]) as response:
            raw = read_response(response, deadline)
            status = response.status
    except HTTPError as error:
        with error:
            if not isinstance(error.fp, HTTPResponse):
                raise ValueError("HTTP failure omitted its response stream") from None
            raw, status = read_response(error.fp, deadline), error.code
    if len(raw) > MAX_RESPONSE:
        raise ValueError("Provider response exceeds 1 MiB")
    return status, raw.decode("utf-8", errors="replace")


def parse_response(
    raw: str, model: str, specification: dict[str, Any]
) -> tuple[Any, dict[str, Any]]:
    wire = loads(raw)
    if wire.get("model") != model:
        raise ValueError("Endpoint returned a different model identifier")
    choices = wire.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("Expected exactly one response choice")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ValueError("Response choice must be an object")
    if choice.get("finish_reason") != "stop":
        raise ValueError("Response was truncated, refused or returned unsupported tool calls")
    message = choice.get("message", {})
    if not isinstance(message, dict):
        raise ValueError("Response message must be an object")
    if message.get("refusal"):
        raise ValueError("Provider refused the request")
    if not isinstance(message.get("content"), str):
        raise ValueError("Provider did not return text content")
    response = loads(message["content"])
    check(response, specification)
    usage = {key: None for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cost")}
    reported = wire.get("usage") or {}
    if not isinstance(reported, dict):
        raise ValueError("Usage must be an object or null")
    for key in usage:
        value = reported.get(key)
        if key != "cost" and type(value) is int and value >= 0:
            usage[key] = value
    return response, usage


def normalize_probe(raw: str, kind: str | None) -> str:
    """Normalize bounded capability probes; tool requests never execute a function."""
    if kind == "tools":
        wire = loads(raw)
        choices = wire.get("choices") or []
        if (
            not isinstance(choices, list)
            or len(choices) != 1
            or not isinstance(choices[0], dict)
            or choices[0].get("finish_reason") != "tool_calls"
        ):
            raise ValueError("Endpoint did not return the requested tool probe")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ValueError("Invalid tool probe message")
        calls = message.get("tool_calls") or []
        if not isinstance(calls, list) or any(
            not isinstance(c, dict) or not isinstance(c.get("function"), dict) for c in calls
        ):
            raise ValueError("Invalid tool probe calls")
        if len(calls) != 1 or calls[0].get("function", {}).get("name") != "genesis_probe":
            raise ValueError("Unsupported probe tool")
        wire["choices"] = [
            {"finish_reason": "stop", "message": {"content": calls[0]["function"]["arguments"]}}
        ]
        return canonical(wire)
    if kind == "streaming":
        chunks, finish, model, usage = [], None, None, None
        complete = False
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                complete = True
                break
            item = loads(data)
            if model and item.get("model") != model:
                raise ValueError("Streaming model identity changed")
            model = item.get("model")
            choices = item.get("choices", [])
            if not isinstance(choices, list) or len(choices) > 1:
                raise ValueError("Invalid streaming choices")
            for choice in choices:
                if not isinstance(choice, dict) or not isinstance(choice.get("delta"), dict):
                    raise ValueError("Invalid streaming delta")
                if choice["delta"].get("refusal"):
                    raise ValueError("Streaming provider refused the request")
                if choice.get("index", 0) != 0 or choice.get("delta", {}).get("tool_calls"):
                    raise ValueError("Unsupported streaming choice")
                chunks.append(choice.get("delta", {}).get("content") or "")
                finish = choice.get("finish_reason") or finish
            usage = item.get("usage") or usage
        if not complete:
            raise ValueError("Incomplete streaming response")
        return canonical(
            {
                "model": model,
                "choices": [{"finish_reason": finish, "message": {"content": "".join(chunks)}}],
                "usage": usage,
            }
        )
    return raw


def invoke(
    directory: Path,
    config: dict[str, Any],
    *,
    task: str,
    data: dict[str, Any],
    classification: str,
    source_hashes: list[str],
    specification: dict[str, Any],
    mock: dict[str, Any] | None = None,
    cancellation: threading.Event | None = None,
    dataset: str | None = None,
    cache: bool = False,
    probe_kind: str | None = None,
) -> dict[str, Any]:
    """Persist every attempted invocation; failures retain sanitized responses, never secrets."""
    check({"schema_version": 1, "providers": [config]}, schema("providers-v1"))
    check({}, {"type": "object", "$defs": {"output": specification}})
    if probe_kind not in {None, "tools", "streaming"} or probe_kind and task != "probe":
        raise ValueError("Capability probes are restricted to the probe task")
    started = time.monotonic()
    attempts: list[dict[str, Any]] = []
    usage_samples: list[dict[str, Any]] = []
    secret = ""
    failure: str | None = None
    response: Any = None
    usage = {key: None for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cost")}
    status = "FAILED"
    # The cache fingerprint includes source versions, model/config and all prompt/schema versions.
    key = fingerprint(
        {
            "config": config,
            "data": data,
            "task": task,
            "schema": specification,
            "prompt": [PROMPT_VERSION, fingerprint(SYSTEM)],
            "sources": source_hashes,
            "classification": classification,
            "probe_kind": probe_kind,
        }
    )
    cache_path = directory / "inference-cache" / (key + ".json")
    try:
        if (
            task not in config["allowed_tasks"]
            or classification not in config["allowed_classifications"]
        ):
            raise ValueError("Task or source classification is not authorized for this provider")
        if config["external"] and classification == "local-only":
            raise ValueError("Local-only data cannot leave the local trust boundary")
        if config["mode"] == "disabled":
            status = "DISABLED"
            raise ValueError("AI is disabled; use the manual workflow")
        if cancellation and cancellation.is_set():
            status = "CANCELLED"
            raise ValueError("Request cancelled")
        if cache and cache_path.exists():
            from ..contracts.records import validate

            cached = validate(load(cache_path), "invocation")
            if cached["data"]["request_hash"] != key or cached["data"]["status"] != "SUCCEEDED":
                raise ValueError("Invalid cached invocation")
            check(cached["data"]["response"], specification)
            return cached
        if config["mode"] == "live":
            secret = credential(config["credential"])
        sanitized = redact(canonical(data), (secret,))
        if len(sanitized) + len(SYSTEM) + len(canonical(specification)) > config["context_chars"]:
            raise ValueError("Input exceeds configured context character budget")
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": sanitized}]
        payload: dict[str, Any] = {
            "model": config["model"],
            "messages": messages,
            "temperature": 0,
            "max_tokens": config["max_tokens"],
            "stream": False,
        }
        if config["capabilities"]["json_schema"]:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "genesis_response",
                    "strict": True,
                    "schema": specification,
                },
            }
        else:
            messages[0]["content"] += " Required JSON schema: " + canonical(specification)
        if probe_kind == "tools":
            payload.pop("response_format", None)
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": "genesis_probe",
                        "description": "Return a probe result; never execute code",
                        "parameters": specification,
                    },
                }
            ]
            payload["tool_choice"] = {"type": "function", "function": {"name": "genesis_probe"}}
        if probe_kind == "streaming":
            payload["stream"] = True
        with admission(directory, config):
            if config["mode"] == "mock":
                response = loads(
                    redact(
                        canonical(
                            config["mock_response"] if config["mock_response"] is not None else mock
                        ),
                        (secret,),
                    )
                )
                check(response, specification)
                attempts.append({"status": "MOCK", "response": canonical(response), "error": None})
                status = "SUCCEEDED"
            else:
                retries = 0
                repaired = False
                while True:
                    if cancellation and cancellation.is_set():
                        status = "CANCELLED"
                        raise ValueError("Request cancelled")
                    try:
                        code, raw = post(config, payload, secret)
                    except OSError, URLError, TimeoutError:
                        code, raw = 0, ""
                    safe = response_text(raw, (secret,))
                    attempts.append({"status": str(code), "response": safe, "error": None})
                    try:
                        reported_usage = loads(normalize_probe(safe, probe_kind)).get("usage")
                        usage_samples.append(
                            reported_usage if isinstance(reported_usage, dict) else {}
                        )
                    except ValueError, TypeError, KeyError, IndexError:
                        usage_samples.append({})
                    if code == 0 or code == 429 or 500 <= code <= 599:
                        if retries < config["max_retries"]:
                            retries += 1
                            if cancellation:
                                cancellation.wait(min(2**retries / 10, 1))
                            else:
                                time.sleep(min(2**retries / 10, 1))
                            continue
                        raise ValueError("Provider unavailable after bounded retries")
                    if code != 200:
                        raise ValueError(
                            f"Provider HTTP {code}; endpoint capability/access requires review"
                        )
                    if cancellation and cancellation.is_set():
                        status = "CANCELLED"
                        raise ValueError("Request cancelled")
                    try:
                        response, usage = parse_response(
                            normalize_probe(safe, probe_kind), config["model"], specification
                        )
                    except ValueError, TypeError, KeyError, IndexError:
                        attempts[-1]["error"] = (
                            "Invalid, refused, truncated or incompatible response"
                        )
                        # Refusal and truncation do not authorize retries to evade a refusal.
                        if probe_kind:
                            raise ValueError("Capability probe failed") from None
                        wire = loads(safe)
                        candidates = wire.get("choices")
                        choice = (
                            candidates[0]
                            if isinstance(candidates, list)
                            and len(candidates) == 1
                            and isinstance(candidates[0], dict)
                            else {}
                        )
                        if (
                            config["format_repair"]
                            and not repaired
                            and wire.get("model") == config["model"]
                            and isinstance(choice.get("message"), dict)
                            and choice.get("finish_reason") == "stop"
                            and not choice.get("message", {}).get("refusal")
                        ):
                            repaired = True
                            messages.append(
                                {
                                    "role": "user",
                                    "content": "The response failed local JSON/schema "
                                    "validation. Return the original "
                                    "answer using the required schema. Do not invent "
                                    "additional evidence.",
                                }
                            )
                            if len(canonical(payload)) > config["context_chars"]:
                                raise ValueError("Format repair exceeds context budget") from None
                            continue
                        raise ValueError(attempts[-1]["error"]) from None
                    status = "SUCCEEDED"
                    break
    except (ValueError, OSError, KeyError, TypeError, KeyboardInterrupt) as error:
        # Errors produced above never contain source text; redact again at the audit boundary.
        failure = redact(str(error), (secret,))[:2048]
        if isinstance(error, KeyboardInterrupt):
            status, failure = "CANCELLED", "Interrupted by user"
    if usage_samples:
        for usage_key in usage:
            reported = [sample.get(usage_key) for sample in usage_samples]
            known = [
                v for v in reported if isinstance(v, int) and not isinstance(v, bool) and v >= 0
            ]
            usage[usage_key] = (
                sum(known) if usage_key != "cost" and len(known) == len(reported) else None
            )
    invocation = record(
        "invocation",
        {
            "provider_id": config["id"],
            "endpoint_ref": fingerprint(config["endpoint"]) if config["endpoint"] else None,
            "client_version": "genesis-llm/1",
            "model_id": config["model"],
            "model_revision": config["model_revision"],
            "serving_revision": config["serving_revision"],
            "task": task,
            "classification": classification,
            "source_hashes": source_hashes,
            "prompt_version": PROMPT_VERSION,
            "schema_hash": fingerprint(specification),
            "request_hash": key,
            "response": response if status == "SUCCEEDED" else None,
            "attempts": attempts,
            "status": status,
            "error": failure,
            "latency_seconds": time.monotonic() - started,
            "retry_count": max(0, len(attempts) - 1),
            "usage": usage,
            "timestamp": now(),
            "capabilities": config["capabilities"],
        },
        str(uuid.uuid4()),
        version=2,
    )
    with store.write(directory) as db:
        store.put(db, invocation, dataset)
    if status != "SUCCEEDED":
        raise InferenceFailure(invocation)
    if cache:
        dump(cache_path, invocation)
    return invocation


def probe(directory: Path, config: dict[str, Any]) -> dict[str, Any]:
    invocation = invoke(
        directory,
        config,
        task="probe",
        data={"instruction": "Return ok=true"},
        classification="public",
        source_hashes=[],
        specification=schema("probe-v1"),
        mock={"ok": True},
    )
    result = {
        "provider_id": config["id"],
        "model": config["model"],
        "config_hash": fingerprint(config),
        "checked": now(),
        "mode": config["mode"],
        "structured_response": "MOCK_PASS" if config["mode"] == "mock" else "PASS",
        "json_schema": ("MOCK_PASS" if config["mode"] == "mock" else "PASS")
        if config["capabilities"]["json_schema"]
        else "NOT_TESTED",
        "tools": "NOT_TESTED",
        "streaming": "NOT_TESTED",
        "invocation": invocation,
    }
    for kind in ("tools", "streaming"):
        if config["capabilities"][kind]:
            try:
                checked = invoke(
                    directory,
                    config,
                    task="probe",
                    data={"instruction": "Return ok=true"},
                    classification="public",
                    source_hashes=[],
                    specification=schema("probe-v1"),
                    mock={"ok": True},
                    probe_kind=kind,
                )
                result[kind] = "MOCK_PASS" if config["mode"] == "mock" else "PASS"
            except InferenceFailure as error:
                checked = error.invocation
                result[kind] = "FAIL"
            result[kind + "_invocation"] = checked
    snapshot = record(
        "source",
        {
            "location": "provider-probe:" + fingerprint(config),
            "worker": "controller",
            "sha256": fingerprint(result),
            "snapshot": canonical(result),
            "media_type": "application/json",
        },
        str(uuid.uuid4()),
    )
    with store.write(directory) as db:
        store.put(db, snapshot)
    result["capability_record"] = snapshot["version"]
    return result
