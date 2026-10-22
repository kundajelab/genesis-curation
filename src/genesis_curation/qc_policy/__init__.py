"""Declarative scientific assessment, separate from measurement and adjudication."""

from importlib.resources import files
from typing import Any

from ..contracts.records import loads, validate


def builtin(name: str) -> dict[str, Any]:
    kinds = {"plant-diagnostic-v1": "policy", "reviewed-catalog-v1": "profile"}
    if name not in kinds:
        raise ValueError("Unknown built-in policy/profile")
    value = loads(files(__package__).joinpath("policies", name + ".json").read_text())
    return validate(value, kinds[name])
