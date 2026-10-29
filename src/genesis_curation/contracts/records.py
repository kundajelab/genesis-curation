"""Strict serialized contracts and content identities shared by curation commands."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator, validators
from jsonschema.protocols import Validator

VERSION = "genesis-curation/1"
MAX_DOCUMENT = 32 * 1024 * 1024
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
Result = Literal["PASS", "WARN", "ERROR", "NOT_EVALUATED", "NOT_APPLICABLE"]


def now() -> str:
    return datetime.now(UTC).isoformat()


def canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def identity(kind: str, *parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, canonical(["genesis", kind, *parts])))


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def identifier(value: str) -> str:
    if not ID.fullmatch(value) or value in {".", ".."}:
        raise ValueError(
            "Identifiers must contain only letters, digits, dots, underscores or dashes"
        )
    return value


def reject_constant(value: str) -> None:
    raise ValueError(f"Nonfinite JSON value: {value}")


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def loads(text: str) -> dict[str, Any]:
    value = json.loads(text, parse_constant=reject_constant, object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def load(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        data = stream.read(MAX_DOCUMENT + 1)
    if len(data) > MAX_DOCUMENT:
        raise ValueError("Document exceeds 32 MiB; split the import into source bundles")
    return loads(data.decode("utf-8"))


def dump(path: Path, value: object, *, immutable: bool = False) -> None:
    """Atomic publication; immutable paths never silently replace an earlier record."""
    text = canonical(value) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        if immutable:
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_text() != text:
                    raise ValueError(f"Immutable output already exists: {path}") from None
        else:
            temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@lru_cache
def schema(version: int = 1) -> dict[str, Any]:
    if version != 1:
        raise ValueError("Unsupported contract version")
    return loads(files(__package__).joinpath(f"schemas/records-v{version}.json").read_text())


@lru_cache
def validator(kind: str, version: int = 1) -> Validator:
    checker = Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda _checker, value: type(value) is int
    ).redefine(
        "number",
        lambda _checker, value: (
            type(value) in (int, float) and (type(value) is int or math.isfinite(value))
        ),
    )
    cls = validators.extend(Draft202012Validator, type_checker=checker)
    if kind not in schema(version)["$defs"]:
        raise ValueError("Unsupported record kind")
    return cls({"$defs": schema(version)["$defs"], "$ref": "#/$defs/" + kind})


def validate(value: object, kind: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int:
        raise ValueError("A versioned record object is required")
    if value["schema_version"] != 1 or (kind and value.get("kind") != kind):
        raise ValueError("Unsupported record version or kind")
    if not isinstance(value.get("kind"), str):
        raise ValueError("A record kind is required")
    errors = sorted(
        validator(value["kind"], value["schema_version"]).iter_errors(value),
        key=lambda e: str(e.json_path),
    )
    if errors:
        raise ValueError(f"Contract violation at {errors[0].json_path}: {errors[0].message}")

    def verify_content(item: object) -> None:
        if isinstance(item, dict):
            if set(item) == {"schema_version", "kind", "id", "version", "data"}:
                unsigned = {key: child for key, child in item.items() if key != "version"}
                if item["version"] != fingerprint(unsigned):
                    raise ValueError("Record content digest is stale")
                if (
                    item["kind"] == "source"
                    and hashlib.sha256(item["data"]["snapshot"].encode()).hexdigest()
                    != item["data"]["sha256"]
                ):
                    raise ValueError("Source snapshot digest is stale")
            for child in item.values():
                verify_content(child)
        elif isinstance(item, list):
            for child in item:
                verify_content(child)

    verify_content(value)
    return value


def record(kind: str, data: dict[str, Any], key: str, *, version: int = 1) -> dict[str, Any]:
    result = {"schema_version": version, "kind": kind, "id": key, "data": data}
    result["version"] = fingerprint(result)
    return validate(result, kind)
