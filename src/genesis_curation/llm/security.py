"""Bounded data handling, local schemas and credential references."""

from __future__ import annotations

import os
import re
import stat
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..contracts.records import canonical, loads, validator

SECRET = re.compile(
    r"(?i)((?:authorization[\"']?\s*[:=]\s*(?:bearer\s+)?|(?:api[_-]?key|access[_-]?token|"
    r"password|secret|hf_token|openai_api_key)[\"']?\s*[:=]\s*)[\"']?)[^\s,;\"'}]+"
)
TOKEN = re.compile(r"\b(?:hf_|sk-)[A-Za-z0-9_-]{8,}\b")


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return TOKEN.sub("[REDACTED]", SECRET.sub(r"\1[REDACTED]", text))


def response_text(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Retain protocol evidence but discard provider-specific hidden-reasoning fields."""
    text = redact(text, secrets)
    hidden = {"reasoning", "reasoning_content", "analysis", "chain_of_thought"}

    def strip(value: object) -> object:
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k.casefold() not in hidden}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value

    try:
        return canonical(strip(loads(text)))
    except ValueError:
        # SSE events can carry reasoning deltas separately from answer content.
        lines = []
        for line in text.splitlines():
            if line.startswith("data:") and line[5:].strip() != "[DONE]":
                try:
                    line = "data: " + canonical(strip(loads(line[5:].strip())))
                except ValueError:
                    pass
            lines.append(line)
        return "\n".join(lines)


def schema(name: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9-]+", name):
        raise ValueError("Invalid packaged schema name")
    return loads(files(__package__).joinpath(f"schemas/{name}.json").read_text())


def check(value: object, specification: dict[str, Any]) -> None:
    """Reuse strict finite number/integer checking; never resolve remote schema URLs."""

    def references(node: object) -> None:
        if isinstance(node, dict):
            if "$ref" in node and not str(node["$ref"]).startswith("#/"):
                raise ValueError("Only document-local schema references are supported")
            for child in node.values():
                references(child)
        elif isinstance(node, list):
            for child in node:
                references(child)

    references(specification)
    canonical(value)
    checked = validator("source").evolve(schema=specification)
    errors = list(checked.iter_errors(value))
    if errors:
        # Do not echo untrusted values, credentials or entire responses into CLI errors.
        raise ValueError(f"Schema violation at {errors[0].json_path}: {errors[0].validator}")


def credential(reference: dict[str, Any] | None) -> str:
    if reference is None:
        return ""
    if bool(reference.get("env")) == bool(reference.get("file")):
        raise ValueError("Choose exactly one environment or protected-file credential reference")
    if reference.get("env"):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", reference["env"]):
            raise ValueError("Invalid credential environment variable name")
        value = os.environ.get(reference["env"], "")
    else:
        path = Path(reference["file"])
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Credential file must be an owned regular file with mode 0600")
        if info.st_size > 16384:
            raise ValueError("Credential file too large")
        value = path.read_text().strip()
    if not value or any(c in value for c in "\r\n\x00"):
        raise ValueError("Credential reference is unavailable or invalid")
    return value


def endpoint(url: str, *, external: bool) -> str:
    parts = urlsplit(url)
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Endpoint must not contain credentials, query parameters or fragments")
    if not parts.hostname or parts.scheme not in {"https", "http"}:
        raise ValueError("An HTTP(S) endpoint is required")
    if parts.scheme == "http" and parts.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("Plain HTTP is restricted to loopback; use TLS or an SSH tunnel")
    if external and parts.scheme != "https":
        raise ValueError("External providers require HTTPS")
    return url.rstrip("/")
