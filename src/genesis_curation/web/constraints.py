"""Explicit search requirements, assessed against supported fields rather than snippets."""

from __future__ import annotations

from typing import Any

from ..curation import harmonize
from . import assessments

FIELDS = {"tissue", "genotype", "treatment", "library_selection"}


def validate(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) - FIELDS:
        raise ValueError("Constraints support tissue, genotype, treatment and library_selection")
    result = {}
    for key, text in value.items():
        if not isinstance(text, str) or len(text) > 200:
            raise ValueError("Constraint values must be text of at most 200 characters")
        if text.strip():
            result[key] = text.strip()
    return result


def assess(state: dict[str, Any], requirements: dict[str, str]) -> dict[str, Any]:
    fields = {f["field"]: f for f in state["fields"]}
    checks = assessments.assess(state, state["fields"])
    result = {}
    for name, expected in requirements.items():
        field = fields.get(name, {})
        supported = field.get("status") == "known" and bool(field.get("evidence"))
        issue = checks.get(name, {})
        if issue.get("issues") or issue.get("needs_verification"):
            supported = bool(state.get("reviews", {}).get(name, {}).get("action") == "approve")
        decision = state.get("reviews", {}).get(name)
        if decision:
            supported = decision["action"] == "approve"
            if supported:
                field = decision["field"]
        terms = harmonize.vocabulary()["terms"].get(name, {})
        actual = str(field.get("value") or "").strip().casefold()
        wanted = expected.casefold()
        matches = actual == wanted or (
            actual in terms and wanted in terms and terms[actual]["id"] == terms[wanted]["id"]
        )
        result[name] = {
            "requested": expected,
            "value": field.get("value"),
            "status": "unresolved" if not supported else "supported" if matches else "contradicted",
            "evidence": field.get("evidence", []),
        }
    return result
